"""Тесты watchdog-обработчиков (ok_helper.handlers).

Функциональность: DirHandler (дерево файлов) и DownloadsHandler
(папка загрузок с фильтрацией временных файлов браузеров).

События watchdog эмулируются простыми объектами — реальный Observer
не запускается.

Группировка в Allure:
  epic    = ok-filemanager
  feature = Watchdog
  story   = задаётся маркером @pytest.mark.story на каждом классе.
"""

from pathlib import Path

import allure
import pytest

from ok_helper.handlers import DirHandler, DownloadsHandler


pytestmark = [
    pytest.mark.handlers,
    pytest.mark.epic('ok-filemanager'),
    pytest.mark.feature('Watchdog'),
]


class FakeEvent:
    """Минимальная замена watchdog.events.FileSystemEvent."""

    def __init__(self, src_path='', dest_path='', is_directory=False):
        self.src_path = src_path
        self.dest_path = dest_path
        self.is_directory = is_directory


class Collector:
    """Собирает всё, что обработчик передал в callback."""

    def __init__(self):
        self.calls = []

    def __call__(self, value):
        self.calls.append(value)

    @property
    def count(self):
        return len(self.calls)


# ─────────────────────────────────────────────────────────────────
# 1. DirHandler — события дерева файлов
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('DirHandler')
class TestDirHandler:
    @pytest.fixture
    def handler(self):
        self.collector = Collector()
        return DirHandler(self.collector)

    @pytest.mark.smoke
    def test_created_file(self, handler, tmp_path):
        p = tmp_path / '100m.gpx'
        handler.on_created(FakeEvent(src_path=str(p)))

        assert self.collector.calls == [p]

    def test_created_directory_ignored(self, handler, tmp_path):
        handler.on_created(
            FakeEvent(src_path=str(tmp_path / 'sub'), is_directory=True))
        assert self.collector.count == 0

    @pytest.mark.smoke
    def test_moved_file_reports_destination(self, handler, tmp_path):
        """При переименовании важно новое имя, а не старое."""
        src = tmp_path / '2026-09-17_11-55_бабушка.gpx'
        dst = tmp_path / '20260917_Babushka.gpx'
        handler.on_moved(FakeEvent(src_path=str(src), dest_path=str(dst)))

        assert self.collector.calls == [dst]

    def test_moved_directory_ignored(self, handler, tmp_path):
        handler.on_moved(FakeEvent(src_path=str(tmp_path / 'a'),
                                   dest_path=str(tmp_path / 'b'),
                                   is_directory=True))
        assert self.collector.count == 0

    @pytest.mark.smoke
    def test_deleted_file_reports_none(self, handler, tmp_path):
        handler.on_deleted(FakeEvent(src_path=str(tmp_path / 'x.gpx')))

        assert self.collector.calls == [None]

    def test_deleted_directory_ignored(self, handler, tmp_path):
        handler.on_deleted(
            FakeEvent(src_path=str(tmp_path / 'sub'), is_directory=True))
        assert self.collector.count == 0

    def test_paths_are_path_objects(self, handler, tmp_path):
        handler.on_created(FakeEvent(src_path=str(tmp_path / 'a.plt')))

        assert isinstance(self.collector.calls[0], Path)

    def test_multiple_events_are_all_reported(self, handler, tmp_path):
        for name in ('a.gpx', 'b.plt', 'c.wpt'):
            handler.on_created(FakeEvent(src_path=str(tmp_path / name)))

        assert [p.name for p in self.collector.calls] == \
            ['a.gpx', 'b.plt', 'c.wpt']

    def test_created_then_deleted(self, handler, tmp_path):
        handler.on_created(FakeEvent(src_path=str(tmp_path / 'a.gpx')))
        handler.on_deleted(FakeEvent(src_path=str(tmp_path / 'a.gpx')))

        assert self.collector.calls == [tmp_path / 'a.gpx', None]

    def test_callback_is_stored_as_is(self):
        cb = Collector()
        assert DirHandler(cb).callback is cb


# ─────────────────────────────────────────────────────────────────
# 2. DownloadsHandler — фильтрация папки загрузок
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('DownloadsHandler')
class TestDownloadsHandler:
    @pytest.fixture
    def handler(self):
        self.collector = Collector()
        return DownloadsHandler(self.collector)

    @pytest.mark.smoke
    @pytest.mark.parametrize('name', [
        'track.gpx', 'track.plt', 'points.wpt',
        '2026-09-17_11-55_бабушка.gpx',
        'Waypoints_20260923.wpt',
    ])
    def test_interesting_files_are_reported(self, handler, tmp_path, name):
        p = tmp_path / name
        handler.on_created(FakeEvent(src_path=str(p)))

        assert self.collector.calls == [p]

    @pytest.mark.smoke
    @pytest.mark.parametrize('name', [
        'track.GPX', 'track.Plt', 'points.WPT',
    ])
    def test_extension_case_insensitive(self, handler, tmp_path, name):
        p = tmp_path / name
        handler.on_created(FakeEvent(src_path=str(p)))

        assert self.collector.count == 1

    @pytest.mark.parametrize('name', [
        'track.gpx.crdownload',   # Chrome / Edge
        'track.gpx.part',         # Firefox
        'track.gpx.tmp',
        'track.gpx.download',
        'track.gpx.partial',
        'track.gpx.opdownload',   # Opera
    ])
    def test_browser_temp_files_ignored(self, handler, tmp_path, name):
        handler.on_created(FakeEvent(src_path=str(tmp_path / name)))

        assert self.collector.count == 0

    @pytest.mark.parametrize('name', [
        'notes.txt', 'track.mp3', 'picture.jpg', 'archive.zip',
        'data.json', 'no_extension',
    ])
    def test_other_extensions_ignored(self, handler, tmp_path, name):
        handler.on_created(FakeEvent(src_path=str(tmp_path / name)))

        assert self.collector.count == 0

    def test_created_directory_ignored(self, handler, tmp_path):
        handler.on_created(
            FakeEvent(src_path=str(tmp_path / 'sub'), is_directory=True))
        assert self.collector.count == 0

    @pytest.mark.smoke
    def test_moved_uses_destination_name(self, handler, tmp_path):
        """Chrome пишет .crdownload и переименовывает в .gpx.

        Именно поэтому в on_moved проверяется dest_path: временное имя
        отфильтровалось бы, а итоговое — нет.
        """
        src = tmp_path / 'track.gpx.crdownload'
        dst = tmp_path / 'track.gpx'
        handler.on_moved(FakeEvent(src_path=str(src), dest_path=str(dst)))

        assert self.collector.calls == [dst]

    def test_moved_to_temp_name_ignored(self, handler, tmp_path):
        handler.on_moved(FakeEvent(src_path=str(tmp_path / 'track.gpx'),
                                   dest_path=str(tmp_path / 'track.gpx.part')))

        assert self.collector.count == 0

    def test_moved_directory_ignored(self, handler, tmp_path):
        handler.on_moved(FakeEvent(src_path=str(tmp_path / 'a'),
                                   dest_path=str(tmp_path / 'b.gpx'),
                                   is_directory=True))
        assert self.collector.count == 0

    def test_deleted_is_ignored(self, handler, tmp_path):
        """У DownloadsHandler удаление ничего не делает."""
        handler.on_deleted(FakeEvent(src_path=str(tmp_path / 'track.gpx')))

        assert self.collector.count == 0

    def test_same_path_reported_twice(self, handler, tmp_path):
        p = tmp_path / 'track.gpx'
        handler.on_created(FakeEvent(src_path=str(p)))
        handler.on_moved(FakeEvent(src_path=str(p), dest_path=str(p)))

        assert self.collector.calls == [p, p]

    def test_callback_receives_path_object(self, handler, tmp_path):
        handler.on_created(FakeEvent(src_path=str(tmp_path / 'a.gpx')))

        assert isinstance(self.collector.calls[0], Path)

    def test_temp_suffixes_constant(self):
        assert '.crdownload' in DownloadsHandler._TEMP_SUFFIXES
        assert '.part' in DownloadsHandler._TEMP_SUFFIXES
        assert DownloadsHandler._INTERESTING_SUFFIXES == \
            {'.gpx', '.plt', '.wpt'}

    def test_temp_suffixes_are_not_interesting(self):
        overlap = (DownloadsHandler._TEMP_SUFFIXES
                   & DownloadsHandler._INTERESTING_SUFFIXES)
        assert overlap == set()
