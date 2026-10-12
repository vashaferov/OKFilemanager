"""Общие фикстуры и настройка импорта для тестов."""

import os
import sys
from pathlib import Path

import pytest

# Корень проекта — на sys.path, чтобы `import ok_helper` работал
# независимо от того, из какой директории запущен pytest.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Тесты GUI не должны требовать реального экрана: Qt рисует в offscreen-
# плагин. Переменную надо выставить до создания QApplication.
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


@pytest.fixture(scope='session')
def qapp():
    """Единственный на весь прогон QApplication (offscreen)."""
    QtWidgets = pytest.importorskip(
        'PySide6.QtWidgets', reason='PySide6 не установлен')
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    yield app
    app.processEvents()


@pytest.fixture
def gui_config(tmp_path):
    """Конфиг в том виде, в каком его отдаёт config.load_config().

    Пути указывают внутрь tmp_path, чтобы ни один тест не тронул
    настоящий %APPDATA%/ok-helper-tui/config.toml.
    """
    root = tmp_path / 'root'
    root.mkdir()
    downloads = tmp_path / 'downloads'
    downloads.mkdir()
    return {
        'root_dir': str(root),
        'root_dir_resolved': root,
        'downloads_dir': str(downloads),
        'downloads_dir_resolved': downloads,
        'watch_downloads': False,
        'theme': 'light',
        'ftp_host': 'ftp.example.com',
        'ftp_port': 21,
        'ftp_user': 'user',
        'ftp_password': 'secret',
        'ftp_path': '/upload',
        'ftp_readonly': False,
        'help_visible_at_start': False,
        'first_run': False,
        '_path': tmp_path / 'config.toml',
    }


class MessageBoxRecorder:
    """Запись обращений к QMessageBox + управление ответом."""

    def __init__(self):
        self.calls = []          # (метод, заголовок, текст)
        self.answer = 'Yes'      # что вернёт question()
        self.click_text = None   # какую кнопку «нажали» в msg.exec()

    @property
    def kinds(self):
        return [c[0] for c in self.calls]

    def texts(self):
        return [c[2] for c in self.calls]


def _install_fake_message_box(monkeypatch, recorder):
    """Подменяет QMessageBox: ничего не показывает, всё записывает."""
    import ok_helper.gui as gui

    class FakeMessageBox:
        Yes = 'Yes'
        No = 'No'
        Ok = 'Ok'
        Cancel = 'Cancel'
        Question = 32
        AcceptRole = 0
        ActionRole = 1
        RejectRole = 2

        def __init__(self, parent=None):
            self._buttons = []
            self._chosen = None

        # --- модальное окно «Новый файл в загрузках» ---
        def setIcon(self, *a):
            pass

        def setWindowTitle(self, *a):
            pass

        def setText(self, *a):
            pass

        def setInformativeText(self, *a):
            pass

        def addButton(self, text, role=0):
            token = object()
            self._buttons.append((token, text))
            if recorder.click_text is None or text == recorder.click_text:
                self._chosen = token
            return token

        def setDefaultButton(self, *a):
            pass

        def exec(self):
            return 0

        def clickedButton(self):
            return self._chosen

        # --- статические хелперы ---
        @classmethod
        def critical(cls, parent, title, text='', *a, **kw):
            recorder.calls.append(('critical', title, str(text)))
            return cls.Ok

        @classmethod
        def information(cls, parent, title, text='', *a, **kw):
            recorder.calls.append(('information', title, str(text)))
            return cls.Ok

        @classmethod
        def warning(cls, parent, title, text='', *a, **kw):
            recorder.calls.append(('warning', title, str(text)))
            return cls.Ok

        @classmethod
        def question(cls, parent, title, text='', *a, **kw):
            recorder.calls.append(('question', title, str(text)))
            return recorder.answer

    monkeypatch.setattr(gui, 'QMessageBox', FakeMessageBox)
    return recorder


@pytest.fixture
def msgbox(monkeypatch):
    """Перехватывает все модальные окна, не открывая их."""
    return _install_fake_message_box(monkeypatch, MessageBoxRecorder())
