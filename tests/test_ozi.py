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

import queue
import sys
import zipfile
from pathlib import Path

import allure
import pytest

from ok_helper import ozi
from ok_helper.ozi import (
    OZI_API_DOWNLOAD_URL,
    OZI_FILE_SUFFIXES,
    OziApiBridge,
    extract_bundled_api,
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


def make_zip(path: Path, members) -> Path:
    """Создаёт zip с заданными элементами (имя -> байты)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w') as zf:
        for name, data in members.items():
            zf.writestr(name, data)
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

    def __init__(self, ok=True):
        self.ok = ok
        self.loads = []

    def load(self, path, track_num=1):
        self.loads.append((Path(path), track_num))
        return self.ok


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
        bridge = FakeBridge()
        monkeypatch.setattr(ozi, 'find_oziexplorer',
                            lambda cfg='': tmp_path / 'oziexp.exe')
        monkeypatch.setattr(ozi, 'find_ozi_api',
                            lambda cfg='', exe=None: tmp_path / 'OziAPI.dll')
        monkeypatch.setattr(ozi, 'is_oziexplorer_running', lambda: True)
        monkeypatch.setattr(ozi, 'get_bridge', lambda dll: bridge)
        monkeypatch.setattr(ozi.subprocess, 'Popen',
                            lambda args: launched.append(args))

        ok, msg = open_in_oziexplorer(src)

        assert ok is True
        assert 'открытое окно' in msg
        assert bridge.loads == [(src, 1)]
        assert launched == []

    def test_track_number_is_passed(self, tmp_path, monkeypatch):
        src = make_file(tmp_path / 'track.plt')
        bridge = FakeBridge()
        monkeypatch.setattr(ozi, 'find_oziexplorer',
                            lambda cfg='': tmp_path / 'oziexp.exe')
        monkeypatch.setattr(ozi, 'find_ozi_api',
                            lambda cfg='', exe=None: tmp_path / 'OziAPI.dll')
        monkeypatch.setattr(ozi, 'is_oziexplorer_running', lambda: True)
        monkeypatch.setattr(ozi, 'get_bridge', lambda dll: bridge)

        ok, _ = open_in_oziexplorer(src, track_num=7)

        assert ok is True
        assert bridge.loads == [(src, 7)]

    def test_api_miss_falls_back_to_launch(self, tmp_path, monkeypatch):
        src = make_file(tmp_path / 'points.wpt')
        exe = tmp_path / 'oziexp.exe'
        launched = []
        monkeypatch.setattr(ozi, 'find_oziexplorer', lambda cfg='': exe)
        monkeypatch.setattr(ozi, 'find_ozi_api',
                            lambda cfg='', exe=None: tmp_path / 'OziAPI.dll')
        monkeypatch.setattr(ozi, 'is_oziexplorer_running', lambda: True)
        monkeypatch.setattr(ozi, 'get_bridge',
                            lambda dll: FakeBridge(ok=False))
        monkeypatch.setattr(ozi.subprocess, 'Popen',
                            lambda args: launched.append(args))

        ok, msg = open_in_oziexplorer(src)

        assert ok is True
        assert launched == [[str(exe), str(src)]]

    def test_not_running_launches(self, tmp_path, monkeypatch):
        """OziExplorer не запущен — мост даже не поднимается."""
        src = make_file(tmp_path / 'points.wpt')
        exe = tmp_path / 'oziexp.exe'
        launched = []

        def no_bridge(dll):
            raise AssertionError('мост не нужен, OziExplorer не запущен')

        monkeypatch.setattr(ozi, 'find_oziexplorer', lambda cfg='': exe)
        monkeypatch.setattr(ozi, 'find_ozi_api',
                            lambda cfg='', exe=None: tmp_path / 'OziAPI.dll')
        monkeypatch.setattr(ozi, 'is_oziexplorer_running', lambda: False)
        monkeypatch.setattr(ozi, 'get_bridge', no_bridge)
        monkeypatch.setattr(ozi.subprocess, 'Popen',
                            lambda args: launched.append(args))

        ok, _ = open_in_oziexplorer(src)

        assert ok is True
        assert launched == [[str(exe), str(src)]]

    def test_no_dll_launches(self, tmp_path, monkeypatch):
        src = make_file(tmp_path / 'points.wpt')
        exe = tmp_path / 'oziexp.exe'
        launched = []
        monkeypatch.setattr(ozi, 'find_oziexplorer', lambda cfg='': exe)
        monkeypatch.setattr(ozi, 'find_ozi_api',
                            lambda cfg='', exe=None: None)
        monkeypatch.setattr(ozi, 'is_oziexplorer_running', lambda: True)
        monkeypatch.setattr(ozi.subprocess, 'Popen',
                            lambda args: launched.append(args))

        ok, _ = open_in_oziexplorer(src)

        assert ok is True
        assert launched == [[str(exe), str(src)]]

    def test_gpx_always_launches(self, tmp_path, monkeypatch):
        """Для .gpx загрузчика в API нет — только запуск exe."""
        src = make_file(tmp_path / 'track.gpx')
        exe = tmp_path / 'oziexp.exe'
        launched = []
        monkeypatch.setattr(ozi, 'find_oziexplorer', lambda cfg='': exe)
        monkeypatch.setattr(ozi, 'find_ozi_api',
                            lambda cfg='', exe=None: tmp_path / 'OziAPI.dll')
        monkeypatch.setattr(ozi, 'is_oziexplorer_running', lambda: True)
        monkeypatch.setattr(ozi, 'get_bridge',
                            lambda dll: pytest.fail('API для .gpx не нужен'))
        monkeypatch.setattr(ozi.subprocess, 'Popen',
                            lambda args: launched.append(args))

        ok, _ = open_in_oziexplorer(src)

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
