"""Watchdog-обработчик для одной директории."""

from pathlib import Path
from typing import Callable, Optional

from watchdog.events import FileSystemEventHandler


class DirHandler(FileSystemEventHandler):
    def __init__(self, callback: Callable[[Optional[Path]], None]):
        self.callback = callback

    def on_created(self, e):
        if not e.is_directory:
            self.callback(Path(e.src_path))

    def on_moved(self, e):
        if not e.is_directory:
            self.callback(Path(e.dest_path))

    def on_deleted(self, e):
        if not e.is_directory:
            self.callback(None)

class DownloadsHandler(FileSystemEventHandler):
    """Отслеживает появление новых файлов в папке загрузок.

    Отфильтровывает временные файлы браузеров (Chrome/Edge создают
    *.crdownload, Firefox — *.part и т.п.) и пропускает только те
    расширения, с которыми работает ok-helper.
    """

    _TEMP_SUFFIXES = {
        '.crdownload', '.part', '.tmp', '.download', '.partial',
        '.opdownload',  # Opera
    }
    _INTERESTING_SUFFIXES = {'.gpx', '.plt', '.wpt'}

    def __init__(self, callback: Callable[[Path], None]):
        self.callback = callback

    def _maybe_fire(self, path: Path) -> None:
        suf = path.suffix.lower()
        if suf in self._TEMP_SUFFIXES:
            return
        if suf not in self._INTERESTING_SUFFIXES:
            return
        self.callback(path)

    def on_created(self, e):
        if not e.is_directory:
            self._maybe_fire(Path(e.src_path))

    def on_moved(self, e):
        # Chrome сохраняет во временный .crdownload и затем
        # переименовывает — именно этот on_moved нам и нужен.
        if not e.is_directory:
            self._maybe_fire(Path(e.dest_path))