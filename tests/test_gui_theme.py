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

import allure
import pytest

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
