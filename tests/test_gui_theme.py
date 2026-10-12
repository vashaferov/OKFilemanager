"""Тесты темы оформления (ok_helper.gui: _system_is_dark, resolve_theme,
build_stylesheet, THEMES).

QApplication не требуется: проверяются только чистые функции.

Группировка в Allure:
  epic    = ok-helper-tui
  feature = Оформление
  story   = задаётся маркером @pytest.mark.story на каждом классе.
"""

import re
import sys
import types
from pathlib import Path

import allure
import pytest
from PySide6.QtGui import QFontMetrics, QImage, QShowEvent
from PySide6.QtWidgets import QApplication

from ok_helper import gui


pytestmark = [
    pytest.mark.theme,
    pytest.mark.epic('ok-helper-tui'),
    pytest.mark.feature('Оформление'),
]


def make_winreg(value=1, exc=None):
    """Модуль-заглушка winreg с одним значением AppsUseLightTheme."""
    mod = types.ModuleType('winreg')
    mod.HKEY_CURRENT_USER = object()

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def open_key(root, path):
        if exc is not None:
            raise exc
        return Key()

    def query_value_ex(key, name):
        if exc is not None:
            raise exc
        return value, 4

    mod.OpenKey = open_key
    mod.QueryValueEx = query_value_ex
    return mod


# ─────────────────────────────────────────────────────────────────
# 1. _system_is_dark — системная тема Windows
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Системная тема Windows')
class TestSystemIsDark:
    @pytest.mark.parametrize('platform', ['linux', 'darwin'])
    def test_non_windows_is_light(self, monkeypatch, platform):
        monkeypatch.setattr(gui.sys, 'platform', platform)
        assert gui._system_is_dark() is False

    @pytest.mark.smoke
    def test_dark_when_apps_use_light_theme_is_zero(self, monkeypatch):
        monkeypatch.setattr(gui.sys, 'platform', 'win32')
        monkeypatch.setitem(sys.modules, 'winreg', make_winreg(value=0))

        assert gui._system_is_dark() is True

    @pytest.mark.smoke
    def test_light_when_apps_use_light_theme_is_one(self, monkeypatch):
        monkeypatch.setattr(gui.sys, 'platform', 'win32')
        monkeypatch.setitem(sys.modules, 'winreg', make_winreg(value=1))

        assert gui._system_is_dark() is False

    def test_registry_error_is_light(self, monkeypatch):
        monkeypatch.setattr(gui.sys, 'platform', 'win32')
        monkeypatch.setitem(sys.modules, 'winreg',
                            make_winreg(exc=FileNotFoundError('нет ключа')))

        assert gui._system_is_dark() is False

    def test_missing_winreg_module_is_light(self, monkeypatch):
        monkeypatch.setattr(gui.sys, 'platform', 'win32')
        # sys.modules[name] = None → `import winreg` бросает ImportError.
        monkeypatch.setitem(sys.modules, 'winreg', None)

        assert gui._system_is_dark() is False

    def test_string_value_is_compared_as_int(self, monkeypatch):
        monkeypatch.setattr(gui.sys, 'platform', 'win32')
        monkeypatch.setitem(sys.modules, 'winreg', make_winreg(value='0'))

        assert gui._system_is_dark() is True


# ─────────────────────────────────────────────────────────────────
# 2. resolve_theme
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Выбор темы')
class TestResolveTheme:
    @pytest.mark.smoke
    @pytest.mark.parametrize('setting,expected', [
        ('light', 'light'),
        ('dark', 'dark'),
    ])
    def test_explicit_theme_passed_through(self, monkeypatch, setting,
                                           expected):
        def boom():
            raise AssertionError('не должны читать системную тему')

        monkeypatch.setattr(gui, '_system_is_dark', boom)

        assert gui.resolve_theme(setting) == expected

    @pytest.mark.smoke
    @pytest.mark.parametrize('system_dark,expected', [
        (True, 'dark'),
        (False, 'light'),
    ])
    def test_auto_follows_system(self, monkeypatch, system_dark, expected):
        monkeypatch.setattr(gui, '_system_is_dark', lambda: system_dark)

        assert gui.resolve_theme('auto') == expected

    @pytest.mark.parametrize('setting', ['', 'AUTO', 'Dark', 'neon', None, 42])
    def test_unknown_falls_back_to_light(self, monkeypatch, setting):
        monkeypatch.setattr(gui, '_system_is_dark', lambda: True)

        assert gui.resolve_theme(setting) == 'light'

    def test_auto_never_returns_auto(self):
        assert gui.resolve_theme('auto') in ('light', 'dark')

    def test_result_is_a_known_theme(self):
        assert gui.resolve_theme('dark') in gui.THEMES


# ─────────────────────────────────────────────────────────────────
# 3. THEMES
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Палитры THEMES')
class TestThemes:
    def test_exactly_two_themes(self):
        assert set(gui.THEMES) == {'dark', 'light'}

    def test_same_keys_in_both_themes(self):
        dark = set(gui.THEMES['dark'])
        light = set(gui.THEMES['light'])

        assert dark == light

    @pytest.mark.parametrize('key', [
        'bg', 'bg_alt', 'bg_elev', 'text', 'text_dim', 'border',
        'accent', 'accent_h', 'sel_bg', 'sel_fg',
        'tree_dir', 'tree_file', 'tree_err', 'tree_up',
        'log_bg', 'log_fg',
    ])
    def test_required_keys_present(self, key):
        assert key in gui.THEMES['light']
        assert key in gui.THEMES['dark']

    @pytest.mark.parametrize('name', ['light', 'dark'])
    def test_colors_are_hex(self, name):
        for key, value in gui.THEMES[name].items():
            assert re.fullmatch(r'#[0-9a-fA-F]{6}', value), (key, value)

    @pytest.mark.parametrize('key', [
        'tree_dir', 'tree_err', 'tree_up',
    ])
    def test_light_tree_colors_match_readme(self, key):
        """README: папки — оранжевые, ошибки — красные, на FTP — зелёные.

        Конкретный оттенок — из палитры: тест следит за смыслом
        (тёплый/красный/зелёный), а не за точным значением.
        """
        value = gui.THEMES['light'][key]
        red, green, blue = (int(value[i:i + 2], 16) for i in (1, 3, 5))

        if key == 'tree_dir':
            assert red > green > blue
        elif key == 'tree_err':
            assert red > green and red > blue
        else:
            assert green > red and green > blue

    def test_themes_differ(self):
        assert gui.THEMES['light']['bg'] != gui.THEMES['dark']['bg']


# ─────────────────────────────────────────────────────────────────
# 4. build_stylesheet
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('QSS-стили')
class TestBuildStylesheet:
    @pytest.mark.smoke
    @pytest.mark.parametrize('name', ['light', 'dark'])
    def test_returns_string(self, name):
        qss = gui.build_stylesheet(name)

        assert isinstance(qss, str)
        assert len(qss) > 1000

    @pytest.mark.smoke
    @pytest.mark.parametrize('name', ['light', 'dark'])
    @pytest.mark.parametrize('key', [
        'bg', 'bg_alt', 'bg_elev', 'text', 'text_dim', 'border',
        'accent', 'accent_h', 'sel_bg', 'sel_fg', 'log_bg', 'log_fg',
    ])
    def test_contains_theme_colors(self, name, key):
        """Цвета из THEMES попадают в QSS (tree_* использует модель)."""
        assert gui.THEMES[name][key] in gui.build_stylesheet(name)

    @pytest.mark.parametrize('selector', [
        'QWidget', 'QMainWindow', 'QLabel#Heading', 'QLabel#Dim',
        'QFrame#Card', 'QLineEdit', 'QPushButton', 'QPushButton#Accent',
        'QToolBar', 'QTabWidget::pane', 'QTabBar::tab:selected',
        'QTreeView', 'QHeaderView::section', 'QPlainTextEdit',
        'QStatusBar', 'QMenuBar', 'QSplitter::handle',
        'QCheckBox::indicator:checked', 'QScrollBar:vertical',
    ])
    def test_expected_selectors_present(self, selector):
        assert selector in gui.build_stylesheet('light')

    def test_themes_produce_different_qss(self):
        assert gui.build_stylesheet('light') != gui.build_stylesheet('dark')

    def test_no_unresolved_placeholders(self):
        """В f-строке не должно остаться фигурных двойных скобок."""
        for name in gui.THEMES:
            qss = gui.build_stylesheet(name)
            assert '{{' not in qss
            assert '}}' not in qss
            assert 'None' not in qss

    def test_hover_and_focus_states_present(self):
        qss = gui.build_stylesheet('dark')

        assert 'QPushButton:hover' in qss
        assert 'QLineEdit:focus' in qss
        assert 'QTabBar::tab:hover' in qss

    def test_uses_accent_for_selection(self):
        qss = gui.build_stylesheet('dark')

        assert gui.THEMES['dark']['accent'] in qss


# ─────────────────────────────────────────────────────────────────
# 4b. Иконки QSS (галочка и стрелки) — рисуются в PNG
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Иконки QSS')
class TestQssIcons:
    """Qt не читает data-URI в QSS, поэтому иконки — файлы PNG.

    Проверяем, что они действительно создаются и что таблица стилей
    ссылается на существующие файлы: иначе галочка и стрелка ветки
    молча не рисуются.
    """

    def test_icons_are_created_and_readable(self, qapp, tmp_path,
                                            monkeypatch):
        monkeypatch.setattr(gui, 'qss_icon_dir', lambda: tmp_path)
        monkeypatch.setattr(gui, '_qss_icon_cache', {})

        icons = gui.build_qss_icons('light')

        assert set(icons) == {'check', 'chevron_down', 'chevron_right'}
        for key, value in icons.items():
            assert value.startswith('url("'), key
            path = Path(value[len('url("'):-len('")')])
            assert path.is_file(), key
            image = QImage(str(path))
            assert not image.isNull(), key
            assert image.size().width() > 0
            opaque = sum(1 for y in range(image.height())
                         for x in range(image.width())
                         if image.pixelColor(x, y).alpha() > 0)
            assert opaque > 0, f'{key}: картинка пустая'

    def test_check_mark_is_white(self, qapp, tmp_path, monkeypatch):
        """Галочка ложится на акцентную заливку, значит она светлая."""
        monkeypatch.setattr(gui, 'qss_icon_dir', lambda: tmp_path)
        monkeypatch.setattr(gui, '_qss_icon_cache', {})

        icons = gui.build_qss_icons('dark')
        image = QImage(icons['check'][len('url("'):-len('")')])

        colors = [image.pixelColor(x, y) for y in range(image.height())
                  for x in range(image.width())
                  if image.pixelColor(x, y).alpha() > 200]
        assert colors
        assert all(col.red() > 200 and col.green() > 200
                   and col.blue() > 200 for col in colors)

    def test_themes_have_own_files(self, qapp, tmp_path, monkeypatch):
        monkeypatch.setattr(gui, 'qss_icon_dir', lambda: tmp_path)
        monkeypatch.setattr(gui, '_qss_icon_cache', {})

        light = gui.build_qss_icons('light')
        dark = gui.build_qss_icons('dark')

        assert light['chevron_down'] != dark['chevron_down']
        assert Path(light['chevron_down'][5:-2]).name.startswith('light-')
        assert Path(dark['chevron_down'][5:-2]).name.startswith('dark-')

    def test_unknown_theme_falls_back_to_light(self, qapp, tmp_path,
                                               monkeypatch):
        monkeypatch.setattr(gui, 'qss_icon_dir', lambda: tmp_path)
        monkeypatch.setattr(gui, '_qss_icon_cache', {})

        icons = gui.build_qss_icons('не-такая')

        assert 'light-' in icons['check']

    def test_missing_icon_dir_does_not_break(self, qapp, monkeypatch):
        """Если писать некуда — QSS собирается, правила без картинок."""
        monkeypatch.setattr(gui, 'qss_icon_dir', lambda: None)
        monkeypatch.setattr(gui, '_qss_icon_cache', {})

        icons = gui.build_qss_icons('dark')

        assert icons == {'check': '', 'chevron_down': '',
                         'chevron_right': ''}
        assert 'QCheckBox::indicator:checked' in gui.build_stylesheet('dark')

    def test_stylesheet_has_no_data_uri(self):
        """data-URI Qt не читает — их не должно остаться в QSS."""
        for name in gui.THEMES:
            assert 'data:image' not in gui.build_stylesheet(name)

    def test_stylesheet_points_to_existing_files(self, qapp, tmp_path,
                                                 monkeypatch):
        monkeypatch.setattr(gui, 'qss_icon_dir', lambda: tmp_path)
        monkeypatch.setattr(gui, '_qss_icon_cache', {})

        qss = gui.build_stylesheet('light')

        for key, value in gui.build_qss_icons('light').items():
            assert value in qss, key
            path = Path(value[len('url("'):-len('")')])
            assert path.is_file(), key

    def test_branch_rules_for_both_states(self):
        """Раскрытая и закрытая ветка имеют свои стрелки."""
        for name in gui.THEMES:
            qss = gui.build_stylesheet(name)
            assert 'QTreeView::branch:has-children:closed' in qss
            assert 'QTreeView::branch:has-children:open' in qss

    def test_combo_arrow_uses_icon(self, qapp, tmp_path, monkeypatch):
        monkeypatch.setattr(gui, 'qss_icon_dir', lambda: tmp_path)
        monkeypatch.setattr(gui, '_qss_icon_cache', {})

        qss = gui.build_stylesheet('dark')

        assert gui.build_qss_icons('dark')['chevron_down'] in qss


# ─────────────────────────────────────────────────────────────────
# 4c. Ширина кнопок под их текст
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Кнопки диалогов')
class TestButtonWidths:
    """QSS задаёт min-width, и QStyleSheetStyle считает его шириной
    кнопки: длинные подписи обрезались. Ширину считаем по шрифту.
    """

    def test_text_width_covers_text(self, qapp, monkeypatch):
        from PySide6.QtWidgets import QPushButton

        button = QPushButton('Переместить в корень')
        needed = gui.button_text_width(button)
        text = QFontMetrics(button.font()).horizontalAdvance(button.text())

        assert needed >= text + 28   # отступы и рамка из QSS

    def test_text_width_of_empty_button(self, qapp):
        from PySide6.QtWidgets import QPushButton

        assert gui.button_text_width(QPushButton('')) == 0

    @pytest.mark.parametrize('theme', ['light', 'dark'])
    def test_offer_dialog_buttons_fit_their_text(self, qapp, theme):
        """Главный случай: кнопки окна «Новый файл в загрузках»."""
        from PySide6.QtWidgets import QMessageBox, QPushButton

        app = QApplication.instance()
        app.setStyleSheet(gui.build_stylesheet(theme))
        msg = QMessageBox()
        msg.setText('Обнаружен новый файл')
        buttons = [
            msg.addButton('Переместить в корень', QMessageBox.AcceptRole),
            msg.addButton('Выбрать папку…', QMessageBox.ActionRole),
            msg.addButton('Игнорировать', QMessageBox.RejectRole),
        ]

        gui.set_min_button_widths(msg, buttons)
        msg.show()
        app.processEvents()
        try:
            for button in buttons:
                needed = QFontMetrics(button.font()).horizontalAdvance(
                    button.text())
                assert button.minimumWidth() >= needed + 28
        finally:
            msg.close()

    def test_set_min_button_widths_tolerates_plain_objects(self, qapp):
        """Замены QMessageBox в тестах не обязаны быть виджетами."""
        from PySide6.QtGui import QFont

        class Fake:
            def __init__(self):
                self.widths = []

            def setMinimumWidth(self, value):
                self.widths.append(value)

        class FakeButton(Fake):
            def text(self):
                return 'Длинная подпись кнопки'

            def font(self):
                return QFont()

        class Token(Fake):
            """Ни текста, ни шрифта — как токен кнопки в заглушках."""

        button = FakeButton()

        gui.set_min_button_widths(None, [button, Token()])

        assert button.widths and button.widths[0] >= 150
        assert Token().widths == []

    def test_message_box_helper_sizes_standard_buttons(self, qapp):
        from PySide6.QtWidgets import QMessageBox, QPushButton

        msg = gui._message_box(None, QMessageBox.Critical, 'Ошибка',
                               'Уже существует: файл.gpx', QMessageBox.Ok)

        assert isinstance(msg, QMessageBox)
        assert msg.text() == 'Уже существует: файл.gpx'
        for button in msg.findChildren(QPushButton):
            assert button.minimumWidth() >= 60

    def test_event_filter_sizes_buttons_when_shown(self, qapp):
        """Общий механизм: фильтр ловит показ кнопки модального окна."""
        from PySide6.QtWidgets import QMessageBox, QPushButton

        app = QApplication.instance()
        app.setStyleSheet(gui.build_stylesheet('light'))
        sizer = gui.install_message_box_sizer(app)
        msg = QMessageBox()
        msg.setText('Обнаружен новый файл')
        buttons = [
            msg.addButton('Переместить в корень', QMessageBox.AcceptRole),
            msg.addButton('Выбрать папку…', QMessageBox.ActionRole),
            msg.addButton('Игнорировать', QMessageBox.RejectRole),
        ]
        for button in buttons:
            button.setMinimumWidth(1)

        msg.show()
        app.processEvents()
        try:
            for button in buttons:
                needed = QFontMetrics(button.font()).horizontalAdvance(
                    button.text())
                assert button.minimumWidth() >= needed + 28
        finally:
            msg.close()
            app.removeEventFilter(sizer)

    def test_event_filter_ignores_other_widgets(self, qapp):
        from PySide6.QtWidgets import QLabel

        app = QApplication.instance()
        sizer = gui.install_message_box_sizer(app)
        label = QLabel('обычная метка')

        app.sendEvent(label, QShowEvent())

        app.removeEventFilter(sizer)
        assert label.minimumWidth() == 0

