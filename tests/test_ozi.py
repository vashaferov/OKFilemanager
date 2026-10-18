"""Тесты интеграции с OziExplorer (ok_helper.ozi).

Функциональность: поиск oziexp.exe и OziAPI.dll, обёртка над
OziAPI.dll (ctypes) и open_in_oziexplorer() — гибрид «API или запуск».

Реальный OziExplorer и сеть не используются: subprocess и WinDLL
подменяются, реестр и PATH изолируются.

Группировка в Allure:
  epic    = ok-filemanager
  feature = Интеграция с OziExplorer
  story   = задаётся маркером @pytest.mark.story на каждом классе.
"""

import sys
from pathlib import Path

import allure
import pytest

from ok_helper import ozi
from ok_helper.ozi import (
    OZI_API_DOWNLOAD_URL,
    OZI_FILE_SUFFIXES,
    OziApi,
    find_ozi_api,
    find_oziexplorer,
    open_in_oziexplorer,
)


pytestmark = [
    pytest.mark.ozi,
    pytest.mark.epic('ok-filemanager'),
    pytest.mark.feature('Интеграция с OziExplorer'),
]


@pytest.fixture
def no_auto(monkeypatch):
    """Отключает автоматический поиск exe (PATH, места установки, реестр)."""
    monkeypatch.setattr(ozi.shutil, 'which', lambda name: None)
    monkeypatch.setattr(ozi, '_CANDIDATE_EXES', [])
    monkeypatch.setattr(ozi, '_registry_exe_candidates', lambda: [])
    return None


def make_file(path: Path, text: str = '') -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


# ─────────────────────────────────────────────────────────────────
# 1. Поиск oziexp.exe
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Поиск oziexp.exe')
class TestFindExe:
    @pytest.mark.smoke
    def test_configured_path_wins(self, tmp_path):
        exe = make_file(tmp_path / 'oziexp.exe')

        assert find_oziexplorer(str(exe)) == exe

    def test_configured_missing_falls_through(self, tmp_path, no_auto):
        assert find_oziexplorer(str(tmp_path / 'nope.exe')) is None

    def test_nothing_found(self, no_auto):
        assert find_oziexplorer() is None

    def test_from_path(self, tmp_path, monkeypatch):
        exe = make_file(tmp_path / 'oziexp.exe')
        monkeypatch.setattr(ozi.shutil, 'which', lambda name: str(exe))
        monkeypatch.setattr(ozi, '_CANDIDATE_EXES', [])
        monkeypatch.setattr(ozi, '_registry_exe_candidates', lambda: [])

        assert find_oziexplorer() == exe

    def test_from_candidate_dir(self, tmp_path, monkeypatch):
        exe = make_file(tmp_path / 'oziexp.exe')
        monkeypatch.setattr(ozi.shutil, 'which', lambda name: None)
        monkeypatch.setattr(ozi, '_CANDIDATE_EXES', [str(exe)])
        monkeypatch.setattr(ozi, '_registry_exe_candidates', lambda: [])

        assert find_oziexplorer() == exe

    def test_from_registry(self, tmp_path, monkeypatch):
        exe = make_file(tmp_path / 'oziexp.exe')
        monkeypatch.setattr(ozi.shutil, 'which', lambda name: None)
        monkeypatch.setattr(ozi, '_CANDIDATE_EXES', [])
        monkeypatch.setattr(ozi, '_registry_exe_candidates',
                            lambda: [exe])

        assert find_oziexplorer() == exe

    def test_registry_candidates_is_list(self):
        # Отсутствие ключей реестра — не ошибка, а пустой список.
        assert isinstance(ozi._registry_exe_candidates(), list)


# ─── фейковый winreg: проверка ветки Uninstall без реального реестра ───

class FakeRegKey:
    def __init__(self, subs=None, values=None):
        self._subs = subs or {}
        self._values = values or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeWinreg:
    HKEY_LOCAL_MACHINE = 'HKLM'
    HKEY_CURRENT_USER = 'HKCU'

    def __init__(self, tree):
        self._tree = tree

    def OpenKey(self, root, path):
        base = root if isinstance(root, FakeRegKey) \
            else self._tree.get(root)
        if base is None:
            raise OSError(2, 'not found')
        node = base
        for part in path.split('\\'):
            if part not in node._subs:
                raise OSError(2, 'not found')
            node = node._subs[part]
        return node

    @staticmethod
    def QueryInfoKey(key):
        return (len(key._subs), 0, 0)

    @staticmethod
    def EnumKey(key, i):
        return list(key._subs)[i]

    @staticmethod
    def QueryValueEx(key, name):
        if name not in key._values:
            raise OSError(2, 'no value')
        return (key._values[name], 1)


def nest(names, leaf):
    """Оборачивает лист в цепочку вложенных ключей."""
    node = leaf
    for name in reversed(names):
        node = FakeRegKey(subs={name: node})
    return node


def uninstall_tree(subs):
    """Дерево реестра с веткой Uninstall под WOW6432Node (32-бит)."""
    return {
        'HKLM': nest(
            ['SOFTWARE', 'WOW6432Node', 'Microsoft', 'Windows',
             'CurrentVersion', 'Uninstall'],
            FakeRegKey(subs=subs)),
    }


@pytest.mark.story('Поиск oziexp.exe в реестре')
class TestRegistryCandidates:
    def test_32bit_entry_by_display_name(self, monkeypatch):
        """GUID-имя ключа: совпадение ищем по DisplayName."""
        tree = uninstall_tree({
            '{4A2B0D0E-0000-0000-0000-000000000000}': FakeRegKey(values={
                'DisplayName': 'OziExplorer 3.95',
                'InstallLocation': 'c:\\OziExplorer\\',
            }),
        })
        monkeypatch.setattr(ozi.sys, 'platform', 'win32')
        monkeypatch.setitem(sys.modules, 'winreg', FakeWinreg(tree))

        found = ozi._registry_exe_candidates()

        assert Path(r'c:\OziExplorer\oziexp.exe') in found
        assert Path(r'c:\OziExplorer\OziExplorer.exe') in found

    def test_entry_by_key_name_without_display(self, monkeypatch):
        tree = uninstall_tree({
            'OziExplorer_is1': FakeRegKey(values={
                'InstallLocation': 'c:\\OziExplorer\\',
            }),
        })
        monkeypatch.setattr(ozi.sys, 'platform', 'win32')
        monkeypatch.setitem(sys.modules, 'winreg', FakeWinreg(tree))

        found = ozi._registry_exe_candidates()

        assert Path(r'c:\OziExplorer\oziexp.exe') in found

    def test_unrelated_entries_ignored(self, monkeypatch):
        tree = uninstall_tree({
            'OziExplorer_is1': FakeRegKey(values={
                'DisplayName': 'OziExplorer 3.95',
                'InstallLocation': 'c:\\OziExplorer\\',
            }),
            '{OTHER}': FakeRegKey(values={
                'DisplayName': 'Some Other App',
                'InstallLocation': 'c:\\Other\\',
            }),
        })
        monkeypatch.setattr(ozi.sys, 'platform', 'win32')
        monkeypatch.setitem(sys.modules, 'winreg', FakeWinreg(tree))

        found = ozi._registry_exe_candidates()

        assert Path(r'c:\OziExplorer\oziexp.exe') in found
        assert Path(r'c:\Other\oziexp.exe') not in found


# ─────────────────────────────────────────────────────────────────
# 2. Поиск OziAPI.dll
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Поиск OziAPI.dll')
class TestFindApi:
    def test_configured(self, tmp_path):
        dll = make_file(tmp_path / 'OziAPI.dll')

        assert find_ozi_api(str(dll)) == dll

    def test_next_to_exe(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ozi, '_bundled_api_candidates', lambda: [])
        exe = make_file(tmp_path / 'oziexp.exe')
        dll = make_file(tmp_path / 'OziAPI.dll')

        assert find_ozi_api('', exe) == dll

    def test_bundled_dll_wins_over_exe_dir(self, tmp_path, monkeypatch):
        """DLL в комплекте программы важнее варианта рядом с oziexp.exe."""
        bundled = make_file(tmp_path / 'app' / 'OziAPI.dll')
        monkeypatch.setattr(ozi, '_bundled_api_candidates',
                            lambda: [bundled])
        exe_dir = tmp_path / 'ozi'
        exe_dir.mkdir()
        exe = make_file(exe_dir / 'oziexp.exe')
        make_file(exe_dir / 'OziAPI.dll')

        assert find_ozi_api('', exe) == bundled

    def test_none_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ozi, '_bundled_api_candidates', lambda: [])

        assert find_ozi_api() is None
        assert find_ozi_api('', tmp_path / 'oziexp.exe') is None

    def test_bundled_candidates_include_lib_and_assets(self):
        cands = ozi._bundled_api_candidates()

        assert cands, 'список мест для поставляемой DLL пуст'
        assert all(p.name == 'OziAPI.dll' for p in cands)
        assert any('lib' in p.parts for p in cands)
        assert any('ozi' in p.parts for p in cands)


@pytest.mark.story('Ссылка на загрузку OziAPI.dll')
class TestDownloadUrl:
    def test_is_https_oziexplorer(self):
        assert OZI_API_DOWNLOAD_URL.startswith('https://')
        assert 'oziexplorer' in OZI_API_DOWNLOAD_URL
        assert OZI_API_DOWNLOAD_URL.endswith('/oziapi.html')


# ─────────────────────────────────────────────────────────────────
# 3. Обёртка OziAPI.dll (ctypes)
# ─────────────────────────────────────────────────────────────────

class FakeFunc:
    """Подмена функции DLL: помнит argtypes/restype, пишет вызовы."""

    def __init__(self, result, calls, name):
        self.result = result
        self.calls = calls
        self.name = name
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        self.calls.append((self.name, args))
        return self.result


class FakeDll:
    """Подмена OziAPI.dll: атрибуты-функции с заданным кодом возврата."""

    def __init__(self, results=None):
        self._results = results or {}
        self.calls = []
        self.funcs = {}

    def __getattr__(self, name):
        funcs = self.__dict__.setdefault('funcs', {})
        if name not in funcs:
            funcs[name] = FakeFunc(self._results.get(name, 0),
                                   self.calls, name)
        return funcs[name]


@pytest.fixture
def fake_dll(monkeypatch):
    """Подменяет ctypes.WinDLL фабрикой FakeDll и отдаёт последний."""
    holder = {}

    def factory(path, *a, **kw):
        dll = FakeDll(holder.get('results'))
        holder['dll'] = dll
        return dll

    def configure(results):
        holder['results'] = results

    monkeypatch.setattr(ozi.ctypes, 'WinDLL', factory, raising=False)
    holder['configure'] = configure
    return holder


@pytest.mark.story('Обёртка OziAPI.dll')
class TestOziApi:
    def test_is_running_true(self, tmp_path, fake_dll):
        fake_dll['configure']({'oziFindOzi': 0})
        api = OziApi(tmp_path / 'OziAPI.dll')

        assert api.is_running() is True

    def test_is_running_false(self, tmp_path, fake_dll):
        fake_dll['configure']({'oziFindOzi': -1})
        api = OziApi(tmp_path / 'OziAPI.dll')

        assert api.is_running() is False

    def test_load_waypoints_calls_wpfile(self, tmp_path, fake_dll):
        fake_dll['configure']({'oziLoadWPfile': 0})
        api = OziApi(tmp_path / 'OziAPI.dll')

        ok = api.load_waypoints(tmp_path / 'points.wpt')

        assert ok is True
        names = [name for name, _ in fake_dll['dll'].calls]
        assert 'oziLoadWPfile' in names

    def test_load_track_passes_number(self, tmp_path, fake_dll):
        fake_dll['configure']({'oziLoadTrackFile': 0})
        api = OziApi(tmp_path / 'OziAPI.dll')

        assert api.load_track(3, tmp_path / 't.plt') is True
        calls = [c for c in fake_dll['dll'].calls
                 if c[0] == 'oziLoadTrackFile']
        assert calls[0][1][0] == 3

    def test_load_failure_returns_false(self, tmp_path, fake_dll):
        fake_dll['configure']({'oziLoadMap': 1})
        api = OziApi(tmp_path / 'OziAPI.dll')

        assert api.load_map(tmp_path / 'm.map') is False

    def test_close_is_silent(self, tmp_path, fake_dll):
        fake_dll['configure']({})
        api = OziApi(tmp_path / 'OziAPI.dll')

        api.close()  # не должно бросать


def test_encode_path_is_bytes():
    data = ozi._encode_path(Path('track.gpx'))

    assert isinstance(data, bytes)
    assert b'track.gpx' in data


# ─────────────────────────────────────────────────────────────────
# 4. _try_api_load: загрузка в уже запущенный OziExplorer
# ─────────────────────────────────────────────────────────────────

class FakeApi:
    """Подмена OziApi для проверки логики _try_api_load."""

    def __init__(self, running=True, results=None):
        self.running = running
        self.results = results or {}
        self.calls = []
        self.closed = False

    def is_running(self):
        return self.running

    def load_waypoints(self, path):
        self.calls.append(('load_waypoints', path))
        return self.results.get('load_waypoints', True)

    def load_track(self, num, path):
        self.calls.append(('load_track', num, path))
        return self.results.get('load_track', True)

    def load_map(self, path):
        self.calls.append(('load_map', path))
        return self.results.get('load_map', True)

    def close(self):
        self.closed = True


def api_factory_of(api):
    return lambda dll_path: api


@pytest.mark.story('Загрузка через OziAPI')
class TestTryApiLoad:
    def test_waypoints(self, tmp_path):
        p = make_file(tmp_path / 'points.wpt')
        api = FakeApi()

        msg = ozi._try_api_load(Path('OziAPI.dll'), p, 1,
                                api_factory=api_factory_of(api))

        assert msg is not None
        assert 'набор точек' in msg
        assert api.calls == [('load_waypoints', p)]
        assert api.closed is True

    def test_track_uses_number(self, tmp_path):
        p = make_file(tmp_path / 't.plt')
        api = FakeApi()

        msg = ozi._try_api_load(Path('OziAPI.dll'), p, 4,
                                api_factory=api_factory_of(api))

        assert msg is not None
        assert api.calls == [('load_track', 4, p)]

    def test_map(self, tmp_path):
        p = make_file(tmp_path / 'm.map')
        api = FakeApi()

        msg = ozi._try_api_load(Path('OziAPI.dll'), p, 1,
                                api_factory=api_factory_of(api))

        assert msg is not None
        assert api.calls == [('load_map', p)]

    def test_not_running(self, tmp_path):
        p = make_file(tmp_path / 'points.wpt')
        api = FakeApi(running=False)

        msg = ozi._try_api_load(Path('OziAPI.dll'), p, 1,
                                api_factory=api_factory_of(api))

        assert msg is None
        assert api.calls == []
        assert api.closed is False

    def test_load_failed(self, tmp_path):
        p = make_file(tmp_path / 'points.wpt')
        api = FakeApi(results={'load_waypoints': False})

        msg = ozi._try_api_load(Path('OziAPI.dll'), p, 1,
                                api_factory=api_factory_of(api))

        assert msg is None
        assert api.closed is True

    def test_unsupported_suffix_not_loaded(self, tmp_path):
        p = make_file(tmp_path / 'track.gpx')

        def boom(dll_path):
            raise AssertionError('API для .gpx вызываться не должен')

        msg = ozi._try_api_load(Path('OziAPI.dll'), p, 1, api_factory=boom)

        assert msg is None

    def test_dll_load_error(self, tmp_path):
        p = make_file(tmp_path / 'points.wpt')

        def boom(dll_path):
            raise OSError('нет dll')

        msg = ozi._try_api_load(Path('OziAPI.dll'), p, 1, api_factory=boom)

        assert msg is None


# ─────────────────────────────────────────────────────────────────
# 5. open_in_oziexplorer: гибрид «API или запуск»
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('open_in_oziexplorer')
class TestOpenInOziExplorer:
    def test_missing_file(self, tmp_path):
        ok, msg = open_in_oziexplorer(tmp_path / 'nope.gpx')

        assert ok is False
        assert 'не найден' in msg

    def test_launch_exe(self, tmp_path, monkeypatch):
        src = make_file(tmp_path / 'track.gpx')
        exe = tmp_path / 'oziexp.exe'
        launched = []
        monkeypatch.setattr(ozi, 'find_oziexplorer', lambda cfg='': exe)
        monkeypatch.setattr(ozi, 'find_ozi_api',
                            lambda cfg='', exe=None: None)
        monkeypatch.setattr(ozi.subprocess, 'Popen',
                            lambda args: launched.append(args))

        ok, msg = open_in_oziexplorer(src)

        assert ok is True
        assert 'открыто в OziExplorer' in msg
        assert launched == [[str(exe), str(src)]]

    def test_exe_not_found(self, tmp_path, monkeypatch):
        src = make_file(tmp_path / 'track.gpx')
        monkeypatch.setattr(ozi, 'find_oziexplorer', lambda cfg='': None)
        monkeypatch.setattr(ozi, 'find_ozi_api',
                            lambda cfg='', exe=None: None)

        ok, msg = open_in_oziexplorer(src)

        assert ok is False
        assert 'не найден' in msg

    def test_api_hit_skips_launch(self, tmp_path, monkeypatch):
        src = make_file(tmp_path / 'points.wpt')
        launched = []
        monkeypatch.setattr(ozi, 'find_oziexplorer',
                            lambda cfg='': tmp_path / 'oziexp.exe')
        monkeypatch.setattr(ozi, 'find_ozi_api',
                            lambda cfg='', exe=None: tmp_path / 'OziAPI.dll')
        monkeypatch.setattr(ozi, '_try_api_load',
                            lambda dll, p, t: 'загружено через OziAPI')
        monkeypatch.setattr(ozi.subprocess, 'Popen',
                            lambda args: launched.append(args))

        ok, msg = open_in_oziexplorer(src)

        assert ok is True
        assert msg == 'загружено через OziAPI'
        assert launched == []

    def test_api_miss_falls_back_to_launch(self, tmp_path, monkeypatch):
        src = make_file(tmp_path / 'points.wpt')
        exe = tmp_path / 'oziexp.exe'
        launched = []
        monkeypatch.setattr(ozi, 'find_oziexplorer', lambda cfg='': exe)
        monkeypatch.setattr(ozi, 'find_ozi_api',
                            lambda cfg='', exe=None: tmp_path / 'OziAPI.dll')
        monkeypatch.setattr(ozi, '_try_api_load', lambda dll, p, t: None)
        monkeypatch.setattr(ozi.subprocess, 'Popen',
                            lambda args: launched.append(args))

        ok, msg = open_in_oziexplorer(src)

        assert ok is True
        assert launched == [[str(exe), str(src)]]

    def test_popen_error_reported(self, tmp_path, monkeypatch):
        src = make_file(tmp_path / 'track.gpx')
        monkeypatch.setattr(ozi, 'find_oziexplorer',
                            lambda cfg='': tmp_path / 'oziexp.exe')
        monkeypatch.setattr(ozi, 'find_ozi_api',
                            lambda cfg='', exe=None: None)

        def boom(args):
            raise OSError('нет прав')

        monkeypatch.setattr(ozi.subprocess, 'Popen', boom)

        ok, msg = open_in_oziexplorer(src)

        assert ok is False
        assert 'не запустить' in msg


def test_supported_suffixes():
    assert OZI_FILE_SUFFIXES == {'.gpx', '.plt', '.wpt', '.map'}
