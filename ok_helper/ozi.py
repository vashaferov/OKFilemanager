"""Интеграция с OziExplorer 3.95: открытие файла в программе.

OziExplorer многовконный: ``oziexp.exe <файл>`` всегда поднимает новое
окно. Чтобы файл попал в **уже открытое** окно, нужен ``OziAPI.dll``,
который управляет OziExplorer через оконные сообщения.

``OziAPI.dll`` — 32-битная (как и сам OziExplorer), а программа работает
на 64-битном Python, который такую DLL загрузить не может
(``WinError 193``). Поэтому DLL загружает поставляемый с Windows
32-битный PowerShell (``%SystemRoot%\\SysWOW64\\WindowsPowerShell``), а
программа общается с ним через stdin/stdout; скрипт-мост —
``ok_helper/ozi_api_bridge.ps1``.

Алгоритм:

1. OziExplorer запущен и найдена OziAPI.dll — файл грузится в текущее
   окно (``oziLoadWPfile``, ``oziLoadTrackFile``, ``oziLoadMap``);
2. иначе — запуск ``oziexp.exe`` с файлом (откроется новое окно).

DLL ищется как готовый файл (заданный путь, папка нашей программы,
распакованная копия, папка ``oziexp.exe``). Комплектный архив
``assets/oziapi_dll.zip`` автоматически **не** распаковывается: это
делает пользователь кнопкой «Распаковать OziAPI» в настройках
(``extract_bundled_api``), пока её не нажали — DLL считается недоступной.

Формат файла определяет действие: ``.wpt`` — набор точек, ``.plt`` —
трек, ``.map`` — карта. Для ``.gpx`` загрузчика в API нет, поэтому такой
файл всегда открывается запуском exe.

Место загрузки выбирается: у OziExplorer 75 треков, и ``.plt`` грузится
в указанный номер (``oziLoadTrackFile``), иначе каждый следующий трек
затирал бы первый. Файл точек API умеет грузить только целиком — это
заменяет уже загруженный набор, поэтому точки можно и добавить по одной
(``oziCreateWPex``, разбор файла — в :mod:`ok_helper.wpt`).
"""

import atexit
import ctypes
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from ok_helper.wpt import read_wpt


# Форматы, которые имеет смысл открывать в OziExplorer.
OZI_FILE_SUFFIXES = {'.gpx', '.plt', '.wpt', '.map'}

# Страница загрузки OziAPI.dll (DLL не входит в комплект OziExplorer).
OZI_API_DOWNLOAD_URL = 'https://www.oziexplorer4.com/oziapi/oziapi.html'

# Имена процессов OziExplorer.
OZI_PROCESS_NAMES = ('oziexp.exe', 'oziexplorer.exe')

# Что умеет грузить API: расширение -> действие моста.
_API_ACTIONS = {'.wpt': 'wp', '.plt': 'track', '.map': 'map'}

# Сколько треков (и наборов точек) у OziExplorer.
OZI_TRACK_MAX = 75

# Что делать с уже загруженными точками при загрузке .wpt:
# 'append' — добавить к ним (по одной через oziCreateWPex),
# 'replace' — заменить набор целиком (oziLoadWPfile).
OZI_WPT_MODES = ('append', 'replace')
OZI_WPT_DEFAULT_MODE = 'append'

# Скрипт-мост рядом с этим модулем.
_BRIDGE_SCRIPT = Path(__file__).resolve().with_name('ozi_api_bridge.ps1')

# Сколько ждать готовности и ответа моста, секунд.
_BRIDGE_TIMEOUT = 20.0

_RESULT_RE = re.compile(
    r'^OZI_RESULT found=(-?\d+) rc=(-?\d+)(?P<extra>(?:\s+\w+=-?\d+)*)$')

# Дополнительные счётчики в ответе моста (например created=5).
_EXTRA_RE = re.compile(r'(\w+)=(-?\d+)')

# Типичные места установки oziexp.exe.
_CANDIDATE_EXES: List[str] = [
    r'C:\Program Files (x86)\OziExplorer\oziexp.exe',
    r'C:\Program Files\OziExplorer\oziexp.exe',
    r'C:\OziExplorer\oziexp.exe',
    r'C:\OziExplorer3\oziexp.exe',
    r'C:\OziExplorer4\oziexp.exe',
]


def _registry_exe_candidates() -> List[Path]:
    """Пути к oziexp.exe из реестра Windows (App Paths, Uninstall)."""
    if sys.platform != 'win32':
        return []
    try:
        import winreg  # type: ignore
    except ImportError:  # pragma: no cover
        return []

    found: List[Path] = []
    app_paths = [
        r'SOFTWARE\Microsoft\Windows\CurrentVersion'
        r'\App Paths\oziexp.exe',
        r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion'
        r'\App Paths\oziexp.exe',
    ]
    roots = [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]

    for root in roots:
        for key_path in app_paths:
            try:
                with winreg.OpenKey(root, key_path) as key:
                    value, _ = winreg.QueryValueEx(key, '')
            except OSError:
                continue
            if value:
                found.append(Path(value))

    # Записи об установке: 64-битные, 32-битные (WOW6432Node) и HKCU.
    uninstall_paths = [
        (winreg.HKEY_LOCAL_MACHINE,
         r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'),
        (winreg.HKEY_LOCAL_MACHINE,
         r'SOFTWARE\WOW6432Node\Microsoft\Windows'
         r'\CurrentVersion\Uninstall'),
        (winreg.HKEY_CURRENT_USER,
         r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'),
    ]
    for root, key_path in uninstall_paths:
        found.extend(_uninstall_exe_candidates(winreg, root, key_path))
    return found


def _uninstall_exe_candidates(winreg, root, key_path) -> List[Path]:
    """Exe-кандидаты из ветки Uninstall (по DisplayName или имени ключа).

    Имя ключа у Inno Setup обычно ``OziExplorer 3.95_is1``, но бывает и
    GUID — поэтому смотрим ещё и на DisplayName.
    """
    result: List[Path] = []
    try:
        with winreg.OpenKey(root, key_path) as key:
            count = winreg.QueryInfoKey(key)[0]
            for i in range(count):
                try:
                    name = winreg.EnumKey(key, i)
                except OSError:
                    continue
                try:
                    with winreg.OpenKey(key, name) as sub:
                        try:
                            display, _ = winreg.QueryValueEx(sub,
                                                             'DisplayName')
                        except OSError:
                            display = ''
                        try:
                            loc, _ = winreg.QueryValueEx(sub,
                                                         'InstallLocation')
                        except OSError:
                            loc = ''
                except OSError:
                    continue
                if 'oziexplorer' not in name.lower() and \
                        'oziexplorer' not in str(display).lower():
                    continue
                if loc:
                    for exe in ('oziexp.exe', 'OziExplorer.exe'):
                        result.append(Path(loc) / exe)
    except OSError:
        pass
    return result


def find_oziexplorer(configured: str = '') -> Optional[Path]:
    """Ищет oziexp.exe: заданный путь → PATH → типовые места → реестр."""
    if configured:
        p = Path(str(configured)).expanduser()
        if p.is_file():
            return p

    for name in ('oziexp', 'oziexp.exe'):
        which = shutil.which(name)
        if which:
            return Path(which)

    for cand in _CANDIDATE_EXES:
        p = Path(cand)
        if p.is_file():
            return p

    for p in _registry_exe_candidates():
        if p.is_file():
            return p

    return None


def _app_dirs() -> List[Path]:
    """Каталоги нашей программы, где ищем комплектные файлы."""
    bases: List[Path] = []
    if getattr(sys, 'frozen', False):
        bases.append(Path(sys.executable).resolve().parent)
    if sys.argv and sys.argv[0]:
        try:
            bases.append(Path(sys.argv[0]).resolve().parent)
        except OSError:  # pragma: no cover
            pass
    pkg = Path(__file__).resolve().parent
    bases.append(pkg)
    bases.append(pkg.parent)

    result: List[Path] = []
    seen = set()
    for base in bases:
        if base not in seen:
            seen.add(base)
            result.append(base)
    return result


def _bundled_api_candidates() -> List[Path]:
    """Где искать готовый OziAPI.dll рядом с нашей программой.

    OziAPI.dll загружает НАША программа, а не OziExplorer (в документации
    API: DLL должна лежать в папке программы, которая её использует, или
    на пути поиска Windows).
    """
    result: List[Path] = []
    seen = set()
    for base in _app_dirs():
        for cand in (base / 'OziAPI.dll',
                     base / 'lib' / 'OziAPI.dll',
                     base / 'assets' / 'ozi' / 'OziAPI.dll'):
            if cand not in seen:
                seen.add(cand)
                result.append(cand)
    return result


def _bundled_zip_candidates() -> List[Path]:
    """Где искать комплектный архив с OziAPI.dll."""
    result: List[Path] = []
    seen = set()
    for base in _app_dirs():
        for cand in (base / 'assets' / 'oziapi_dll.zip',
                     base / 'oziapi_dll.zip',
                     base / 'lib' / 'oziapi_dll.zip'):
            if cand not in seen:
                seen.add(cand)
                result.append(cand)
    return result


def find_ozi_api(configured: str = '',
                 exe: Optional[Path] = None) -> Optional[Path]:
    """Ищет OziAPI.dll.

    Порядок: заданный путь → рядом с нашей программой → распакованная
    кнопкой «Распаковать OziAPI» → рядом с ``oziexp.exe``.

    Комплектный архив сам по себе DLL не заменяет: пока пользователь не
    распаковал его (F10 → OziExplorer) или не указал путь, DLL считается
    недоступной и файл открывается запуском ``oziexp.exe``.
    """
    if configured:
        p = Path(str(configured)).expanduser()
        if p.is_file():
            return p

    candidates: List[Path] = list(_bundled_api_candidates())
    candidates.append(extracted_api_path())
    if exe is not None:
        candidates.append(exe.with_name('OziAPI.dll'))
    for p in candidates:
        if p.is_file():
            return p
    return None


def _api_cache_dir() -> Path:
    """Каталог, куда кнопкой распаковывается DLL из комплектного архива."""
    base = os.environ.get('LOCALAPPDATA')
    root = Path(base) if base else Path(tempfile.gettempdir())
    return root / 'ok-filemanager'


def extracted_api_path() -> Path:
    """Путь, по которому лежит распакованная кнопкой DLL."""
    return _api_cache_dir() / 'OziAPI.dll'


def bundled_api_zip() -> Optional[Path]:
    """Первый найденный комплектный архив с OziAPI.dll."""
    for zp in _bundled_zip_candidates():
        if zp.is_file():
            return zp
    return None


def extract_api_zip(zip_path, dest_dir) -> Path:
    """Распаковывает ``OziAPI.dll`` из архива в ``dest_dir``.

    Возвращает путь к распакованному файлу. Если он уже на месте и того
    же размера, распаковка не повторяется.
    """
    zip_path = Path(zip_path)
    dest_dir = Path(dest_dir)
    target = dest_dir / 'OziAPI.dll'
    with zipfile.ZipFile(zip_path) as zf:
        member = None
        for info in zf.infolist():
            if Path(info.filename).name.lower() == 'oziapi.dll':
                member = info
                break
        if member is None:
            raise OSError(f'в {zip_path.name} нет OziAPI.dll')
        if target.is_file() and target.stat().st_size == member.file_size:
            return target
        dest_dir.mkdir(parents=True, exist_ok=True)
        with zf.open(member) as src, open(target, 'wb') as dst:
            shutil.copyfileobj(src, dst)
    return target


def extract_bundled_api(zip_path=None, dest_dir=None) -> Path:
    """Распаковывает OziAPI.dll из комплектного архива.

    Вызывается кнопкой «Распаковать OziAPI» в настройках, а не
    автоматически. Возвращает путь к распакованной DLL.

    :raises OSError: если архива нет или в нём нет DLL.
    """
    zp = Path(zip_path) if zip_path is not None else bundled_api_zip()
    if zp is None:
        raise OSError('комплектный архив с OziAPI.dll не найден')
    target = Path(dest_dir) if dest_dir is not None else _api_cache_dir()
    return extract_api_zip(zp, target)


def find_ozi_powershell() -> Optional[Path]:
    """32-битный PowerShell из Windows — он умеет грузить 32-битные DLL."""
    if sys.platform != 'win32':
        return None
    root = os.environ.get('SystemRoot') or r'C:\Windows'
    p = (Path(root) / 'SysWOW64' / 'WindowsPowerShell' / 'v1.0'
         / 'powershell.exe')
    return p if p.is_file() else None


class _ProcessEntry32W(ctypes.Structure):
    """Структура PROCESSENTRY32W (toolhelp32)."""

    _fields_ = [
        ('dwSize', ctypes.c_ulong),
        ('cntUsage', ctypes.c_ulong),
        ('th32ProcessID', ctypes.c_ulong),
        ('th32DefaultHeapID', ctypes.POINTER(ctypes.c_ulong)),
        ('th32ModuleID', ctypes.c_ulong),
        ('cntThreads', ctypes.c_ulong),
        ('th32ParentProcessID', ctypes.c_ulong),
        ('pcPriClassBase', ctypes.c_long),
        ('dwFlags', ctypes.c_ulong),
        ('szExeFile', ctypes.c_wchar * 260),
    ]


def _iter_process_names() -> Iterator[str]:
    """Имена запущенных процессов (через toolhelp32).

    Быстрее и надёжнее, чем ``tasklist``: не зависит от локали и не
    запускает лишний процесс.
    """
    if sys.platform != 'win32':
        return
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong,
                                                  ctypes.c_ulong]
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p,
                                         ctypes.POINTER(_ProcessEntry32W)]
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p,
                                        ctypes.POINTER(_ProcessEntry32W)]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    th32cs_snapprocess = 0x00000002
    snap = kernel32.CreateToolhelp32Snapshot(th32cs_snapprocess, 0)
    if not snap or snap == ctypes.c_void_p(-1).value:
        return
    try:
        entry = _ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            yield entry.szExeFile
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)


def is_oziexplorer_running() -> bool:
    """Запущен ли OziExplorer (по именам процессов)."""
    if sys.platform != 'win32':
        return False
    try:
        return any(name.lower() in OZI_PROCESS_NAMES
                   for name in _iter_process_names())
    except OSError:  # pragma: no cover - сбой WinAPI
        return False


_EOF = object()


class OziApiBridge:
    """Мост к 32-битной OziAPI.dll через 32-битный PowerShell.

    Процесс держится живым: запуск стоит ~3 секунды (``Add-Type``
    компилирует P/Invoke-обёртку), а каждая команда после этого —
    доли секунды. Протокол описан в ``ozi_api_bridge.ps1``.

    Если процесс умер (например, OziExplorer перезапускали), следующая
    команда поднимет его заново.
    """

    def __init__(self, dll, powershell, script=_BRIDGE_SCRIPT,
                 timeout: float = _BRIDGE_TIMEOUT):
        self.dll = Path(dll)
        self.powershell = Path(powershell)
        self.script = Path(script)
        self.timeout = timeout
        self.version = ''
        self._proc: Optional[subprocess.Popen] = None
        self._lines: queue.Queue = queue.Queue()
        self._pump_thread: Optional[threading.Thread] = None

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> bool:
        """Запускает мост. True, если он ответил и готов."""
        if self.alive():
            return True
        self._lines = queue.Queue()
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        try:
            self._proc = subprocess.Popen(
                [str(self.powershell), '-NoProfile', '-NonInteractive',
                 '-ExecutionPolicy', 'Bypass', '-File', str(self.script),
                 '-Dll', str(self.dll)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
                errors='replace', creationflags=flags)
        except OSError:
            self._proc = None
            return False

        self._pump_thread = threading.Thread(target=self._pump, daemon=True)
        self._pump_thread.start()

        ready = self._next_line(self.timeout)
        if not isinstance(ready, str) or \
                not ready.startswith('OZI_BRIDGE_READY='):
            self.stop()
            return False
        self.version = ready.split('=', 1)[1].strip()
        return True

    def _pump(self):
        """Читает stdout моста в очередь (в фоне, чтобы был таймаут)."""
        proc = self._proc
        try:
            if proc is not None and proc.stdout is not None:
                for line in proc.stdout:
                    self._lines.put(line.rstrip('\r\n'))
        except (OSError, ValueError):  # pragma: no cover - поток закрыли
            pass
        finally:
            self._lines.put(_EOF)

    def _next_line(self, timeout: float):
        """Следующая строка, _EOF на закрытии, None на таймауте."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                return self._lines.get(timeout=remaining)
            except queue.Empty:
                return None

    def _request(self, command: str,
                 timeout: Optional[float] = None) -> Optional[str]:
        """Отправляет команду и ждёт строку протокола (``OZI_...``)."""
        if not self.start():
            return None
        proc = self._proc
        try:
            if proc is None or proc.stdin is None:
                return None
            proc.stdin.write(command + '\n')
            proc.stdin.flush()
        except (OSError, ValueError):
            self.stop()
            return None

        deadline = time.monotonic() + (timeout or self.timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            line = self._next_line(remaining)
            if line is _EOF:
                self.stop()
                return None
            if line is None:
                return None
            if line == 'OZI_PONG' or line.startswith('OZI_RESULT'):
                return line
            # прочие строки (предупреждения) пропускаем

    def _result(self, command: str) -> Optional[dict]:
        """Ответ моста словарём: ``found``, ``rc`` и счётчики команды."""
        line = self._request(command)
        if line is None:
            return None
        m = _RESULT_RE.match(line)
        if m is None:
            return None
        res = {'found': int(m.group(1)), 'rc': int(m.group(2))}
        for key, value in _EXTRA_RE.findall(m.group('extra')):
            res[key] = int(value)
        return res

    def _found_and_rc(self, command: str) -> Optional[Tuple[int, int]]:
        res = self._result(command)
        if res is None:
            return None
        return res['found'], res['rc']

    def is_running(self) -> bool:
        """Найден ли запущенный OziExplorer (``oziFindOzi``)."""
        res = self._found_and_rc('find')
        return res is not None and res[0] == 0

    def load(self, path, track_num: int = 1) -> bool:
        """Грузит файл в открытое окно. True при rc=0.

        Для трека ``track_num`` — номер трека (1..75); мост ещё включает
        показ треков и перерисовывает карту.
        """
        path = Path(path)
        action = _API_ACTIONS.get(path.suffix.lower())
        if action is None:
            return False
        res = self._found_and_rc(f'{action}\t{path}\t{int(track_num)}')
        return res == (0, 0)

    def add_waypoints(self, points) -> Optional[Tuple[int, int]]:
        """Добавляет точки к загруженным. (создано, не удалось) или None.

        ``oziLoadWPfile`` грузит файл целиком и заменяет набор, поэтому
        «добавить» можно только по точке (``oziCreateWPex``). Список
        уезжает в мост одной командой, чтобы не платить за круговой рейт
        на каждую точку; мост отвечает счётчиками (в том числе частичных
        отказов — файл может содержать точки, которые OziExplorer взял, и
        точки, которые он отверг).
        """
        payload = json.dumps([p.as_api() for p in points],
                             ensure_ascii=True, separators=(',', ':'))
        res = self._result('addwpts\t' + payload)
        if res is None or res['found'] != 0 or res['rc'] != 0:
            return None
        return int(res.get('created', 0)), int(res.get('failed', 0))

    def stop(self):
        """Останавливает мост (``quit``, при необходимости — kill)."""
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.poll() is None and proc.stdin is not None:
                proc.stdin.write('quit\n')
                proc.stdin.flush()
                proc.wait(timeout=3)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:  # pragma: no cover
                pass
        for stream in (proc.stdin, proc.stdout):
            try:
                if stream is not None:
                    stream.close()
            except OSError:  # pragma: no cover
                pass


_bridge: Optional[OziApiBridge] = None


def get_bridge(dll=None) -> Optional[OziApiBridge]:
    """Общий мост к OziAPI.dll (запускается при первом обращении)."""
    global _bridge
    if _bridge is not None and _bridge.alive():
        return _bridge
    powershell = find_ozi_powershell()
    if dll is None or powershell is None:
        return None
    bridge = OziApiBridge(dll, powershell)
    if not bridge.start():
        bridge.stop()
        return None
    _bridge = bridge
    return _bridge


def close_bridge():
    """Останавливает общий мост (вызывается при выходе программы)."""
    global _bridge
    if _bridge is not None:
        _bridge.stop()
        _bridge = None


atexit.register(close_bridge)


def _launch(exe_path: Optional[Path], path: Path) -> Tuple[bool, str]:
    if exe_path is None:
        return False, ('OziExplorer не найден. Укажите путь к oziexp.exe '
                       'в настройках (F10 → OziExplorer).')
    try:
        subprocess.Popen([str(exe_path), str(path)])
    except OSError as e:
        return False, f'не запустить OziExplorer: {e}'
    return True, f'открыто в OziExplorer: {path.name}'


def _api_ready(path, exe: str = '', dll: str = ''):
    """(действие API, путь к DLL), если файл пойдёт через API.

    Пусто — файл будет открыт запуском ``oziexp.exe`` (новое окно): нет
    DLL, OziExplorer не запущен или формат API не поддерживает.
    """
    action = _API_ACTIONS.get(Path(path).suffix.lower())
    if action is None:
        return '', None
    exe_path = find_oziexplorer(exe)
    dll_path = find_ozi_api(dll, exe_path)
    if dll_path is None or not is_oziexplorer_running():
        return '', None
    return action, dll_path


def api_plan(path, exe: str = '', dll: str = '') -> str:
    """Каким действием API откроется файл: ``wp`` / ``track`` / ``map``.

    Пустая строка — файл откроется запуском ``oziexp.exe`` в новом окне.
    Нужна до вызова :func:`open_in_oziexplorer`, чтобы спросить у
    пользователя место загрузки только тогда, когда оно вообще есть.
    """
    return _api_ready(path, exe, dll)[0]


def open_in_oziexplorer(path, exe: str = '', dll: str = '',
                        track_num: int = 1,
                        wpt_mode: str = OZI_WPT_DEFAULT_MODE
                        ) -> Tuple[bool, str]:
    """Открывает файл в OziExplorer. Возвращает (успех, сообщение).

    Если OziExplorer уже запущен и доступна OziAPI.dll, файл грузится в
    текущее окно; иначе запускается ``oziexp.exe`` (новое окно).

    ``track_num`` — номер трека для ``.plt`` (1..75). ``wpt_mode`` —
    что делать с точками при загрузке ``.wpt``: ``append`` добавляет их
    к загруженным, ``replace`` заменяет набор целиком.
    """
    path = Path(path)
    if not path.is_file():
        return False, f'файл не найден: {path.name}'

    action, dll_path = _api_ready(path, exe, dll)
    if action:
        bridge = get_bridge(dll_path)
        if bridge is not None:
            if action == 'wp' and wpt_mode == 'append':
                points = read_wpt(path)
                if not points:
                    return False, f'{path.name}: в файле нет точек'
                added = bridge.add_waypoints(points)
                if added is not None:
                    created, failed = added
                    msg = (f'{path.name} — добавлено точек в открытое '
                           f'окно OziExplorer: {created}')
                    if failed:
                        msg += f' (не удалось: {failed})'
                    return True, msg
                return False, ('не удалось добавить точки в OziExplorer; '
                               'попробуйте заменить набор точек: это делает '
                               'сам OziExplorer')
            if bridge.load(path, track_num):
                if action == 'track':
                    return True, (f'{path.name} — загружено в трек '
                                  f'{int(track_num)} открытого окна '
                                  f'OziExplorer')
                return True, (f'{path.name} — загружено в открытое окно '
                              f'OziExplorer')

    return _launch(find_oziexplorer(exe), path)
