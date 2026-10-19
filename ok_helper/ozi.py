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

1. найдена OziAPI.dll — файл грузится в текущее окно
   (``oziLoadWPfile``, ``oziLoadTrackFile``, ``oziLoadMap``); если
   OziExplorer ещё не запущен, запускаем его сами и ждём, пока API
   начнёт отвечать (проверено: окно готово к командам не сразу, а
   секунд через восемь после старта — до этого загрузка не проходит);
2. DLL нет: ``.map`` открывается запуском ``oziexp.exe`` (карта
   грузится и так), а ``.plt``/``.wpt``/``.gpx``/``.kml`` отдать в
   открытое окно нечем — говорим об этом прямо.

DLL ищется как готовый файл (заданный путь, папка нашей программы,
распакованная копия, папка ``oziexp.exe``). Комплектный архив
``assets/oziapi_dll.zip`` автоматически **не** распаковывается: это
делает пользователь кнопкой «Распаковать OziAPI» в настройках
(``extract_bundled_api``), пока её не нажали — DLL считается недоступной.

Формат файла определяет действие: ``.wpt`` — набор точек, ``.plt`` —
трек, ``.map`` — карта. У GPX и KML своих загрузчиков в API нет (а
``oziLoadTrackFile`` на такой файл отвечает модальным «Invalid numeric
input.»), поэтому ``.gpx``/``.kml`` разбираем сами (:mod:`ok_helper.gpxkml`)
и отдаём OziExplorer обычными ``.plt``/``.wpt``.

Место загрузки выбирается: у OziExplorer 75 треков, и ``.plt`` грузится
в указанный номер (``oziLoadTrackFile``), иначе каждый следующий трек
затирал бы первый. Файл точек API умеет грузить только целиком — это
заменяет уже загруженный набор, поэтому точки можно и добавить по одной
(``oziCreateWPex``, разбор файла — в :mod:`ok_helper.wpt`).
"""

import atexit
import ctypes
import hashlib
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

from ok_helper.gpxkml import (Content, GpxKmlError, read_gpx_kml, write_plt,
                              write_wpt)
from ok_helper.wpt import read_wpt


# Форматы, которые имеет смысл открывать в OziExplorer.
OZI_FILE_SUFFIXES = {'.gpx', '.kml', '.plt', '.wpt', '.map'}

# Страница загрузки OziAPI.dll (DLL не входит в комплект OziExplorer).
OZI_API_DOWNLOAD_URL = 'https://www.oziexplorer4.com/oziapi/oziapi.html'

# Имена процессов OziExplorer.
OZI_PROCESS_NAMES = ('oziexp.exe', 'oziexplorer.exe')

# Что умеет грузить API: расширение -> действие моста.
_API_ACTIONS = {'.wpt': 'wp', '.plt': 'track', '.map': 'map'}

# Что API не умеет: такие файлы разбираем сами (ok_helper.gpxkml) и
# отдаём OziExplorer уже обычными .plt/.wpt.
OZI_PARSE_ACTION = 'gpxkml'
_PARSE_ACTIONS = {'.gpx': OZI_PARSE_ACTION, '.kml': OZI_PARSE_ACTION}

# Сколько треков (и наборов точек) у OziExplorer.
OZI_TRACK_MAX = 75

# Сколько ждать готовности запущенного OziExplorer, секунд. Замер на
# живом OziExplorer 3.95: оболочка появляется сразу, API отвечает
# «нашёл» секунды через семь, а команды начинают проходить секунд через
# восемь-девять; запас взят с большим избытком.
OZI_START_TIMEOUT = 60.0

# Шаг ожидания при запуске и повторах загрузки, секунд.
_OZI_POLL = 0.5

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


def clamp_track_num(value) -> int:
    """Номер трека в допустимых границах (1..75)."""
    try:
        num = int(value)
    except (TypeError, ValueError):
        num = 1
    return min(max(num, 1), OZI_TRACK_MAX)


def _api_action(path) -> str:
    """Действие для файла: ``track``/``wp``/``map``/``gpxkml``.

    Пустая строка — через API такой файл в открытое окно не отдать.
    """
    suffix = Path(path).suffix.lower()
    if suffix in _API_ACTIONS:
        return _API_ACTIONS[suffix]
    return _PARSE_ACTIONS.get(suffix, '')


def api_plan(path, exe: str = '', dll: str = '') -> str:
    """Каким действием файл попадёт в уже открытое окно OziExplorer.

    ``track`` — обычный ``.plt`` (важен номер трека), ``wp`` — ``.wpt``
    (важен режим точек), ``map`` — карта, ``gpxkml`` — ``.gpx``/``.kml``,
    которые разбираем сами (:mod:`ok_helper.gpxkml`).

    Пустая строка — либо для формата такого действия нет (``.map`` при
    этом всё равно откроется запуском ``oziexp.exe``), либо не нашлась
    ``OziAPI.dll``: без неё файл в открытое окно не отдать, и спрашивать
    место загрузки незачем. Нужна до :func:`open_in_oziexplorer`, чтобы
    спрашивать место только тогда, когда оно вообще есть.
    """
    action = _api_action(path)
    if not action:
        return ''
    if find_ozi_api(dll, find_oziexplorer(exe)) is None:
        return ''
    return action


def _wait_api(bridge: OziApiBridge, timeout: float, progress=None) -> bool:
    """Ждёт, пока запущенный OziExplorer начнёт отвечать API."""
    deadline = time.monotonic() + timeout
    while True:
        if bridge.is_running():
            return True
        if time.monotonic() >= deadline:
            return False
        if progress is not None:
            progress('Ждём, пока OziExplorer начнёт отвечать...')
        time.sleep(_OZI_POLL)


def _prepare_oziexplorer(exe_path: Optional[Path], dll_path,
                         progress=None
                         ) -> Tuple[Optional[OziApiBridge], bool, str]:
    """Мост к открытому OziExplorer; нет окна — запускаем и ждём.

    Возвращает (мост, только что запустили OziExplorer, сообщение об
    ошибке). Мост равен None, если поднять API не удалось.
    """
    started = False
    if not is_oziexplorer_running():
        if exe_path is None:
            return None, False, ('OziExplorer не запущен, а найти oziexp.exe '
                                 'не удалось. Укажите путь в настройках '
                                 '(F10 → OziExplorer).')
        try:
            subprocess.Popen([str(exe_path)])
        except OSError as e:
            return None, False, f'не запустить OziExplorer: {e}'
        started = True
        if progress is not None:
            progress(f'Запускаем OziExplorer ({exe_path.name})...')

    bridge = get_bridge(dll_path)
    if bridge is None:
        return None, started, ('не удалось поднять OziAPI.dll: нужен '
                               '32-битный PowerShell из Windows и сама DLL')
    if started and not _wait_api(bridge, OZI_START_TIMEOUT, progress):
        return None, started, (f'OziExplorer не ответил за '
                               f'{int(OZI_START_TIMEOUT)} с, файл не '
                               f'загружен')
    return bridge, started, ''


def _load_file(bridge: OziApiBridge, path, track_num: int,
               retry: bool = False, progress=None) -> bool:
    """Грузит файл в открытое окно, при необходимости повторяя попытки.

    Повторы нужны сразу после запуска OziExplorer: ``oziFindOzi``
    отвечает «нашёл» уже в первые секунды, но команды ещё не проходят —
    OziExplorer восстанавливает своё состояние. Неудачная попытка ничего
    не портит: файл просто не грузится.
    """
    deadline = time.monotonic() + (OZI_START_TIMEOUT if retry else 0.0)
    while True:
        if bridge.load(path, track_num):
            return True
        if time.monotonic() >= deadline:
            return False
        if progress is not None:
            progress('OziExplorer ещё не готов, повторяем загрузку...')
        time.sleep(_OZI_POLL)


def _add_points(bridge: OziApiBridge, points, retry: bool = False,
                progress=None) -> Optional[Tuple[int, int]]:
    """Добавляет точки. (создано, не удалось) или None при отказе."""
    deadline = time.monotonic() + (OZI_START_TIMEOUT if retry else 0.0)
    while True:
        added = bridge.add_waypoints(points)
        if added is not None:
            return added
        if time.monotonic() >= deadline:
            return None
        if progress is not None:
            progress('OziExplorer ещё не готов, повторяем...')
        time.sleep(_OZI_POLL)


def _import_dir() -> Path:
    """Каталог, куда пишем ``.plt``/``.wpt`` для загрузки в OziExplorer.

    Файлы не удаляем: OziExplorer запоминает имя файла трека (и при
    следующем запуске восстанавливает трек из него), поэтому удалённый
    файл оставил бы трек «битым».
    """
    d = _api_cache_dir() / 'import'
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:  # pragma: no cover - сбой файловой системы
        return Path(tempfile.gettempdir())
    return d


def _import_path(path: Path, suffix: str, index: int = 0) -> Path:
    """Имя файла для загрузки: имя исходника + хеш полного пути.

    Хеш нужен, чтобы одноимённые файлы из разных папок не затирали копии
    друг друга, а повторная загрузка того же файла — перезаписывала свои.
    """
    digest = hashlib.md5(str(path).lower().encode('utf-8')).hexdigest()[:8]
    tail = f'_{index}' if index else ''
    return _import_dir() / f'{path.stem}_{digest}{tail}{suffix}'


def _track_name(path: Path, index: int, total: int) -> str:
    """Имя трека в OziExplorer: имя файла, для нескольких — с номером.

    Имя файла пользователь уже даёт по своим правилам (дата и позывной),
    а имена внутри GPX/KML бывают служебными вида
    «Oct-16th-2025-10-53AM-Flight-Airdata».
    """
    return path.stem if total == 1 else f'{path.stem} {index + 1}'


def _partial(path: Path, done: List[str], problem: str) -> str:
    """Сообщение о неудаче: что успело загрузиться до неё."""
    text = f'{path.name}: {problem}'
    if done:
        text += '; успело загрузиться — ' + '; '.join(done)
    return text


def _tracks_summary(start: int, loaded: int, tracks) -> str:
    """Что уже легло в OziExplorer: слоты и число точек."""
    last = start + loaded - 1
    where = f'слот {start}' if last == start else f'слоты {start}..{last}'
    total = sum(track.count() for track in tracks[:loaded])
    return f'треки: {where} ({total} точек)'


def import_gpx_kml(path, bridge: OziApiBridge, track_num: int = 1,
                   wpt_mode: str = OZI_WPT_DEFAULT_MODE,
                   content: Optional[Content] = None,
                   retry: bool = False, progress=None) -> Tuple[bool, str]:
    """Грузит ``.gpx``/``.kml`` в открытое окно OziExplorer.

    Треки пишем во временные ``.plt`` и грузим ``oziLoadTrackFile`` в
    выбранный слот и следующие за ним, точки — во временный ``.wpt``
    (``oziLoadWPfile``, режим «заменить») либо создаём по одной
    (``oziCreateWPex``, режим «добавить»). ``content`` — уже разобранный
    файл (:class:`ok_helper.gpxkml.Content`), чтобы не разбирать дважды.
    """
    path = Path(path)
    if content is None:
        try:
            content = read_gpx_kml(path)
        except GpxKmlError as e:
            return False, str(e)

    tracks = [track for track in content.tracks if track.has_points()]
    points = list(content.waypoints)
    if not tracks and not points:
        return False, f'{path.name}: в файле нет ни треков, ни точек'

    start = clamp_track_num(track_num)
    if tracks and start + len(tracks) - 1 > OZI_TRACK_MAX:
        return False, (f'{path.name}: треков в файле {len(tracks)}, а у '
                       f'OziExplorer их всего {OZI_TRACK_MAX} — начиная с '
                       f'трека {start} они не помещаются, выберите номер '
                       f'поменьше')

    parts: List[str] = []
    for index, track in enumerate(tracks):
        target = _import_path(path, '.plt',
                              index + 1 if len(tracks) > 1 else 0)
        write_plt(track, target, _track_name(path, index, len(tracks)))
        slot = start + index
        if not _load_file(bridge, target, slot, retry, progress):
            return False, _partial(path, parts, f'трек не лёг в слот {slot}')
        # Что уже в OziExplorer — на случай отказа на следующем треке.
        parts = [_tracks_summary(start, index + 1, tracks)]

    if points:
        if wpt_mode == 'replace':
            target = _import_path(path, '.wpt')
            write_wpt(points, target)
            if not _load_file(bridge, target, 1, retry, progress):
                return False, _partial(
                    path, parts, f'не удалось заменить точки ({len(points)})')
            parts.append(f'точки: заменены ({len(points)})')
        else:
            added = _add_points(bridge, points, retry, progress)
            if added is None:
                return False, _partial(path, parts,
                                       'не удалось добавить точки')
            created, failed = added
            text = f'точки: добавлено {created}'
            if failed:
                text += f', не удалось {failed}'
            parts.append(text)

    return True, (f'{path.name} — загружено в открытое окно OziExplorer: '
                  + '; '.join(parts))


def open_in_oziexplorer(path, exe: str = '', dll: str = '',
                        track_num: int = 1,
                        wpt_mode: str = OZI_WPT_DEFAULT_MODE,
                        content: Optional[Content] = None,
                        progress=None) -> Tuple[bool, str]:
    """Открывает файл в OziExplorer. Возвращает (успех, сообщение).

    Если нашлась ``OziAPI.dll``, файл уходит в **уже открытое** окно (при
    необходимости OziExplorer запускается сам, и мы ждём готовности API).
    Без DLL ``.map`` открывается запуском ``oziexp.exe`` — карта грузится
    и так, — а остальные форматы отдать в окно нечем: об этом прямо
    сообщаем, потому что запуск exe их всё равно не загрузит.

    ``track_num`` — номер трека для ``.plt`` (1..75); с него начинаются и
    треки из ``.gpx``/``.kml``. ``wpt_mode`` — что делать с точками:
    ``append`` добавляет их к загруженным, ``replace`` заменяет набор
    целиком. ``content`` — разобранный ``.gpx``/``.kml``
    (:class:`ok_helper.gpxkml.Content`). ``progress`` — необязательный
    отклик (например, «запускаем OziExplorer»): запуск занимает секунды
    и в графической программе их надо показывать.
    """
    path = Path(path)
    if not path.is_file():
        return False, f'файл не найден: {path.name}'

    action = _api_action(path)
    exe_path = find_oziexplorer(exe)
    dll_path = find_ozi_api(dll, exe_path) if action else None

    if action and dll_path is not None:
        bridge, started, error = _prepare_oziexplorer(exe_path, dll_path,
                                                      progress)
        if bridge is None:
            return False, error
        if action == OZI_PARSE_ACTION:
            return import_gpx_kml(path, bridge, track_num=track_num,
                                  wpt_mode=wpt_mode, content=content,
                                  retry=started, progress=progress)
        if action == 'wp' and wpt_mode == 'append':
            points = read_wpt(path)
            if not points:
                return False, f'{path.name}: в файле нет точек'
            added = _add_points(bridge, points, started, progress)
            if added is not None:
                created, failed = added
                msg = (f'{path.name} — добавлено точек в открытое окно '
                       f'OziExplorer: {created}')
                if failed:
                    msg += f' (не удалось: {failed})'
                return True, msg
            return False, ('не удалось добавить точки в OziExplorer; '
                           'попробуйте заменить набор точек: это делает '
                           'сам OziExplorer')
        if _load_file(bridge, path, track_num, started, progress):
            if action == 'track':
                return True, (f'{path.name} — загружено в трек '
                              f'{clamp_track_num(track_num)} открытого окна '
                              f'OziExplorer')
            return True, (f'{path.name} — загружено в открытое окно '
                          f'OziExplorer')
        if action == 'map':
            # Карта грузится и простым запуском exe — не оставляем
            # пользователя без карты, если API не справился.
            return _launch(exe_path, path)
        return False, (f'{path.name}: OziExplorer не принял файл '
                       f'(мост вернул ошибку)')

    if path.suffix.lower() == '.map':
        return _launch(exe_path, path)

    return False, (f'{path.name}: без OziAPI.dll файл в уже открытое окно '
                   f'OziExplorer не загрузить — запуск oziexp.exe с файлом '
                   f'его не грузит. DLL лежит в комплектном архиве: '
                   f'F10 → OziExplorer → «Распаковать OziAPI».')
