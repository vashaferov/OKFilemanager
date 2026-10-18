"""Интеграция с OziExplorer 3.95: открытие файла в программе.

Поддерживаются два способа (гибрид):

  1. **OziAPI.dll** — если DLL доступна и OziExplorer уже запущен, файл
     грузится в текущее окно через API (``oziLoadWPfile``,
     ``oziLoadTrackFile``, ``oziLoadMap``). Так не открывается второе
     окно. Требует включённого OziAPI в OziExplorer (Configuration →
     OziAPI) и версии ≥ 3.90.4g.
  2. **Запуск oziexp.exe с файлом** — запасной, универсальный путь:
     ``oziexp.exe <файл>``. Работает без OziAPI, но OziExplorer
     многовконный: каждая команда поднимает **новое** окно (проверено
     на 3.95.6f — три запуска дали три процесса). Способы попасть в уже
     открытое окно без DLL нет: OziExplorer не поддерживает ни
     single-instance, ни DDE, а официальная справка допускает в
     командной строке только имя **карты**.

Формат файла определяет действие: ``.wpt`` — набор точек, ``.plt`` —
трек, ``.map`` — карта. Для ``.gpx`` API-загрузчика нет, поэтому такой
файл всегда открывается запуском exe.
"""

import ctypes
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional, Tuple


# Форматы, которые имеет смысл открывать в OziExplorer.
OZI_FILE_SUFFIXES = {'.gpx', '.plt', '.wpt', '.map'}

# Страница загрузки OziAPI.dll (DLL не входит в комплект OziExplorer).
OZI_API_DOWNLOAD_URL = 'https://www.oziexplorer4.com/oziapi/oziapi.html'

# Типичные места установки oziexp.exe.
_CANDIDATE_EXES: List[str] = [
    r'C:\Program Files (x86)\OziExplorer\oziexp.exe',
    r'C:\Program Files\OziExplorer\oziexp.exe',
    r'C:\OziExplorer\oziexp.exe',
    r'C:\OziExplorer3\oziexp.exe',
    r'C:\OziExplorer4\oziexp.exe',
]

# Что и как грузить через OziAPI: расширение → (описание, метод).
_API_LOADERS = {
    '.wpt': ('набор точек', 'load_waypoints'),
    '.plt': ('трек',        'load_track'),
    '.map': ('карта',       'load_map'),
}


def _encode_path(path) -> bytes:
    """ANSI-строка для pansichar-параметров OziAPI.

    На Windows используется системная кодировка (mbcs); вне Windows
    такого кодека нет, поэтому подстраховываемся UTF-8.
    """
    try:
        return str(path).encode('mbcs')
    except (LookupError, UnicodeEncodeError):
        return str(path).encode('utf-8')


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


def _bundled_api_candidates() -> List[Path]:
    """Где искать OziAPI.dll, поставляемый вместе с нашей программой.

    OziAPI.dll загружает НАША программа, а не OziExplorer (в документации
    API: DLL должна лежать в папке программы, которая её использует, или
    на пути поиска Windows). Поэтому DLL можно положить рядом с нашим
    exe или в ``lib/`` / ``assets/ozi/`` — тогда конечному пользователю не
    нужно ничего скачивать самостоятельно.
    """
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
        for cand in (base / 'OziAPI.dll',
                     base / 'lib' / 'OziAPI.dll',
                     base / 'assets' / 'ozi' / 'OziAPI.dll'):
            if cand not in seen:
                seen.add(cand)
                result.append(cand)
    return result


def find_ozi_api(configured: str = '',
                 exe: Optional[Path] = None) -> Optional[Path]:
    """Ищет OziAPI.dll.

    Порядок: заданный путь → поставляемая с программой DLL → рядом с
    ``oziexp.exe``.
    """
    if configured:
        p = Path(str(configured)).expanduser()
        if p.is_file():
            return p

    candidates: List[Path] = list(_bundled_api_candidates())
    if exe is not None:
        candidates.append(exe.with_name('OziAPI.dll'))
    for p in candidates:
        if p.is_file():
            return p
    return None


class OziApi:
    """Тонкая обёртка над OziAPI.dll (stdcall-функции)."""

    _SIGNATURES = {
        'oziFindOzi':      ([], ctypes.c_int),
        'oziAPIsetTimeout': ([ctypes.c_int], ctypes.c_int),
        'oziLoadWPfile':   ([ctypes.c_char_p], ctypes.c_int),
        'oziLoadTrackFile': ([ctypes.c_int, ctypes.c_char_p], ctypes.c_int),
        'oziLoadMap':      ([ctypes.c_char_p], ctypes.c_int),
        'oziCloseApi':     ([], ctypes.c_int),
    }

    def __init__(self, dll_path):
        win_dll = getattr(ctypes, 'WinDLL', None)
        if win_dll is None:
            raise OSError('OziAPI.dll доступен только в Windows')
        try:
            self._dll = win_dll(str(dll_path))
        except OSError as e:
            raise OSError(f'не загрузить OziAPI.dll: {e}')
        self._fn = {}
        for name, (argtypes, restype) in self._SIGNATURES.items():
            fn = getattr(self._dll, name)
            fn.argtypes = argtypes
            fn.restype = restype
            self._fn[name] = fn

    def is_running(self) -> bool:
        try:
            return self._fn['oziFindOzi']() == 0
        except Exception:
            return False

    def _call(self, name: str, *args) -> bool:
        try:
            self._fn['oziAPIsetTimeout'](1000)
        except Exception:
            pass
        try:
            return self._fn[name](*args) == 0
        except Exception:
            return False

    def load_waypoints(self, path) -> bool:
        return self._call('oziLoadWPfile', _encode_path(path))

    def load_track(self, track_num: int, path) -> bool:
        return self._call('oziLoadTrackFile', int(track_num),
                          _encode_path(path))

    def load_map(self, path) -> bool:
        return self._call('oziLoadMap', _encode_path(path))

    def close(self):
        try:
            self._fn['oziCloseApi']()
        except Exception:
            pass


def _try_api_load(dll_path: Path, path: Path, track_num: int,
                  api_factory: Callable = OziApi) -> Optional[str]:
    """Пробует загрузить файл в запущенный OziExplorer через OziAPI.

    Возвращает текст успеха или None, если API неприменим: OziExplorer
    не запущен, формату нет загрузчика, загрузка не удалась.
    """
    rule = _API_LOADERS.get(path.suffix.lower())
    if rule is None:
        return None
    try:
        api = api_factory(dll_path)
    except OSError:
        return None

    if not api.is_running():
        return None
    try:
        what, method = rule
        if method == 'load_track':
            ok = api.load_track(track_num, path)
        else:
            ok = getattr(api, method)(path)
    except Exception:
        ok = False
    finally:
        try:
            api.close()
        except Exception:
            pass
    if not ok:
        return None
    return f'загружено через OziAPI ({what}): {path.name}'


def _launch(exe_path: Optional[Path], path: Path) -> Tuple[bool, str]:
    if exe_path is None:
        return False, ('OziExplorer не найден. Укажите путь к oziexp.exe '
                       'в настройках (F10 → OziExplorer).')
    try:
        subprocess.Popen([str(exe_path), str(path)])
    except OSError as e:
        return False, f'не запустить OziExplorer: {e}'
    return True, f'открыто в OziExplorer: {path.name}'


def open_in_oziexplorer(path, exe: str = '', dll: str = '',
                        track_num: int = 1) -> Tuple[bool, str]:
    """Открывает файл в OziExplorer. Возвращает (успех, сообщение).

    Сначала пробует OziAPI (в уже запущенное окно), иначе запускает
    oziexp.exe с путём к файлу.
    """
    path = Path(path)
    if not path.is_file():
        return False, f'файл не найден: {path.name}'

    exe_path = find_oziexplorer(exe)
    dll_path = find_ozi_api(dll, exe_path)

    if dll_path is not None:
        loaded = _try_api_load(dll_path, path, track_num)
        if loaded is not None:
            return True, loaded

    return _launch(exe_path, path)
