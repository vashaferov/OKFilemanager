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

    # Подписи стандартных кнопок: Qt отдаёт их на языке системы, в
    # тестах достаточно устойчивых названий.
    fake_standard_texts = {
        0x00000400: 'OK',        # Ok
        0x00000800: 'Сохранить',  # Save
        0x00400000: 'Отмена',     # Cancel
        0x00200000: 'Закрыть',    # Close
        0x00004000: 'Да',         # Yes
        0x00010000: 'Нет',        # No
        0x02000000: 'Применить',  # Apply
    }

    class FakeButton:
        """Кнопка заглушки: помнит подпись и минимальную ширину."""

        def __init__(self, box, text, standard=0):
            self._box = box
            self._text = text
            self.standard = standard
            self.minimum_width = 0

        def text(self):
            return self._text

        def set_text(self, value):
            self._text = value

        def setMinimumWidth(self, value):
            self.minimum_width = value

        def font(self):
            from PySide6.QtGui import QFont
            return QFont()

    class FakeMessageBox:
        # Значения совпадают с Qt: код обращается к кнопкам по этим
        # константам, и заглушка обязана отвечать так же.
        NoButton = 0x00000000
        Ok = 0x00000400
        Save = 0x00000800
        Cancel = 0x00400000
        Close = 0x00200000
        Yes = 0x00004000
        No = 0x00010000
        Apply = 0x02000000
        NoIcon = 0
        Information = 1
        Warning = 2
        Critical = 3
        Question = 4
        # Ответ question() и роли кнопок.
        AcceptRole = 0
        ActionRole = 1
        RejectRole = 2

        def __init__(self, parent=None):
            self._buttons = []
            self._by_key = {}
            self._chosen = None
            self._standard = 0

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
            token = FakeButton(self, text)
            self._buttons.append((token, text))
            if recorder.click_text is None or text == recorder.click_text:
                self._chosen = token
            return token

        def setStandardButtons(self, buttons):
            self._standard = buttons

        def button(self, which):
            """Возвращает кнопку по константе (Ok, Save, Cancel…)."""
            token = self._by_key.get(which)
            if token is None:
                token = FakeButton(self, '', which)
                self._by_key[which] = token
            return token

        def findChildren(self, _type=None):
            """Как QObject.findChildren: все кнопки, включая стандартные."""
            found = [token for token, _ in self._buttons]
            found.extend(self._by_key.values())
            for token in found:
                if not token.text():
                    token.set_text(fake_standard_texts.get(
                        token.standard, 'OK'))
            return found

        def setDefaultButton(self, *a):
            pass

        def exec(self):
            return 0

        def clickedButton(self):
            if self._chosen is not None:
                return self._chosen
            if self._by_key:
                return next(iter(self._by_key.values()))
            return None

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
            # Как настоящий Qt: возвращаем константу кнопки, а не строку.
            answer = str(recorder.answer).strip().lower()
            return cls.No if answer in ('no', 'n') else cls.Yes

    monkeypatch.setattr(gui, 'QMessageBox', FakeMessageBox)
    return recorder


@pytest.fixture
def msgbox(monkeypatch):
    """Перехватывает все модальные окна, не открывая их."""
    return _install_fake_message_box(monkeypatch, MessageBoxRecorder())
