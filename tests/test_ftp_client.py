"""Тесты FTP-клиента (ok_helper.ftp): листинг, загрузка, удаление.

Функциональность: list_folder(), upload_file(), delete_file() и
внутренний _cwd_to_folder(), включая режим эмуляции
(ftp_readonly = true) и защиту от path traversal.

Реальный сервер не нужен: ftplib.FTP подменяется мини-эмуляцией,
которая ведёт себя как настоящий сервер (папки, листинг, ошибки 550).

Группировка в Allure:
  epic    = ok-helper-tui
  feature = FTP
  story   = задаётся маркером @pytest.mark.story на каждом классе.
"""

import ftplib
from pathlib import Path

import allure
import pytest

from ok_helper import ftp as ftp_mod
from ok_helper.ftp import (
    FtpError,
    delete_file,
    is_target_folder_name,
    list_folder,
    upload_file,
)


pytestmark = [
    pytest.mark.ftp,
    pytest.mark.epic('ok-helper-tui'),
    pytest.mark.feature('FTP'),
]


# ─────────────────────────────────────────────────────────────────
# Мини-эмуляция FTP-сервера
# ─────────────────────────────────────────────────────────────────

class FakeFtpServer:
    """Дерево папок + записи о том, что делал клиент."""

    def __init__(self, dirs=(), entries=None, mlsd=True, nlst_names=None,
                 fail_connect=False, fail_mkd=False, fail_delete=False,
                 fail_quit=False, fail_stor=False, fail_nlst=False):
        self.dirs = set(dirs)
        self.entries = dict(entries or {})
        self.mlsd_supported = mlsd
        self.nlst_names = nlst_names
        self.fail_connect = fail_connect
        self.fail_mkd = fail_mkd
        self.fail_delete = fail_delete
        self.fail_quit = fail_quit
        self.fail_stor = fail_stor
        self.fail_nlst = fail_nlst

        self.connections = []      # созданные FTP-подключения
        self.deleted = []          # имена удалённых файлов
        self.stored = []           # (команда, содержимое)

    @property
    def conn(self):
        assert self.connections, 'FTP-подключение не создавалось'
        return self.connections[-1]


class FakeFTP:
    """Одно подключение: помнит текущую папку и все вызовы."""

    def __init__(self, server):
        self.srv = server
        self.path = []
        self.calls = []
        self.logged_in = None
        self.passive = None
        self.closed = False
        server.connections.append(self)

    # --- служебное ---
    def _record(self, name, *args):
        self.calls.append((name,) + args)

    def _candidate(self, part):
        return '/'.join(self.path + [part])

    # --- команды FTP ---
    def connect(self, host, port, timeout=None):
        self._record('connect', host, port, timeout)
        if self.srv.fail_connect:
            raise ftplib.error_temp('421 Service not available')

    def login(self, user, password):
        self._record('login', user)
        self.logged_in = user

    def set_pasv(self, value):
        self._record('set_pasv', value)
        self.passive = value

    def cwd(self, part):
        self._record('cwd', part)
        if part == '..':
            if self.path:
                self.path.pop()
            return
        if self._candidate(part) not in self.srv.dirs:
            raise ftplib.error_perm(f'550 {part}: No such directory')
        self.path.append(part)

    def mkd(self, part):
        self._record('mkd', part)
        if self.srv.fail_mkd:
            raise ftplib.error_perm('550 Permission denied')
        path = self._candidate(part)
        self.srv.dirs.add(path)
        return path

    def _current(self):
        return '/'.join(self.path)

    def mlsd(self):
        self._record('mlsd', self._current())
        if not self.srv.mlsd_supported:
            raise ftplib.error_perm('500 Unknown command MLSD')
        for name, kind, size in self.srv.entries.get(self._current(), []):
            facts = {'type': kind}
            if kind == 'file':
                facts['size'] = str(size)
            yield name, facts

    def nlst(self):
        self._record('nlst', self._current())
        if self.srv.fail_nlst:
            raise ftplib.error_perm('550 Cannot list')
        if self.srv.nlst_names is not None:
            return list(self.srv.nlst_names)
        return [n for n, _, _ in self.srv.entries.get(self._current(), [])]

    def storbinary(self, cmd, f):
        self._record('storbinary', cmd)
        data = f.read()
        if self.srv.fail_stor:
            raise ftplib.error_perm('552 Disk full')
        self.srv.stored.append((cmd, data))

    def delete(self, name):
        self._record('delete', name)
        if self.srv.fail_delete:
            raise ftplib.error_perm(f'550 {name}: Permission denied')
        self.srv.deleted.append(name)

    def quit(self):
        self._record('quit')
        if self.srv.fail_quit:
            raise OSError('соединение уже закрыто')

    def close(self):
        self._record('close')
        self.closed = True


@pytest.fixture
def server(monkeypatch):
    """Подменяет ftplib.FTP мини-сервером и отдаёт его тесту."""
    srv = FakeFtpServer()
    monkeypatch.setattr(ftp_mod.ftplib, 'FTP', lambda *a, **kw: FakeFTP(srv))
    return srv


@pytest.fixture
def remote_server(monkeypatch):
    """Готовое дерево: /upload/2026-09-23_Moscow с файлом и папкой."""
    srv = FakeFtpServer(
        dirs={'upload', 'upload/2026-09-23_Moscow',
              'upload/2026-09-23_Moscow/sub'},
        entries={
            'upload/2026-09-23_Moscow': [
                ('100m.gpx', 'file', 1024),
                ('sub', 'dir', 0),
                ('.', 'dir', 0),
                ('..', 'dir', 0),
            ],
        },
    )
    monkeypatch.setattr(ftp_mod.ftplib, 'FTP', lambda *a, **kw: FakeFTP(srv))
    return srv


def records(srv, name):
    """Все вызовы указанной команды по всем подключениям."""
    out = []
    for conn in srv.connections:
        out.extend(c[1:] for c in conn.calls if c[0] == name)
    return out


def touch(p: Path) -> Path:
    p.write_text('')
    return p


# ─────────────────────────────────────────────────────────────────
# 1. is_target_folder_name — вспомогательная проверка
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Имя целевой папки')
class TestIsTargetFolderName:
    @pytest.mark.parametrize('name', [
        '2026-09-23_Moscow',
        '2026-09-23_moscow-1',
        '  2026-09-23_Moscow  ',
        '2026-01-01_a',
    ])
    def test_valid(self, name):
        assert is_target_folder_name(name) is True

    @pytest.mark.parametrize('name', [
        '', None, '2026-09-23', '2026-09-23_', '23-09-2026_Moscow',
        'Moscow_2026-09-23', '2026-09-23_Москва',
    ])
    def test_invalid(self, name):
        assert is_target_folder_name(name) is False


# ─────────────────────────────────────────────────────────────────
# 2. list_folder — листинг
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Листинг папки')
class TestListFolder:
    @pytest.mark.smoke
    def test_mlsd_listing(self, remote_server):
        entries = list_folder('ftp.example.com', 21, 'user', 'pw',
                              '/upload', '2026-09-23_Moscow')

        assert entries == [('sub', True, 0), ('100m.gpx', False, 1024)]

    def test_directories_come_first(self, remote_server):
        entries = list_folder('h', 21, 'u', 'p', '/upload',
                              '2026-09-23_Moscow')
        flags = [is_dir for _, is_dir, _ in entries]

        assert flags == sorted(flags, reverse=True)

    @pytest.mark.parametrize('dot', ['.', '..'])
    def test_dot_entries_skipped(self, remote_server, dot):
        names = [n for n, _, _ in list_folder(
            'h', 21, 'u', 'p', '/upload', '2026-09-23_Moscow')]

        assert dot not in names

    def test_unknown_type_skipped(self, monkeypatch):
        srv = FakeFtpServer(
            dirs={'d'},
            entries={'d': [('weird', 'cdir', 0), ('ok.gpx', 'file', 5)]},
        )
        monkeypatch.setattr(ftp_mod.ftplib, 'FTP',
                            lambda *a, **kw: FakeFTP(srv))

        assert list_folder('h', 21, 'u', 'p', '', 'd') == \
            [('ok.gpx', False, 5)]

    def test_size_parsed_from_facts(self, remote_server):
        entries = list_folder('h', 21, 'u', 'p', '/upload',
                              '2026-09-23_Moscow')

        assert ('100m.gpx', False, 1024) in entries

    def test_entries_sorted_case_insensitively(self, monkeypatch):
        srv = FakeFtpServer(dirs={'d'}, entries={'d': [
            ('b.gpx', 'file', 1), ('A.gpx', 'file', 1), ('c.gpx', 'file', 1),
        ]})
        monkeypatch.setattr(ftp_mod.ftplib, 'FTP',
                            lambda *a, **kw: FakeFTP(srv))

        names = [n for n, _, _ in list_folder('h', 21, 'u', 'p', '', 'd')]
        assert names == ['A.gpx', 'b.gpx', 'c.gpx']

    @pytest.mark.smoke
    def test_cwd_order_base_then_folder(self, remote_server):
        list_folder('h', 21, 'u', 'p', '/upload', '2026-09-23_Moscow')

        assert records(remote_server, 'cwd') == \
            [('upload',), ('2026-09-23_Moscow',)]

    @pytest.mark.parametrize('base', ['', '/'])
    def test_empty_base_is_skipped(self, monkeypatch, base):
        srv = FakeFtpServer(dirs={'2026-09-23_Moscow'},
                            entries={'2026-09-23_Moscow': []})
        monkeypatch.setattr(ftp_mod.ftplib, 'FTP',
                            lambda *a, **kw: FakeFTP(srv))

        list_folder('h', 21, 'u', 'p', base, '2026-09-23_Moscow')

        assert records(srv, 'cwd') == [('2026-09-23_Moscow',)]

    def test_nested_folder_parts(self, monkeypatch):
        srv = FakeFtpServer(dirs={'a', 'a/b'}, entries={'a/b': []})
        monkeypatch.setattr(ftp_mod.ftplib, 'FTP',
                            lambda *a, **kw: FakeFTP(srv))

        list_folder('h', 21, 'u', 'p', '', 'a/b')

        assert records(srv, 'cwd') == [('a',), ('b',)]

    def test_pasv_and_login(self, remote_server):
        list_folder('ftp.example.com', 2121, 'bob', 'pw', '/upload',
                    '2026-09-23_Moscow')

        assert records(remote_server, 'connect') == \
            [('ftp.example.com', 2121, 30)]
        assert records(remote_server, 'login') == [('bob',)]
        assert records(remote_server, 'set_pasv') == [(True,)]

    def test_log_callback_receives_summary(self, remote_server):
        lines = []
        list_folder('h', 21, 'u', 'p', '/upload', '2026-09-23_Moscow',
                    log=lines.append)

        assert any('Листинг' in line and '2 элементов' in line
                   for line in lines)

    @pytest.mark.smoke
    def test_quit_called(self, remote_server):
        list_folder('h', 21, 'u', 'p', '/upload', '2026-09-23_Moscow')

        assert records(remote_server, 'quit') == [()]

    def test_close_after_failed_quit(self, monkeypatch):
        srv = FakeFtpServer(dirs={'d'}, entries={'d': []}, fail_quit=True)
        monkeypatch.setattr(ftp_mod.ftplib, 'FTP',
                            lambda *a, **kw: FakeFTP(srv))

        list_folder('h', 21, 'u', 'p', '', 'd')

        assert records(srv, 'close') == [()]

    # --- ошибки конфигурации ---

    def test_missing_host(self, server):
        with pytest.raises(FtpError, match='ftp_host'):
            list_folder('', 21, 'u', 'p', '', 'd')
        assert server.connections == []

    def test_missing_user(self, server):
        with pytest.raises(FtpError, match='ftp_user'):
            list_folder('h', 21, '', 'p', '', 'd')
        assert server.connections == []

    @pytest.mark.parametrize('folder', ['', '   '])
    def test_missing_folder(self, server, folder):
        with pytest.raises(FtpError, match='папка на сервере не задана'):
            list_folder('h', 21, 'u', 'p', '', folder)

    def test_unknown_folder(self, remote_server):
        with pytest.raises(FtpError, match='нет папки nope'):
            list_folder('h', 21, 'u', 'p', '/upload', 'nope')

    def test_connection_error_wrapped(self, monkeypatch):
        srv = FakeFtpServer(fail_connect=True)
        monkeypatch.setattr(ftp_mod.ftplib, 'FTP',
                            lambda *a, **kw: FakeFTP(srv))

        with pytest.raises(FtpError, match='421'):
            list_folder('h', 21, 'u', 'p', '', 'd')

    # --- запасной путь через NLST ---

    @pytest.mark.smoke
    def test_nlst_fallback(self, monkeypatch):
        """MLSD не поддерживается — работаем через NLST."""
        srv = FakeFtpServer(
            dirs={'d', 'd/sub'},
            entries={'d': [('sub', 'dir', 0), ('f.gpx', 'file', 0)]},
            mlsd=False,
        )
        monkeypatch.setattr(ftp_mod.ftplib, 'FTP',
                            lambda *a, **kw: FakeFTP(srv))

        entries = list_folder('h', 21, 'u', 'p', '', 'd')

        assert entries == [('sub', True, 0), ('f.gpx', False, 0)]

    def test_nlst_fallback_logs(self, monkeypatch):
        srv = FakeFtpServer(dirs={'d'}, entries={'d': []}, mlsd=False)
        monkeypatch.setattr(ftp_mod.ftplib, 'FTP',
                            lambda *a, **kw: FakeFTP(srv))
        lines = []

        list_folder('h', 21, 'u', 'p', '', 'd', log=lines.append)

        assert any('MLSD не поддерживается' in line for line in lines)

    def test_nlst_fallback_strips_path(self, monkeypatch):
        srv = FakeFtpServer(dirs={'d'}, mlsd=False,
                            nlst_names=['d/file.gpx'])
        monkeypatch.setattr(ftp_mod.ftplib, 'FTP',
                            lambda *a, **kw: FakeFTP(srv))

        assert list_folder('h', 21, 'u', 'p', '', 'd') == \
            [('file.gpx', False, 0)]

    def test_nlst_failure_wrapped(self, monkeypatch):
        srv = FakeFtpServer(dirs={'d'}, mlsd=False, fail_nlst=True)
        monkeypatch.setattr(ftp_mod.ftplib, 'FTP',
                            lambda *a, **kw: FakeFTP(srv))

        with pytest.raises(FtpError, match='не получить листинг'):
            list_folder('h', 21, 'u', 'p', '', 'd')


# ─────────────────────────────────────────────────────────────────
# 3. upload_file — загрузка
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Загрузка файла')
class TestUploadFile:
    def test_happy_path(self, tmp_path, remote_server):
        src = touch(tmp_path / '100m.gpx')
        lines = []

        upload_file('h', 21, 'u', 'p', '/upload', '2026-09-23_Moscow',
                    src, log=lines.append)

        assert remote_server.stored == \
            [('STOR 100m.gpx', b'')]
        assert records(remote_server, 'cwd') == \
            [('upload',), ('2026-09-23_Moscow',)]
        assert any('Готово' in line for line in lines)

    def test_creates_missing_folders(self, tmp_path, monkeypatch):
        srv = FakeFtpServer(dirs=set())
        monkeypatch.setattr(ftp_mod.ftplib, 'FTP',
                            lambda *a, **kw: FakeFTP(srv))
        src = touch(tmp_path / '100m.gpx')
        lines = []

        upload_file('h', 21, 'u', 'p', '', '2026-09-23_Moscow',
                    src, log=lines.append)

        assert records(srv, 'mkd') == [('2026-09-23_Moscow',)]
        assert any('Создаю папку' in line for line in lines)
        assert srv.stored == [('STOR 100m.gpx', b'')]

    def test_warns_when_file_already_on_server(self, tmp_path, monkeypatch):
        srv = FakeFtpServer(
            dirs={'2026-09-23_Moscow'},
            entries={'2026-09-23_Moscow': [('100m.gpx', 'file', 10)]},
        )
        monkeypatch.setattr(ftp_mod.ftplib, 'FTP',
                            lambda *a, **kw: FakeFTP(srv))
        src = touch(tmp_path / '100m.gpx')
        lines = []

        upload_file('h', 21, 'u', 'p', '', '2026-09-23_Moscow',
                    src, log=lines.append)

        assert any('уже есть' in line for line in lines)

    def test_upload_content_is_sent(self, tmp_path, remote_server):
        payload = '<gpx>данные</gpx>'.encode('utf-8')
        src = tmp_path / '100m.gpx'
        src.write_bytes(payload)

        upload_file('h', 21, 'u', 'p', '/upload', '2026-09-23_Moscow', src)

        assert remote_server.stored[0][1] == payload

    @pytest.mark.smoke
    def test_readonly_does_not_write(self, tmp_path, remote_server):
        """Режим эмуляции: подключаемся и читаем, но не пишем."""
        src = touch(tmp_path / '100m.gpx')
        lines = []

        upload_file('h', 21, 'u', 'p', '/upload', '2026-09-23_Moscow',
                    src, log=lines.append, readonly=True)

        assert remote_server.stored == []
        assert records(remote_server, 'mkd') == []
        assert records(remote_server, 'storbinary') == []
        assert any('эмуляция' in line for line in lines)

    def test_readonly_requires_existing_folder(self, tmp_path, monkeypatch):
        srv = FakeFtpServer(dirs=set())
        monkeypatch.setattr(ftp_mod.ftplib, 'FTP',
                            lambda *a, **kw: FakeFTP(srv))
        src = touch(tmp_path / '100m.gpx')

        with pytest.raises(FtpError, match='нет папки'):
            upload_file('h', 21, 'u', 'p', '', '2026-09-23_Moscow',
                        src, readonly=True)

        assert records(srv, 'mkd') == []

    def test_readonly_log_mentions_size(self, tmp_path, remote_server):
        src = tmp_path / '100m.gpx'
        src.write_bytes(b'0123456789')
        lines = []

        upload_file('h', 21, 'u', 'p', '/upload', '2026-09-23_Moscow',
                    src, log=lines.append, readonly=True)

        assert any('10 Б' in line for line in lines)

    # --- отказы до подключения ---

    @pytest.mark.parametrize('name', [
        'notes.txt', '20260923_Ivanov.gpx', 'random.wpt',
        '20260923_иванов.plt',
    ])
    def test_rejects_not_uploadable(self, tmp_path, server, name):
        src = touch(tmp_path / name)

        with pytest.raises(FtpError, match='недопустимый файл'):
            upload_file('h', 21, 'u', 'p', '/upload', '2026-09-23_Moscow',
                        src)

        assert server.connections == []

    def test_rejects_ten_tracks(self, tmp_path, server):
        d = tmp_path / '10-Tracks'
        d.mkdir()
        src = touch(d / '100m.gpx')

        with pytest.raises(FtpError, match='10-Tracks'):
            upload_file('h', 21, 'u', 'p', '/upload', '2026-09-23_Moscow',
                        src)

    def test_rejection_message_lists_allowed(self, tmp_path, server):
        src = touch(tmp_path / 'notes.txt')

        with pytest.raises(FtpError, match=r'm\.gpx'):
            upload_file('h', 21, 'u', 'p', '/upload', '2026-09-23_Moscow',
                        src)

    @pytest.mark.parametrize('folder,reason', [
        ('', 'некорректное имя папки'),
        ('2026-09-23', 'некорректное имя папки'),
        ('Moscow', 'некорректное имя папки'),
    ])
    def test_rejects_bad_folder_name(self, tmp_path, server, folder, reason):
        src = touch(tmp_path / '100m.gpx')

        with pytest.raises(FtpError, match=reason):
            upload_file('h', 21, 'u', 'p', '/upload', folder, src)

        assert server.connections == []

    def test_root_target_rejected_by_name_check(self, tmp_path, server):
        """'/' не проходит уже проверку имени папки."""
        src = touch(tmp_path / '100m.gpx')

        with pytest.raises(FtpError, match='некорректное имя папки'):
            upload_file('h', 21, 'u', 'p', '/', '/', src)

        assert server.connections == []

    def test_rejects_missing_host(self, tmp_path, server):
        src = touch(tmp_path / '100m.gpx')

        with pytest.raises(FtpError, match='ftp_host'):
            upload_file('', 21, 'u', 'p', '', '2026-09-23_Moscow', src)

    def test_rejects_missing_user(self, tmp_path, server):
        src = touch(tmp_path / '100m.gpx')

        with pytest.raises(FtpError, match='ftp_user'):
            upload_file('h', 21, '', 'p', '', '2026-09-23_Moscow', src)

    def test_rejects_missing_local_file(self, tmp_path, server):
        with pytest.raises(FtpError, match='локальный файл не найден'):
            upload_file('h', 21, 'u', 'p', '', '2026-09-23_Moscow',
                        tmp_path / '20260923_Ivanov.plt')

    # --- ошибки сервера ---

    def test_mkd_failure_wrapped(self, tmp_path, monkeypatch):
        srv = FakeFtpServer(dirs=set(), fail_mkd=True)
        monkeypatch.setattr(ftp_mod.ftplib, 'FTP',
                            lambda *a, **kw: FakeFTP(srv))
        src = touch(tmp_path / '100m.gpx')

        with pytest.raises(FtpError, match='не создать'):
            upload_file('h', 21, 'u', 'p', '', '2026-09-23_Moscow', src)

    def test_stor_failure_wrapped(self, tmp_path, monkeypatch):
        srv = FakeFtpServer(dirs={'2026-09-23_Moscow'}, fail_stor=True)
        monkeypatch.setattr(ftp_mod.ftplib, 'FTP',
                            lambda *a, **kw: FakeFTP(srv))
        src = touch(tmp_path / '100m.gpx')

        with pytest.raises(FtpError, match='552'):
            upload_file('h', 21, 'u', 'p', '', '2026-09-23_Moscow', src)

    def test_nlst_failure_before_upload_is_tolerated(self, tmp_path,
                                                     monkeypatch):
        srv = FakeFtpServer(dirs={'2026-09-23_Moscow'}, fail_nlst=True)
        monkeypatch.setattr(ftp_mod.ftplib, 'FTP',
                            lambda *a, **kw: FakeFTP(srv))
        src = touch(tmp_path / '100m.gpx')

        upload_file('h', 21, 'u', 'p', '', '2026-09-23_Moscow', src)

        assert srv.stored == [('STOR 100m.gpx', b'')]

    def test_close_after_failed_quit(self, tmp_path, monkeypatch):
        srv = FakeFtpServer(dirs={'2026-09-23_Moscow'}, fail_quit=True)
        monkeypatch.setattr(ftp_mod.ftplib, 'FTP',
                            lambda *a, **kw: FakeFTP(srv))
        src = touch(tmp_path / '100m.gpx')

        upload_file('h', 21, 'u', 'p', '', '2026-09-23_Moscow', src)

        assert records(srv, 'close') == [()]


# ─────────────────────────────────────────────────────────────────
# 4. delete_file — удаление
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Удаление файла')
class TestDeleteFile:
    @pytest.mark.smoke
    def test_happy_path(self, remote_server):
        lines = []

        delete_file('h', 21, 'u', 'p', '/upload', '2026-09-23_Moscow',
                    '100m.gpx', log=lines.append)

        assert remote_server.deleted == ['100m.gpx']
        assert records(remote_server, 'delete') == [('100m.gpx',)]
        assert any('Удалён' in line for line in lines)

    @pytest.mark.smoke
    def test_readonly_forbids_and_does_not_connect(self, server):
        """README: удаление только при ftp_readonly = false."""
        with pytest.raises(FtpError, match='режиме эмуляции'):
            delete_file('h', 21, 'u', 'p', 'upload', '2026-09-23_Moscow',
                        '100m.gpx', readonly=True)

        assert server.connections == []

    def test_readonly_checked_before_config(self, server):
        """Даже с пустым конфигом сообщение — про эмуляцию."""
        with pytest.raises(FtpError, match='эмуляц'):
            delete_file('', 21, '', '', '', '', '', readonly=True)

    @pytest.mark.parametrize('name', [
        '', '   ', '.', '..', 'a/b', 'a\\b', 'sub/../../etc/passwd',
    ])
    def test_invalid_names_rejected(self, remote_server, name):
        with pytest.raises(FtpError, match='некорректное имя файла'):
            delete_file('h', 21, 'u', 'p', '/upload', '2026-09-23_Moscow',
                        name)

        assert remote_server.deleted == []

    def test_nul_byte_in_name_rejected(self, remote_server):
        with pytest.raises(FtpError, match='некорректное имя файла'):
            delete_file('h', 21, 'u', 'p', '/upload', '2026-09-23_Moscow',
                        'a\x00b')

    def test_missing_host(self, server):
        with pytest.raises(FtpError, match='ftp_host'):
            delete_file('', 21, 'u', 'p', '', '2026-09-23_Moscow', 'a.gpx')

    def test_missing_user(self, server):
        with pytest.raises(FtpError, match='ftp_user'):
            delete_file('h', 21, '', 'p', '', '2026-09-23_Moscow', 'a.gpx')

    def test_missing_folder(self, server):
        with pytest.raises(FtpError, match='папка на сервере не задана'):
            delete_file('h', 21, 'u', 'p', '', '  ', 'a.gpx')

    def test_unknown_folder(self, remote_server):
        with pytest.raises(FtpError, match='нет папки nope'):
            delete_file('h', 21, 'u', 'p', '/upload', 'nope', 'a.gpx')

    def test_server_refusal_wrapped(self, monkeypatch):
        srv = FakeFtpServer(dirs={'2026-09-23_Moscow'}, fail_delete=True)
        monkeypatch.setattr(ftp_mod.ftplib, 'FTP',
                            lambda *a, **kw: FakeFTP(srv))

        with pytest.raises(FtpError, match='не удалить'):
            delete_file('h', 21, 'u', 'p', '', '2026-09-23_Moscow',
                        'a.gpx')

    def test_connection_error_wrapped(self, monkeypatch):
        srv = FakeFtpServer(fail_connect=True)
        monkeypatch.setattr(ftp_mod.ftplib, 'FTP',
                            lambda *a, **kw: FakeFTP(srv))

        with pytest.raises(FtpError, match='421'):
            delete_file('h', 21, 'u', 'p', '', '2026-09-23_Moscow', 'a.gpx')

    def test_close_after_failed_quit(self, monkeypatch):
        srv = FakeFtpServer(dirs={'2026-09-23_Moscow'}, fail_quit=True)
        monkeypatch.setattr(ftp_mod.ftplib, 'FTP',
                            lambda *a, **kw: FakeFTP(srv))

        delete_file('h', 21, 'u', 'p', '', '2026-09-23_Moscow', 'a.gpx')

        assert records(srv, 'close') == [()]

    def test_name_is_trimmed(self, remote_server):
        delete_file('h', 21, 'u', 'p', '/upload', '2026-09-23_Moscow',
                    '  100m.gpx  ')

        assert remote_server.deleted == ['100m.gpx']
