"""Тесты точки входа (main.py).

Функциональность: разбор аргумента `directory`, выбор директории из
конфига, проверки «не директория» и «PySide6 не установлен», запуск GUI.

GUI подменяется: run_gui только фиксирует вызов.

Группировка в Allure:
  epic    = ok-helper-tui
  feature = Точка входа
  story   = задаётся маркером @pytest.mark.story на каждом классе.
"""

import importlib.util
import sys
import types
from pathlib import Path

import allure
import pytest

import ok_helper.gui as gui


pytestmark = [
    pytest.mark.main,
    pytest.mark.epic('ok-helper-tui'),
    pytest.mark.feature('Точка входа'),
]


ROOT = Path(__file__).resolve().parent.parent


def load_main_module():
    """Загружает main.py из корня проекта.

    Сначала пробуем обычный импорт (так же, как при `python main.py`).
    Если под именем `main` уже импортирован чужой модуль, наш файл
    загружается напрямую по пути.
    """
    import importlib

    expected = (ROOT / 'main.py').resolve()
    module = sys.modules.get('main')
    if module is None:
        module = importlib.import_module('main')
    if Path(getattr(module, '__file__', '') or '.').resolve() == expected:
        return module

    name = 'ok_helper_main_entry'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / 'main.py')
        fallback = importlib.util.module_from_spec(spec)
        sys.modules[name] = fallback
        spec.loader.exec_module(fallback)
    return sys.modules[name]


@pytest.fixture
def app(tmp_path, monkeypatch):
    """main с подменённым конфигом и перехваченным run_gui."""
    module = load_main_module()
    root = tmp_path / 'tracks'
    root.mkdir()
    cfg = {
        'root_dir': str(root),
        'root_dir_resolved': root,
        'theme': 'light',
        '_path': tmp_path / 'config.toml',
    }
    calls = []
    loaded = []

    monkeypatch.setattr(module, 'load_config',
                        lambda: loaded.append(True) or cfg)
    monkeypatch.setattr(module, 'config_path',
                        lambda: tmp_path / 'ok-helper-tui' / 'config.toml')
    monkeypatch.setattr(gui, 'run_gui',
                        lambda directory, config: calls.append(
                            (directory, config)))
    monkeypatch.setattr(sys, 'argv', ['main.py'])

    return types.SimpleNamespace(module=module, cfg=cfg, calls=calls,
                                 loaded=loaded, root=root, tmp_path=tmp_path)


def run(app, *argv):
    sys.argv = ['main.py', *argv]
    app.module.main()


# ─────────────────────────────────────────────────────────────────
# 1. Выбор директории
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Выбор директории')
class TestDirectorySelection:
    @pytest.mark.smoke
    def test_directory_argument_is_used(self, app):
        run(app, str(app.root))

        assert app.calls == [(app.root.resolve(), app.cfg)]

    @pytest.mark.smoke
    def test_no_argument_uses_config_root(self, app):
        run(app)

        assert app.calls == [(app.root, app.cfg)]

    def test_config_is_loaded_once(self, app):
        run(app, str(app.root))

        assert app.loaded == [True]

    def test_tilde_is_expanded(self, app):
        run(app, '~')

        directory, _ = app.calls[0]
        assert directory == Path.home().resolve()
        assert directory.is_dir()

    def test_relative_path_is_resolved(self, app, monkeypatch):
        sub = app.root / 'sub'
        sub.mkdir()
        monkeypatch.chdir(app.root)

        run(app, 'sub')

        assert app.calls[0][0] == sub.resolve()

    def test_same_config_object_is_passed(self, app):
        run(app, str(app.root))

        assert app.calls[0][1] is app.cfg

    def test_run_gui_called_exactly_once(self, app):
        run(app, str(app.root))

        assert len(app.calls) == 1

    def test_directory_is_a_path_object(self, app):
        run(app, str(app.root))

        assert isinstance(app.calls[0][0], Path)


# ─────────────────────────────────────────────────────────────────
# 2. Ошибки: не директория
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Проверка директории')
class TestNotADirectory:
    def test_missing_path_exits(self, app, capsys):
        with pytest.raises(SystemExit) as exc:
            run(app, str(app.tmp_path / 'нет-такой'))

        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert 'Не директория' in captured.err
        assert app.calls == []

    def test_file_argument_exits(self, app, capsys):
        f = app.tmp_path / 'файл.txt'
        f.write_text('')

        with pytest.raises(SystemExit):
            run(app, str(f))

        assert 'Не директория' in capsys.readouterr().err
        assert app.calls == []

    def test_error_message_mentions_config_path(self, app, capsys):
        with pytest.raises(SystemExit):
            run(app, str(app.tmp_path / 'нет-такой'))

        assert 'Проверьте root_dir' in capsys.readouterr().err

    def test_missing_config_root_exits(self, app, capsys):
        app.cfg['root_dir_resolved'] = app.tmp_path / 'нет-такой'

        with pytest.raises(SystemExit):
            run(app)

        assert 'Не директория' in capsys.readouterr().err

    def test_gui_not_started_without_directory(self, app):
        app.cfg['root_dir_resolved'] = app.tmp_path / 'нет-такой'

        with pytest.raises(SystemExit):
            run(app)

        assert app.calls == []


# ─────────────────────────────────────────────────────────────────
# 3. Отсутствующий PySide6
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Отсутствующий PySide6')
class TestMissingPySide6:
    @pytest.fixture
    def no_pyside(self, monkeypatch):
        # sys.modules[name] = None → `import PySide6` бросает ImportError.
        monkeypatch.setitem(sys.modules, 'PySide6', None)

    @pytest.mark.smoke
    def test_exits_with_error(self, app, no_pyside, capsys):
        with pytest.raises(SystemExit) as exc:
            run(app, str(app.root))

        assert exc.value.code == 1
        assert 'PySide6 не установлен' in capsys.readouterr().err

    def test_explains_how_to_install(self, app, no_pyside, capsys):
        with pytest.raises(SystemExit):
            run(app, str(app.root))

        err = capsys.readouterr().err
        assert 'Установите зависимости' in err
        assert 'pip install -r requirements.txt' in err

    def test_gui_not_started(self, app, no_pyside):
        with pytest.raises(SystemExit):
            run(app, str(app.root))

        assert app.calls == []

    def test_check_happens_after_directory_check(self, app, no_pyside,
                                                capsys):
        """Сначала сообщение о директории, а не об отсутствии PySide6."""
        with pytest.raises(SystemExit):
            run(app, str(app.tmp_path / 'нет-такой'))

        assert 'Не директория' in capsys.readouterr().err


# ─────────────────────────────────────────────────────────────────
# 4. Аргументы командной строки
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Параметры командной строки')
class TestCli:
    def test_help_exits_zero(self, app, capsys):
        with pytest.raises(SystemExit) as exc:
            run(app, '--help')

        assert exc.value.code == 0
        assert 'ok-helper' in capsys.readouterr().out

    def test_help_describes_directory_argument(self, app, capsys):
        with pytest.raises(SystemExit):
            run(app, '--help')

        assert 'Начальная' in capsys.readouterr().out

    def test_unknown_option_fails(self, app, capsys):
        with pytest.raises(SystemExit) as exc:
            run(app, '--нет-такого')

        assert exc.value.code == 2

    def test_only_one_positional_argument(self, app, capsys):
        with pytest.raises(SystemExit) as exc:
            run(app, str(app.root), 'лишний')

        assert exc.value.code == 2
        assert app.calls == []
