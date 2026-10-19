"""Тесты интеграции с OziExplorer (ok_helper.ozi).

Функциональность: поиск oziexp.exe и OziAPI.dll, распаковка комплектного
архива с DLL, поиск 32-битного PowerShell, определение запущенного
OziExplorer, мост к OziAPI.dll и open_in_oziexplorer() — гибрид
«API или запуск».

Реальный OziExplorer, PowerShell и сеть не используются: subprocess и
WinAPI подменяются, реестр и PATH изолируются.

Группировка в Allure:
  epic    = ok-filemanager
  feature = Интеграция с OziExplorer
  story   = задаётся маркером @pytest.mark.story на каждом классе.
"""

import json
import queue
import sys
import zipfile
from pathlib import Path

import allure
import pytest

from ok_helper import ozi
from ok_helper.gpxkml import Content, Track, TrackPoint
from ok_helper.wpt import Waypoint
from ok_helper.ozi import (
    OZI_API_DOWNLOAD_URL,
    OZI_FILE_SUFFIXES,
    OZI_PARSE_ACTION,
    OZI_START_TIMEOUT,
    OZI_TRACK_MAX,
    OZI_WPT_DEFAULT_MODE,
    OZI_WPT_MODES,
    OziApiBridge,
    _import_dir,
    _import_path,
    _track_name,
    api_plan,
    clamp_track_num,
    extract_bundled_api,
    find_ozi_api,
    find_oziexplorer,
    import_gpx_kml,
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


@pytest.fixture(autouse=True)
def isolated_api_cache(tmp_path, monkeypatch):
    """Уводит распакованную DLL из %LOCALAPPDATA% в песочницу теста.

    Иначе тесты нашли бы реальную копию, распакованную на машине.
    """
    monkeypatch.setattr(ozi, '_api_cache_dir',
                        lambda: tmp_path / 'api-cache')


def make_file(path: Path, text: str = '') -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


# Два waypoint'а в реальном формате: cp1251, высота в футах, дата
# TDateTime. Второй без даты и с неизвестной высотой (-777).
WPT_SAMPLE = (
    'OziExplorer Waypoint File Version 1.1\r\n'
    'WGS 84\r\n'
    'Reserved 2\r\n'
    'garmin\r\n'
    '1,Дом,55.755800,37.617300,44927.5,1,1,5,0,2,0,1,0,328,-1,-1,-1,'
    '0,0,0,0,0,0\r\n'
    '2,Точка2,55.700000,37.700000,,3,1,5,0,2,0,1,0,-777,-1,-1,-1,'
    '0,0,0,0,0,0\r\n'
).encode('cp1251')


def make_zip(path: Path, members) -> Path:
    """Создаёт zip с заданными элементами (имя -> байты)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w') as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


# Минимальный GPX для проверок .gpx-ветки: трек из двух точек и точка.
GPX_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test">
  <wpt lat="57.483576" lon="60.990078"><name>A1</name></wpt>
  <trk>
    <name>Тестовый</name>
    <trkseg>
      <trkpt lat="60.581368" lon="60.154179"><ele>188.09</ele></trkpt>
      <trkpt lat="60.581400" lon="60.154200"><ele>189.0</ele></trkpt>
    </trkseg>
  </trk>
</gpx>
"""


@pytest.fixture(scope='module')
def bridge_script() -> str:
    """Текст скрипта-моста: он обязан быть ASCII (PowerShell 5.1)."""
    return ozi._BRIDGE_SCRIPT.read_text(encoding='ascii')


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


# ─────────────────────────────────────────────────────────────────
# 3. Комплектный архив с OziAPI.dll
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Комплектный архив OziAPI.dll')
class TestBundledZip:
    def test_zip_candidates_include_assets(self):
        cands = ozi._bundled_zip_candidates()

        assert cands
        assert all(p.name == 'oziapi_dll.zip' for p in cands)
        assert any('assets' in p.parts for p in cands)

    def test_real_project_zip_exists(self):
        """В комплекте проекта архив с DLL действительно есть."""
        found = [p for p in ozi._bundled_zip_candidates() if p.is_file()]
        assert found, 'assets/oziapi_dll.zip не найден'
        with zipfile.ZipFile(found[0]) as zf:
            names = [Path(n).name.lower() for n in zf.namelist()]
        assert 'oziapi.dll' in names

    def test_extract_real_zip(self, tmp_path):
        zp = make_zip(tmp_path / 'oziapi_dll.zip',
                      {'OziAPI.dll': b'MZ' + b'x' * 100})
        out = tmp_path / 'cache'

        dll = ozi.extract_api_zip(zp, out)

        assert dll == out / 'OziAPI.dll'
        assert dll.read_bytes() == b'MZ' + b'x' * 100

    def test_extract_from_subfolder(self, tmp_path):
        zp = make_zip(tmp_path / 'oziapi_dll.zip',
                      {'ozi/readme.txt': b'hi',
                       'ozi/OziAPI.dll': b'DLL'})
        out = tmp_path / 'cache'

        dll = ozi.extract_api_zip(zp, out)

        assert dll.read_bytes() == b'DLL'

    def test_extract_missing_dll_raises(self, tmp_path):
        zp = make_zip(tmp_path / 'oziapi_dll.zip', {'readme.txt': b'hi'})

        with pytest.raises(OSError, match='OziAPI'):
            ozi.extract_api_zip(zp, tmp_path / 'cache')

    def test_extract_skips_when_same_size(self, tmp_path, monkeypatch):
        zp = make_zip(tmp_path / 'oziapi_dll.zip', {'OziAPI.dll': b'1234'})
        out = tmp_path / 'cache'
        out.mkdir()
        target = out / 'OziAPI.dll'
        target.write_bytes(b'1234')
        called = []

        real_open = zipfile.ZipFile

        class Watcher(real_open):
            def open(self, *a, **kw):
                called.append(a)
                return super().open(*a, **kw)

        monkeypatch.setattr(ozi.zipfile, 'ZipFile', Watcher)

        dll = ozi.extract_api_zip(zp, out)

        assert dll == target
        assert called == []

    def test_extract_rewrites_when_size_differs(self, tmp_path):
        zp = make_zip(tmp_path / 'oziapi_dll.zip', {'OziAPI.dll': b'12345678'})
        out = tmp_path / 'cache'
        out.mkdir()
        (out / 'OziAPI.dll').write_bytes(b'old')

        dll = ozi.extract_api_zip(zp, out)

        assert dll.read_bytes() == b'12345678'


@pytest.mark.story('Распаковка OziAPI.dll')
class TestExtractBundledApi:
    def test_bundled_zip_none_when_absent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ozi, '_bundled_zip_candidates', lambda: [])

        assert ozi.bundled_api_zip() is None

    def test_bundled_zip_found(self, tmp_path, monkeypatch):
        zp = make_file(tmp_path / 'assets' / 'oziapi_dll.zip', 'zip')
        monkeypatch.setattr(ozi, '_bundled_zip_candidates', lambda: [zp])

        assert ozi.bundled_api_zip() == zp

    def test_extract_bundled_without_zip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ozi, '_bundled_zip_candidates', lambda: [])

        with pytest.raises(OSError, match='не найден'):
            extract_bundled_api()

    def test_extract_bundled_into_cache(self, tmp_path, monkeypatch):
        zp = make_zip(tmp_path / 'assets' / 'oziapi_dll.zip',
                      {'OziAPI.dll': b'DLL'})
        monkeypatch.setattr(ozi, '_bundled_zip_candidates', lambda: [zp])
        monkeypatch.setattr(ozi, '_api_cache_dir',
                            lambda: tmp_path / 'cache')

        dll = extract_bundled_api()

        assert dll == tmp_path / 'cache' / 'OziAPI.dll'
        assert dll.read_bytes() == b'DLL'

    def test_extract_bundled_respects_cache_dir(self, tmp_path, monkeypatch):
        zp = make_zip(tmp_path / 'assets' / 'oziapi_dll.zip',
                      {'OziAPI.dll': b'DLL'})
        monkeypatch.setattr(ozi, '_bundled_zip_candidates', lambda: [zp])

        dll = extract_bundled_api(dest_dir=tmp_path / 'other')

        assert dll == tmp_path / 'other' / 'OziAPI.dll'

    def test_extract_bundled_zip_without_dll_raises(self, tmp_path,
                                                    monkeypatch):
        zp = make_zip(tmp_path / 'assets' / 'oziapi_dll.zip',
                      {'readme.txt': b'hi'})
        monkeypatch.setattr(ozi, '_bundled_zip_candidates', lambda: [zp])

        with pytest.raises(OSError, match='OziAPI'):
            extract_bundled_api(dest_dir=tmp_path / 'cache')

    def test_extracted_copy_is_found_by_find_ozi_api(self, tmp_path,
                                                     monkeypatch):
        """После распаковки DLL находится обычным поиском."""
        zp = make_zip(tmp_path / 'assets' / 'oziapi_dll.zip',
                      {'OziAPI.dll': b'DLL'})
        cache = tmp_path / 'cache'
        monkeypatch.setattr(ozi, '_bundled_zip_candidates', lambda: [zp])
        monkeypatch.setattr(ozi, '_api_cache_dir', lambda: cache)
        monkeypatch.setattr(ozi, '_bundled_api_candidates', lambda: [])
        assert find_ozi_api() is None      # до распаковки DLL недоступна

        dll = extract_bundled_api()

        assert find_ozi_api() == dll
        assert find_ozi_api('', tmp_path / 'oziexp.exe') == dll

    def test_configured_path_wins_over_extracted(self, tmp_path, monkeypatch):
        cache = tmp_path / 'cache'
        make_file(cache / 'OziAPI.dll')
        own = make_file(tmp_path / 'own' / 'OziAPI.dll')
        monkeypatch.setattr(ozi, '_bundled_api_candidates', lambda: [])
        monkeypatch.setattr(ozi, '_api_cache_dir', lambda: cache)

        assert find_ozi_api(str(own)) == own

    def test_app_dir_wins_over_extracted(self, tmp_path, monkeypatch):
        cache = tmp_path / 'cache'
        make_file(cache / 'OziAPI.dll')
        near_app = make_file(tmp_path / 'app' / 'OziAPI.dll')
        monkeypatch.setattr(ozi, '_bundled_api_candidates',
                            lambda: [near_app])
        monkeypatch.setattr(ozi, '_api_cache_dir', lambda: cache)

        assert find_ozi_api() == near_app

    def test_extracted_wins_over_exe_dir(self, tmp_path, monkeypatch):
        cache = tmp_path / 'cache'
        extracted = make_file(cache / 'OziAPI.dll')
        exe_dir = tmp_path / 'ozi'
        exe_dir.mkdir()
        exe = make_file(exe_dir / 'oziexp.exe')
        make_file(exe_dir / 'OziAPI.dll')
        monkeypatch.setattr(ozi, '_bundled_api_candidates', lambda: [])
        monkeypatch.setattr(ozi, '_api_cache_dir', lambda: cache)

        assert find_ozi_api('', exe) == extracted


@pytest.mark.story('Ссылка на загрузку OziAPI.dll')
class TestDownloadUrl:
    def test_is_https_oziexplorer(self):
        assert OZI_API_DOWNLOAD_URL.startswith('https://')
        assert 'oziexplorer' in OZI_API_DOWNLOAD_URL
        assert OZI_API_DOWNLOAD_URL.endswith('/oziapi.html')


# ─────────────────────────────────────────────────────────────────
# 3б. Скрипт-мост: то, что нельзя поймать на подменённом процессе
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Скрипт-мост ozi_api_bridge.ps1')
class TestBridgeScript:
    """Проверки самого скрипта: мост исполняет PowerShell, не Python."""

    def test_is_ascii_only(self, bridge_script):
        """PowerShell 5.1 читает .ps1 как ANSI: кириллица сломала бы мост.

        Чтение с encoding='ascii' в фикстуре уже это гарантирует; тест
        оставлен, чтобы отказ был понятен по имени.
        """
        assert bridge_script.isascii()

    @pytest.mark.smoke
    def test_waypoints_iterated_with_foreach(self, bridge_script):
        """Список точек разбирается foreach, а не @(...).

        Проверено на живом PowerShell 5.1: ``@($json | ConvertFrom-Json)``
        даёт массив из ОДНОГО элемента (весь список), поэтому поля точки
        не читаются, и файл из нескольких точек не добавлялся вовсе.
        """
        code = [line for line in bridge_script.splitlines()
                if not line.lstrip().startswith('#')]

        assert 'foreach ($p in ($json | ConvertFrom-Json))' in bridge_script
        assert not [line for line in code
                    if '@($json | ConvertFrom-Json)' in line]

    def test_loads_track_into_given_slot(self, bridge_script):
        assert 'oziLoadTrackFile($track' in bridge_script

    def test_shows_tracks_and_refreshes(self, bridge_script):
        """Иначе загруженный в другой номер трек остаётся скрытым."""
        assert 'oziShowAllTracks()' in bridge_script
        assert 'oziShowTrack($track)' in bridge_script
        assert 'oziRefreshMap()' in bridge_script

    def test_declares_waypoint_api(self, bridge_script):
        assert 'oziCreateWPex' in bridge_script

    def test_declares_stdcall(self, bridge_script):
        assert bridge_script.count('CallingConvention.StdCall') >= 10

    def test_reports_counters(self, bridge_script):
        assert "' created={0} failed={1}'" in bridge_script


# ─────────────────────────────────────────────────────────────────
# 4. 32-битный PowerShell и поиск запущенного OziExplorer
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('32-битный PowerShell')
class TestFindPowershell:
    def test_found_on_windows(self):
        p = ozi.find_ozi_powershell()

        if sys.platform == 'win32':
            assert p is not None
            assert p.is_file()
            assert 'SysWOW64' in p.parts
        else:
            assert p is None

    def test_none_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ozi.sys, 'platform', 'win32')
        monkeypatch.setenv('SystemRoot', str(tmp_path))

        assert ozi.find_ozi_powershell() is None

    def test_none_on_non_windows(self, monkeypatch):
        monkeypatch.setattr(ozi.sys, 'platform', 'linux')

        assert ozi.find_ozi_powershell() is None


@pytest.mark.story('Поиск запущенного OziExplorer')
class TestIsOziExplorerRunning:
    def test_true_for_oziexp(self, monkeypatch):
        monkeypatch.setattr(ozi, '_iter_process_names',
                            lambda: iter(['explorer.exe', 'OziExp.exe']))

        assert ozi.is_oziexplorer_running() is True

    def test_true_for_oziexplorer_name(self, monkeypatch):
        monkeypatch.setattr(ozi, '_iter_process_names',
                            lambda: iter(['OziExplorer.exe']))

        assert ozi.is_oziexplorer_running() is True

    def test_false_when_absent(self, monkeypatch):
        monkeypatch.setattr(ozi, '_iter_process_names',
                            lambda: iter(['explorer.exe', 'python.exe']))

        assert ozi.is_oziexplorer_running() is False

    def test_false_on_non_windows(self, monkeypatch):
        monkeypatch.setattr(ozi.sys, 'platform', 'linux')

        assert ozi.is_oziexplorer_running() is False

    def test_api_failure_is_not_an_exception(self, monkeypatch):
        def boom():
            raise OSError('WinAPI упал')

        monkeypatch.setattr(ozi, '_iter_process_names', boom)

        assert ozi.is_oziexplorer_running() is False

    def test_iter_names_smoke(self):
        """Перечисление процессов работает (только под Windows)."""
        if sys.platform != 'win32':
            pytest.skip('только Windows')
        names = list(ozi._iter_process_names())

        assert names
        assert any(n.lower().endswith('.exe') for n in names)


# ─────────────────────────────────────────────────────────────────
# 5. Мост к OziAPI.dll (OziApiBridge)
# ─────────────────────────────────────────────────────────────────

class FakeStdout:
    """Подмена stdout моста: строки отдаются итератору по мере появления."""

    def __init__(self):
        self.q: 'queue.Queue' = queue.Queue()

    def push(self, line):
        self.q.put(line + '\n')

    def __iter__(self):
        while True:
            item = self.q.get()
            if item is None:
                return
            yield item

    def close(self):
        self.q.put(None)


class FakeStdin:
    """Подмена stdin моста: каждый write получает ответ от «скрипта»."""

    def __init__(self, proc):
        self.proc = proc
        self.closed = False

    def write(self, text):
        for line in text.splitlines():
            self.proc.written.append(line)
            proc = self.proc
            answer = proc.answer(line)
            if answer and not proc.stdout.closed:
                proc.stdout.push(answer)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class FakeProc:
    """Подмена subprocess.Popen для моста."""

    def __init__(self, ready='OZI_BRIDGE_READY=1.09', answer=None,
                 ready_line=True):
        self.stdout = FakeStdout()
        self.stdout.closed = False
        self.stdin = FakeStdin(self)
        self.stderr = None
        self.written = []
        self.returncode = None
        self.killed = False
        self._answer = answer
        if ready_line:
            self.stdout.push(ready)

    def answer(self, command):
        if command == 'quit':
            return None
        if self._answer is not None:
            return self._answer(command)
        if command == 'find':
            return 'OZI_RESULT found=0 rc=0'
        if command == 'ping':
            return 'OZI_PONG'
        return 'OZI_RESULT found=0 rc=0'

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        self.stdout.closed = True
        self.stdout.close()
        return 0

    def kill(self):
        self.killed = True
        self.returncode = -9
        self.stdout.closed = True
        self.stdout.close()


@pytest.fixture
def fake_popen(monkeypatch):
    """Подменяет subprocess.Popen и отдаёт созданные «процессы»."""
    holder = {}

    def factory(args, **kwargs):
        proc = FakeProc(answer=holder.get('answer'),
                        ready=holder.get('ready', 'OZI_BRIDGE_READY=1.09'),
                        ready_line=holder.get('ready_line', True))
        holder['proc'] = proc
        holder['args'] = args
        holder['kwargs'] = kwargs
        holder.setdefault('procs', []).append(proc)
        return proc

    monkeypatch.setattr(ozi.subprocess, 'Popen', factory)
    return holder


def make_bridge(tmp_path, **kw):
    return OziApiBridge(tmp_path / 'OziAPI.dll',
                        tmp_path / 'powershell.exe',
                        tmp_path / 'bridge.ps1', **kw)


@pytest.mark.story('Мост к OziAPI.dll')
class TestOziApiBridge:
    @pytest.mark.smoke
    def test_start_reads_version(self, tmp_path, fake_popen):
        bridge = make_bridge(tmp_path, timeout=1.0)

        assert bridge.start() is True
        assert bridge.version == '1.09'
        assert bridge.alive() is True

    def test_start_command_line(self, tmp_path, fake_popen):
        bridge = make_bridge(tmp_path, timeout=1.0)

        bridge.start()

        args = fake_popen['args']
        assert args[0] == str(tmp_path / 'powershell.exe')
        assert '-NoProfile' in args
        assert '-ExecutionPolicy' in args
        assert 'Bypass' in args
        assert '-File' in args
        assert str(tmp_path / 'bridge.ps1') in args
        assert '-Dll' in args
        assert str(tmp_path / 'OziAPI.dll') in args

    def test_start_no_window(self, tmp_path, fake_popen):
        bridge = make_bridge(tmp_path, timeout=1.0)

        bridge.start()

        assert fake_popen['kwargs'].get('creationflags') == \
            getattr(ozi.subprocess, 'CREATE_NO_WINDOW', 0)

    def test_start_without_ready_fails(self, tmp_path, fake_popen):
        fake_popen['ready'] = 'OZI_BRIDGE_ERROR=1'
        bridge = make_bridge(tmp_path, timeout=1.0)

        assert bridge.start() is False
        assert bridge.alive() is False

    def test_start_popen_error(self, tmp_path, monkeypatch):
        def boom(args, **kwargs):
            raise OSError('нет прав')

        monkeypatch.setattr(ozi.subprocess, 'Popen', boom)
        bridge = make_bridge(tmp_path, timeout=1.0)

        assert bridge.start() is False

    def test_is_running_true(self, tmp_path, fake_popen):
        bridge = make_bridge(tmp_path, timeout=1.0)

        assert bridge.is_running() is True
        assert 'find' in fake_popen['proc'].written

    def test_is_running_false(self, tmp_path, fake_popen):
        fake_popen['answer'] = lambda cmd: 'OZI_RESULT found=-1 rc=-1'
        bridge = make_bridge(tmp_path, timeout=1.0)

        assert bridge.is_running() is False

    def test_load_waypoints_command(self, tmp_path, fake_popen):
        bridge = make_bridge(tmp_path, timeout=1.0)
        p = make_file(tmp_path / 'points.wpt')

        assert bridge.load(p) is True
        assert fake_popen['proc'].written[-1] == f'wp\t{p}\t1'

    def test_load_track_passes_number(self, tmp_path, fake_popen):
        bridge = make_bridge(tmp_path, timeout=1.0)
        p = make_file(tmp_path / 'track.plt')

        assert bridge.load(p, 4) is True
        assert fake_popen['proc'].written[-1] == f'track\t{p}\t4'

    def test_load_map_command(self, tmp_path, fake_popen):
        bridge = make_bridge(tmp_path, timeout=1.0)
        p = make_file(tmp_path / 'map.map')

        assert bridge.load(p) is True
        assert fake_popen['proc'].written[-1] == f'map\t{p}\t1'

    def test_load_failure_returns_false(self, tmp_path, fake_popen):
        fake_popen['answer'] = lambda cmd: 'OZI_RESULT found=0 rc=1'
        bridge = make_bridge(tmp_path, timeout=1.0)
        p = make_file(tmp_path / 'points.wpt')

        assert bridge.load(p) is False

    def test_load_unsupported_suffix(self, tmp_path, fake_popen):
        bridge = make_bridge(tmp_path, timeout=1.0)
        p = make_file(tmp_path / 'track.gpx')

        assert bridge.load(p) is False
        # мост даже не запускается: формат не поддержан API
        assert 'proc' not in fake_popen

    def test_request_timeout_returns_none(self, tmp_path, fake_popen):
        fake_popen['answer'] = lambda cmd: None
        bridge = make_bridge(tmp_path, timeout=0.2)
        p = make_file(tmp_path / 'points.wpt')

        assert bridge.load(p) is False

    def test_closed_bridge_restarts(self, tmp_path, fake_popen):
        bridge = make_bridge(tmp_path, timeout=1.0)
        bridge.start()
        first = fake_popen['proc']
        first.wait()  # мост «умер»
        p = make_file(tmp_path / 'points.wpt')

        assert bridge.load(p) is True
        assert len(fake_popen['procs']) == 2

    # --- добавление точек (addwpts) ---

    def test_add_waypoints_command(self, tmp_path, fake_popen):
        fake_popen['answer'] = (
            lambda cmd: 'OZI_RESULT found=0 rc=0 created=3 failed=1')
        bridge = make_bridge(tmp_path, timeout=1.0)
        points = [Waypoint(name='Дом', lat=55.7, lon=37.6),
                  Waypoint(name='Точка', lat=55.8, lon=37.9)]

        assert bridge.add_waypoints(points) == (3, 1)
        line = fake_popen['proc'].written[-1]
        assert line.startswith('addwpts\t')
        assert '"name":"Дом"' not in line          # JSON в ASCII
        assert '\\u0414\\u043e\\u043c' in line

    def test_add_waypoints_payload_is_json_array(self, tmp_path, fake_popen):
        fake_popen['answer'] = lambda cmd: 'OZI_RESULT found=0 rc=0 created=2'
        bridge = make_bridge(tmp_path, timeout=1.0)
        points = [Waypoint(name='A', lat=1.0, lon=2.0, altitude_m=100.5),
                  Waypoint(name='B', lat=3.0, lon=4.0)]

        bridge.add_waypoints(points)

        data = json.loads(fake_popen['proc'].written[-1].split('\t', 1)[1])
        assert [p['name'] for p in data] == ['A', 'B']
        assert data[0]['lat'] == 1.0
        assert data[0]['altitude'] == 100.5
        # поля, которые API ждёт всегда, есть у каждой точки
        for key in ('symbol', 'date', 'description', 'fontSize',
                    'proximityRoute'):
            assert key in data[1]

    def test_add_waypoints_empty_list_not_sent(self, tmp_path, fake_popen):
        """Пустой список не гоняем: мост ответил бы rc=-4."""
        bridge = make_bridge(tmp_path, timeout=1.0)

        assert bridge.add_waypoints([]) == (0, 0)
        # запрос всё же уходит одним пакетом с пустым массивом
        assert fake_popen['proc'].written[-1] == 'addwpts\t[]'

    def test_add_waypoints_nothing_created(self, tmp_path, fake_popen):
        fake_popen['answer'] = (
            lambda cmd: 'OZI_RESULT found=0 rc=-4 created=0 failed=5')
        bridge = make_bridge(tmp_path, timeout=1.0)

        assert bridge.add_waypoints([Waypoint(name='A')]) is None

    def test_add_waypoints_not_running(self, tmp_path, fake_popen):
        fake_popen['answer'] = lambda cmd: 'OZI_RESULT found=-1 rc=-1'
        bridge = make_bridge(tmp_path, timeout=1.0)

        assert bridge.add_waypoints([Waypoint(name='A')]) is None

    def test_add_waypoints_timeout(self, tmp_path, fake_popen):
        fake_popen['answer'] = lambda cmd: None
        bridge = make_bridge(tmp_path, timeout=0.2)

        assert bridge.add_waypoints([Waypoint(name='A')]) is None

    def test_result_parses_extras(self, tmp_path, fake_popen):
        fake_popen['answer'] = (
            lambda cmd: 'OZI_RESULT found=0 rc=0 created=7 failed=2')
        bridge = make_bridge(tmp_path, timeout=1.0)

        assert bridge._result('addwpts\t[]') == {
            'found': 0, 'rc': 0, 'created': 7, 'failed': 2}

    def test_result_without_extras(self, tmp_path, fake_popen):
        bridge = make_bridge(tmp_path, timeout=1.0)

        assert bridge._result('find') == {'found': 0, 'rc': 0}

    def test_result_garbage_line(self, tmp_path, fake_popen):
        fake_popen['answer'] = lambda cmd: 'OZI_RESULT какой-то мусор'
        bridge = make_bridge(tmp_path, timeout=1.0)

        assert bridge._result('find') is None

    def test_stop_sends_quit(self, tmp_path, fake_popen):
        bridge = make_bridge(tmp_path, timeout=1.0)
        bridge.start()

        bridge.stop()

        assert fake_popen['proc'].written[-1] == 'quit'
        assert bridge.alive() is False

    def test_stop_is_idempotent(self, tmp_path, fake_popen):
        bridge = make_bridge(tmp_path, timeout=1.0)

        bridge.stop()  # не должно бросать
        bridge.stop()

        assert bridge.alive() is False

    def test_stop_kills_stuck_process(self, tmp_path, fake_popen):
        bridge = make_bridge(tmp_path, timeout=1.0)
        bridge.start()
        proc = fake_popen['proc']

        def stuck(timeout=None):
            raise ozi.subprocess.TimeoutExpired('powershell', timeout)

        proc.wait = stuck
        bridge.stop()

        assert proc.killed is True

    def test_alive_false_before_start(self, tmp_path):
        assert make_bridge(tmp_path).alive() is False


@pytest.mark.story('Общий мост')
class TestGetBridge:
    def test_returns_none_without_powershell(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ozi, 'find_ozi_powershell', lambda: None)

        assert ozi.get_bridge(tmp_path / 'OziAPI.dll') is None

    def test_returns_none_without_dll(self, monkeypatch):
        monkeypatch.setattr(ozi, 'find_ozi_powershell',
                            lambda: Path('ps.exe'))

        assert ozi.get_bridge(None) is None

    def test_starts_and_caches(self, tmp_path, monkeypatch, fake_popen):
        monkeypatch.setattr(ozi, 'find_ozi_powershell',
                            lambda: tmp_path / 'powershell.exe')
        monkeypatch.setattr(ozi, '_bridge', None)
        dll = make_file(tmp_path / 'OziAPI.dll')

        first = ozi.get_bridge(dll)
        second = ozi.get_bridge(dll)

        assert first is not None
        assert first is second
        assert len(fake_popen['procs']) == 1

    def test_dead_bridge_is_replaced(self, tmp_path, monkeypatch,
                                    fake_popen):
        monkeypatch.setattr(ozi, 'find_ozi_powershell',
                            lambda: tmp_path / 'powershell.exe')
        monkeypatch.setattr(ozi, '_bridge', None)
        dll = make_file(tmp_path / 'OziAPI.dll')

        first = ozi.get_bridge(dll)
        fake_popen['proc'].wait()
        second = ozi.get_bridge(dll)

        assert second is not first
        assert len(fake_popen['procs']) == 2

    def test_close_bridge_stops_process(self, tmp_path, monkeypatch,
                                        fake_popen):
        monkeypatch.setattr(ozi, 'find_ozi_powershell',
                            lambda: tmp_path / 'powershell.exe')
        monkeypatch.setattr(ozi, '_bridge', None)
        dll = make_file(tmp_path / 'OziAPI.dll')
        ozi.get_bridge(dll)

        ozi.close_bridge()

        assert ozi._bridge is None
        assert fake_popen['proc'].written[-1] == 'quit'

    def test_close_bridge_without_bridge(self, monkeypatch):
        monkeypatch.setattr(ozi, '_bridge', None)

        ozi.close_bridge()  # не должно бросать


# ─────────────────────────────────────────────────────────────────
# 6. open_in_oziexplorer: гибрид «API или запуск»
# ─────────────────────────────────────────────────────────────────

class FakeBridge:
    """Подмена OziApiBridge для проверки логики open_in_oziexplorer."""

    def __init__(self, ok=True, add_ok=True, add_counts=None, running=True,
                 fail_times=0, fail_loads=()):
        self.ok = ok
        self.add_ok = add_ok
        # (создано, не удалось); None — считать по числу точек
        self.add_counts = add_counts
        # Ответ на oziFindOzi: False — «OziExplorer ещё не готов».
        self.running = running
        # Загрузки с этими номерами (с 1) не удаются: проверка повторов и
        # того, что о частичном успехе сообщаем честно.
        self.fail_loads = set(fail_loads) | set(range(1, fail_times + 1))
        self.loads = []
        self.added = []

    def is_running(self):
        return self.running

    def load(self, path, track_num=1):
        self.loads.append((Path(path), track_num))
        if len(self.loads) in self.fail_loads:
            return False
        return self.ok

    def add_waypoints(self, points):
        self.added.append(list(points))
        if not self.add_ok:
            return None
        return self.add_counts or (len(points), 0)


class OziWorld:
    """Окружение OziExplorer для тестов: exe, DLL, процесс, мост, запуск.

    ``running`` — запущен ли OziExplorer *до* вызова; Popen подменён так,
    что после запуска exe процесс появляется (как в жизни).
    """

    def __init__(self, monkeypatch, tmp_path, bridge=None, dll=True,
                 exe=True, running=True):
        self.bridge = bridge
        self.launched = []
        self.exe = tmp_path / 'oziexp.exe' if exe else None
        self.running = running
        monkeypatch.setattr(ozi, 'find_oziexplorer', lambda cfg='': self.exe)
        monkeypatch.setattr(
            ozi, 'find_ozi_api',
            lambda cfg='', exe=None: (tmp_path / 'OziAPI.dll'
                                      if dll else None))
        monkeypatch.setattr(ozi, 'is_oziexplorer_running',
                            lambda: self.running)
        if bridge is not None:
            monkeypatch.setattr(ozi, 'get_bridge', lambda dll: bridge)
        monkeypatch.setattr(ozi.subprocess, 'Popen', self._popen)
        # Проверки не должны ждать по-настоящему.
        monkeypatch.setattr(ozi, '_OZI_POLL', 0.0)

    def _popen(self, args):
        self.launched.append(args)
        self.running = True
        return object()


@pytest.mark.story('open_in_oziexplorer')
class TestOpenInOziExplorer:
    @pytest.mark.smoke
    def test_missing_file(self, tmp_path):
        ok, msg = open_in_oziexplorer(tmp_path / 'nope.gpx')

        assert ok is False
        assert 'не найден' in msg

    def test_map_launches_without_dll(self, tmp_path, monkeypatch):
        """.map грузится и запуском exe — без DLL открываем в новом окне."""
        src = make_file(tmp_path / 'map.map')
        world = OziWorld(monkeypatch, tmp_path, dll=False)

        ok, msg = open_in_oziexplorer(src)

        assert ok is True
        assert 'открыто в OziExplorer' in msg
        assert world.launched == [[str(world.exe), str(src)]]

    def test_map_launch_exe_not_found(self, tmp_path, monkeypatch):
        src = make_file(tmp_path / 'map.map')
        OziWorld(monkeypatch, tmp_path, dll=False, exe=False)

        ok, msg = open_in_oziexplorer(src)

        assert ok is False
        assert 'не найден' in msg

    def test_exe_error_is_reported(self, tmp_path, monkeypatch):
        src = make_file(tmp_path / 'map.map')
        OziWorld(monkeypatch, tmp_path, dll=False)

        def boom(args):
            raise OSError('нет прав')

        monkeypatch.setattr(ozi.subprocess, 'Popen', boom)

        ok, msg = open_in_oziexplorer(src)

        assert ok is False
        assert 'не запустить' in msg

    def test_api_hit_skips_launch(self, tmp_path, monkeypatch):
        src = make_file(tmp_path / 'points.wpt')
        bridge = FakeBridge()
        world = OziWorld(monkeypatch, tmp_path, bridge)

        ok, msg = open_in_oziexplorer(src, wpt_mode='replace')

        assert ok is True
        assert 'открытое окно' in msg
        assert bridge.loads == [(src, 1)]
        assert world.launched == []

    def test_wpt_append_adds_points(self, tmp_path, monkeypatch):
        src = tmp_path / 'points.wpt'
        src.write_bytes(WPT_SAMPLE)
        bridge = FakeBridge()
        OziWorld(monkeypatch, tmp_path, bridge)

        ok, msg = open_in_oziexplorer(src)

        assert ok is True
        assert 'добавлено точек' in msg
        assert '2' in msg
        assert len(bridge.added) == 1
        assert len(bridge.added[0]) == 2
        assert bridge.loads == []

    def test_wpt_append_is_default(self, tmp_path, monkeypatch):
        src = tmp_path / 'points.wpt'
        src.write_bytes(WPT_SAMPLE)
        bridge = FakeBridge()
        OziWorld(monkeypatch, tmp_path, bridge)

        open_in_oziexplorer(src)

        assert bridge.added and not bridge.loads

    def test_wpt_replace_uses_load(self, tmp_path, monkeypatch):
        src = tmp_path / 'points.wpt'
        src.write_bytes(WPT_SAMPLE)
        bridge = FakeBridge()
        OziWorld(monkeypatch, tmp_path, bridge)

        ok, msg = open_in_oziexplorer(src, wpt_mode='replace')

        assert ok is True
        assert bridge.loads == [(src, 1)]
        assert bridge.added == []

    def test_wpt_append_without_points_fails(self, tmp_path, monkeypatch):
        src = make_file(tmp_path / 'empty.wpt')
        bridge = FakeBridge()
        OziWorld(monkeypatch, tmp_path, bridge)

        ok, msg = open_in_oziexplorer(src)

        assert ok is False
        assert 'нет точек' in msg
        assert bridge.added == []

    def test_wpt_append_failure_suggests_replace(self, tmp_path, monkeypatch):
        src = tmp_path / 'points.wpt'
        src.write_bytes(WPT_SAMPLE)
        bridge = FakeBridge(add_ok=False)
        OziWorld(monkeypatch, tmp_path, bridge)

        ok, msg = open_in_oziexplorer(src)

        assert ok is False
        assert 'заменить' in msg

    def test_wpt_append_reports_rejected_points(self, tmp_path, monkeypatch):
        """Часть точек OziExplorer может не взять — об этом говорим."""
        src = tmp_path / 'points.wpt'
        src.write_bytes(WPT_SAMPLE)
        bridge = FakeBridge(add_counts=(2, 1))
        OziWorld(monkeypatch, tmp_path, bridge)

        ok, msg = open_in_oziexplorer(src)

        assert ok is True
        assert 'добавлено точек' in msg
        assert 'не удалось: 1' in msg

    def test_track_number_is_passed(self, tmp_path, monkeypatch):
        src = make_file(tmp_path / 'track.plt')
        bridge = FakeBridge()
        OziWorld(monkeypatch, tmp_path, bridge)

        ok, _ = open_in_oziexplorer(src, track_num=7)

        assert ok is True
        assert bridge.loads == [(src, 7)]

    def test_map_loads_ignores_track_number(self, tmp_path, monkeypatch):
        src = make_file(tmp_path / 'map.map')
        bridge = FakeBridge()
        OziWorld(monkeypatch, tmp_path, bridge)

        ok, _ = open_in_oziexplorer(src, track_num=7)

        assert ok is True
        assert bridge.loads == [(src, 7)]

    def test_api_miss_for_map_falls_back_to_launch(self, tmp_path,
                                                   monkeypatch):
        """Карту не оставляем пользователю неоткрытой из-за сбоя API."""
        src = make_file(tmp_path / 'map.map')
        world = OziWorld(monkeypatch, tmp_path, FakeBridge(ok=False))

        ok, msg = open_in_oziexplorer(src)

        assert ok is True
        assert world.launched == [[str(world.exe), str(src)]]

    def test_api_miss_for_track_reports_error(self, tmp_path, monkeypatch):
        src = make_file(tmp_path / 'track.plt')
        world = OziWorld(monkeypatch, tmp_path, FakeBridge(ok=False))

        ok, msg = open_in_oziexplorer(src)

        assert ok is False
        assert 'не принял файл' in msg
        assert world.launched == []

    @pytest.mark.smoke
    def test_not_running_starts_oziexplorer(self, tmp_path, monkeypatch):
        """OziExplorer не запущен — запускаем сами и грузим через API."""
        src = make_file(tmp_path / 'track.plt')
        bridge = FakeBridge()
        world = OziWorld(monkeypatch, tmp_path, bridge, running=False)

        ok, _ = open_in_oziexplorer(src, track_num=3)

        assert ok is True
        # exe запускается без файла: файл грузит API, а не командная строка.
        assert world.launched == [[str(world.exe)]]
        assert bridge.loads == [(src, 3)]

    def test_started_oziexplorer_retries_load(self, tmp_path, monkeypatch):
        """Сразу после запуска загрузка может не пройти — повторяем."""
        src = make_file(tmp_path / 'track.plt')
        bridge = FakeBridge(fail_times=2)
        OziWorld(monkeypatch, tmp_path, bridge, running=False)

        ok, _ = open_in_oziexplorer(src, track_num=3)

        assert ok is True
        assert len(bridge.loads) == 3

    def test_start_timeout_is_reported(self, tmp_path, monkeypatch):
        src = make_file(tmp_path / 'track.plt')
        bridge = FakeBridge(running=False)
        OziWorld(monkeypatch, tmp_path, bridge, running=False)
        monkeypatch.setattr(ozi, 'OZI_START_TIMEOUT', 0.01)

        ok, msg = open_in_oziexplorer(src)

        assert ok is False
        assert 'не ответил' in msg
        assert bridge.loads == []

    def test_start_without_exe_is_reported(self, tmp_path, monkeypatch):
        src = make_file(tmp_path / 'track.plt')
        bridge = FakeBridge(running=False)
        OziWorld(monkeypatch, tmp_path, bridge, running=False, exe=False)

        ok, msg = open_in_oziexplorer(src)

        assert ok is False
        assert 'oziexp.exe' in msg
        assert bridge.loads == []

    def test_progress_reports_start(self, tmp_path, monkeypatch):
        """Пока запускается OziExplorer, программа показывает, что ждёт."""
        src = make_file(tmp_path / 'track.plt')
        bridge = FakeBridge()
        OziWorld(monkeypatch, tmp_path, bridge, running=False)
        seen = []

        open_in_oziexplorer(src, progress=seen.append)

        assert any('Запускаем' in text for text in seen)

    @pytest.mark.parametrize('name', ['track.plt', 'points.wpt',
                                      'track.gpx', 'track.kml'])
    def test_without_dll_is_honest(self, tmp_path, monkeypatch, name):
        """.plt/.wpt/.gpx/.kml без DLL в открытое окно не отдать."""
        src = make_file(tmp_path / name)
        world = OziWorld(monkeypatch, tmp_path, dll=False)

        ok, msg = open_in_oziexplorer(src)

        assert ok is False
        assert 'OziAPI.dll' in msg
        assert world.launched == []

    def test_gpx_goes_through_api(self, tmp_path, monkeypatch):
        """.gpx разбираем сами и грузим обычным .plt (см. gpxkml)."""
        src = make_file(tmp_path / 'track.gpx', GPX_SAMPLE)
        bridge = FakeBridge()
        OziWorld(monkeypatch, tmp_path, bridge)

        ok, msg = open_in_oziexplorer(src, track_num=5)

        assert ok is True
        assert bridge.loads == [(_import_path(src, '.plt'), 5)]
        assert 'треки: слот 5' in msg
        assert 'точки: добавлено 1' in msg
        written = _import_path(src, '.plt').read_text(encoding='cp1251')
        assert 'track' in written.splitlines()[4]

    def test_kml_goes_through_api(self, tmp_path, monkeypatch):
        src = make_file(
            tmp_path / 'points.kml',
            '<?xml version="1.0" encoding="UTF-8"?><kml><Document>'
            '<Placemark><name>A1</name><Point><coordinates>60.99,57.48'
            '</coordinates></Point></Placemark></Document></kml>')
        bridge = FakeBridge()
        OziWorld(monkeypatch, tmp_path, bridge)

        ok, msg = open_in_oziexplorer(src, wpt_mode='replace')

        assert ok is True
        assert bridge.loads == [(_import_path(src, '.wpt'), 1)]
        assert 'точки: заменены (1)' in msg

    def test_gpx_unparsable_is_reported(self, tmp_path, monkeypatch):
        src = make_file(tmp_path / 'track.gpx', '<gpx><мусор>')
        bridge = FakeBridge()
        OziWorld(monkeypatch, tmp_path, bridge)

        ok, msg = open_in_oziexplorer(src)

        assert ok is False
        assert bridge.loads == []
        assert tmp_path.name not in msg        # никаких путей наружу


# ─────────────────────────────────────────────────────────────────
# 7. import_gpx_kml: треки и точки из .gpx/.kml
# ─────────────────────────────────────────────────────────────────

def one_track(name='Тестовый', points=2) -> Track:
    return Track(name=name, segments=[[
        TrackPoint(lat=60.5 + index / 1000, lon=60.1 + index / 1000,
                   altitude_m=188.09 + index)
        for index in range(points)
    ]])


def one_point(name='A1') -> Waypoint:
    return Waypoint(name=name, lat=57.483576, lon=60.990078)


@pytest.mark.story('import_gpx_kml')
class TestImportGpxKml:
    def test_track_is_written_and_loaded(self, tmp_path):
        src = make_file(tmp_path / 'track.gpx')
        bridge = FakeBridge()

        ok, msg = import_gpx_kml(src, bridge, track_num=5,
                                 content=Content(tracks=[one_track()]))

        assert ok is True
        target = _import_path(src, '.plt')
        assert bridge.loads == [(target, 5)]
        lines = target.read_text(encoding='cp1251').splitlines()
        assert lines[0] == 'OziExplorer Track Point File Version 2.1'
        assert 'track' in lines[4]
        assert lines[5] == '2'
        assert 'треки: слот 5 (2 точек)' in msg

    def test_two_tracks_take_two_slots(self, tmp_path):
        src = make_file(tmp_path / 'track.gpx')
        bridge = FakeBridge()
        content = Content(tracks=[one_track('Первый'), one_track('Второй')])

        ok, msg = import_gpx_kml(src, bridge, track_num=5, content=content)

        assert ok is True
        assert bridge.loads == [
            (_import_path(src, '.plt', 1), 5),
            (_import_path(src, '.plt', 2), 6),
        ]
        assert 'треки: слоты 5..6' in msg
        assert ' 1' in _import_path(src, '.plt', 1).read_text(
            encoding='cp1251').splitlines()[4]

    def test_points_replace_writes_wpt(self, tmp_path):
        src = make_file(tmp_path / 'points.kml')
        bridge = FakeBridge()

        ok, msg = import_gpx_kml(src, bridge, wpt_mode='replace',
                                 content=Content(waypoints=[one_point()]))

        assert ok is True
        target = _import_path(src, '.wpt')
        assert bridge.loads == [(target, 1)]
        lines = target.read_text(encoding='cp1251').splitlines()
        assert lines[0] == 'OziExplorer Waypoint File Version 1.1'
        assert lines[4].startswith('1,A1,')
        assert 'точки: заменены (1)' in msg

    def test_points_append_creates_them(self, tmp_path):
        src = make_file(tmp_path / 'points.kml')
        bridge = FakeBridge()

        ok, msg = import_gpx_kml(src, bridge, wpt_mode='append',
                                 content=Content(waypoints=[one_point()]))

        assert ok is True
        assert len(bridge.added) == 1
        assert bridge.loads == []
        assert 'точки: добавлено 1' in msg

    def test_tracks_and_points_together(self, tmp_path):
        src = make_file(tmp_path / 'both.kml')
        bridge = FakeBridge()
        content = Content(tracks=[one_track()], waypoints=[one_point()])

        ok, msg = import_gpx_kml(src, bridge, track_num=2,
                                 wpt_mode='replace', content=content)

        assert ok is True
        assert bridge.loads == [(_import_path(src, '.plt'), 2),
                                (_import_path(src, '.wpt'), 1)]
        assert 'треки: слот 2' in msg
        assert 'точки: заменены (1)' in msg

    def test_slot_overflow_is_reported(self, tmp_path):
        src = make_file(tmp_path / 'track.gpx')
        bridge = FakeBridge()
        content = Content(tracks=[one_track(), one_track(), one_track()])

        ok, msg = import_gpx_kml(src, bridge, track_num=OZI_TRACK_MAX,
                                 content=content)

        assert ok is False
        assert 'не помещаются' in msg
        assert bridge.loads == []

    def test_last_slots_still_fit(self, tmp_path):
        src = make_file(tmp_path / 'track.gpx')
        bridge = FakeBridge()
        content = Content(tracks=[one_track(), one_track()])

        ok, _ = import_gpx_kml(src, bridge,
                               track_num=OZI_TRACK_MAX - 1, content=content)

        assert ok is True
        assert [num for _, num in bridge.loads] == [
            OZI_TRACK_MAX - 1, OZI_TRACK_MAX]

    def test_empty_content_is_reported(self, tmp_path):
        src = make_file(tmp_path / 'track.gpx')
        bridge = FakeBridge()

        ok, msg = import_gpx_kml(src, bridge, content=Content())

        assert ok is False
        assert 'нет ни треков, ни точек' in msg

    def test_file_is_parsed_when_content_is_missing(self, tmp_path):
        src = make_file(tmp_path / 'track.gpx', GPX_SAMPLE)
        bridge = FakeBridge()

        ok, _ = import_gpx_kml(src, bridge, track_num=4)

        assert ok is True
        assert bridge.loads[0][1] == 4

    def test_unparsable_file_is_reported(self, tmp_path):
        src = make_file(tmp_path / 'track.gpx', 'не xml')

        ok, msg = import_gpx_kml(src, FakeBridge())

        assert ok is False
        assert 'track.gpx' in msg

    def test_partial_failure_lists_what_loaded(self, tmp_path):
        src = make_file(tmp_path / 'track.gpx')
        bridge = FakeBridge(fail_loads=(2,))
        content = Content(tracks=[one_track(), one_track()])

        ok, msg = import_gpx_kml(src, bridge, track_num=5, content=content)

        assert ok is False
        assert 'трек не лёг в слот 6' in msg
        assert 'успело загрузиться' in msg
        assert 'слот 5' in msg

    def test_load_retries_when_asked(self, tmp_path):
        src = make_file(tmp_path / 'track.gpx')
        bridge = FakeBridge(fail_times=1)

        ok, _ = import_gpx_kml(src, bridge, track_num=5, retry=True,
                               content=Content(tracks=[one_track()]))

        assert ok is True
        assert len(bridge.loads) == 2

    def test_no_retry_by_default(self, tmp_path):
        src = make_file(tmp_path / 'track.gpx')
        bridge = FakeBridge(fail_times=1)

        ok, _ = import_gpx_kml(src, bridge, track_num=5,
                               content=Content(tracks=[one_track()]))

        assert ok is False
        assert len(bridge.loads) == 1


# ─────────────────────────────────────────────────────────────────
# 8. Копии файлов и имена треков
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Копии файлов для OziExplorer')
class TestImportPaths:
    def test_import_dir_is_inside_program_cache(self):
        assert _import_dir() == ozi._api_cache_dir() / 'import'
        assert _import_dir().is_dir()

    def test_same_file_gives_same_copy(self, tmp_path):
        src = make_file(tmp_path / 'track.gpx')

        assert _import_path(src, '.plt') == _import_path(src, '.plt')

    def test_different_files_do_not_collide(self, tmp_path):
        """Одноимённые файлы из разных папок не затирают копии друг друга."""
        first = make_file(tmp_path / 'a' / 'track.gpx')
        second = make_file(tmp_path / 'b' / 'track.gpx')

        assert _import_path(first, '.plt') != _import_path(second, '.plt')

    def test_suffix_and_index(self, tmp_path):
        src = make_file(tmp_path / 'track.gpx')

        assert _import_path(src, '.wpt').suffix == '.wpt'
        assert _import_path(src, '.plt', 2).stem.endswith('_2')

    def test_track_name_for_single_track(self, tmp_path):
        src = tmp_path / '20251016_Copter01.gpx'

        assert _track_name(src, 0, 1) == '20251016_Copter01'

    def test_track_name_for_several_tracks(self, tmp_path):
        src = tmp_path / '20260813_Avtonom03.kml'

        assert _track_name(src, 0, 3) == '20260813_Avtonom03 1'
        assert _track_name(src, 2, 3) == '20260813_Avtonom03 3'


@pytest.mark.story('clamp_track_num')
class TestClampTrackNum:
    def test_inside_range(self):
        assert clamp_track_num(7) == 7

    def test_low_is_one(self):
        assert clamp_track_num(0) == 1
        assert clamp_track_num(-5) == 1

    def test_high_is_max(self):
        assert clamp_track_num(OZI_TRACK_MAX + 10) == OZI_TRACK_MAX

    def test_garbage_is_one(self):
        assert clamp_track_num(None) == 1
        assert clamp_track_num('abc') == 1

    def test_string_number(self):
        assert clamp_track_num('12') == 12


def test_start_timeout_is_generous():
    """Замер на живом OziExplorer: готовность ~9 с, запас больше."""
    assert OZI_START_TIMEOUT >= 30.0


def test_supported_suffixes():
    assert OZI_FILE_SUFFIXES == {'.gpx', '.kml', '.plt', '.wpt', '.map'}


def test_track_and_wpt_mode_constants():
    assert OZI_TRACK_MAX == 75
    assert OZI_WPT_MODES == ('append', 'replace')
    assert OZI_WPT_DEFAULT_MODE in OZI_WPT_MODES


@pytest.mark.story('api_plan: пойдёт ли файл в открытое окно')
class TestApiPlan:
    def ready(self, monkeypatch, tmp_path, running=True, dll=True):
        monkeypatch.setattr(ozi, 'find_oziexplorer',
                            lambda cfg='': tmp_path / 'oziexp.exe')
        monkeypatch.setattr(ozi, 'find_ozi_api',
                            lambda cfg='', exe=None: (tmp_path / 'OziAPI.dll'
                                                      if dll else None))
        monkeypatch.setattr(ozi, 'is_oziexplorer_running', lambda: running)

    def test_track(self, tmp_path, monkeypatch):
        self.ready(monkeypatch, tmp_path)

        assert api_plan(tmp_path / 'track.plt') == 'track'

    def test_waypoints(self, tmp_path, monkeypatch):
        self.ready(monkeypatch, tmp_path)

        assert api_plan(tmp_path / 'points.wpt') == 'wp'

    def test_map(self, tmp_path, monkeypatch):
        self.ready(monkeypatch, tmp_path)

        assert api_plan(tmp_path / 'map.map') == 'map'

    def test_gpx_is_parsed_by_us(self, tmp_path, monkeypatch):
        self.ready(monkeypatch, tmp_path)

        assert api_plan(tmp_path / 'track.gpx') == OZI_PARSE_ACTION

    def test_kml_is_parsed_by_us(self, tmp_path, monkeypatch):
        self.ready(monkeypatch, tmp_path)

        assert api_plan(tmp_path / 'track.kml') == OZI_PARSE_ACTION

    def test_unknown_suffix(self, tmp_path, monkeypatch):
        self.ready(monkeypatch, tmp_path)

        assert api_plan(tmp_path / 'track.txt') == ''

    def test_without_dll(self, tmp_path, monkeypatch):
        self.ready(monkeypatch, tmp_path, dll=False)

        assert api_plan(tmp_path / 'track.plt') == ''

    def test_without_running_oziexplorer(self, tmp_path, monkeypatch):
        """OziExplorer не запущен, но его можно запустить — место спросим."""
        self.ready(monkeypatch, tmp_path, running=False)

        assert api_plan(tmp_path / 'track.plt') == 'track'
