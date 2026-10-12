"""Тесты конфигурации (config.py).

Функциональность: config_path(), load_config(), save_config(),
_toml_value(), _soft_parse(), _read_toml(), _as_path().

Все операции идут в tmp_path — настоящий config.toml не читается и
не перезаписывается.

Группировка в Allure:
  epic    = ok-helper-tui
  feature = Конфигурация
  story   = задаётся маркером @pytest.mark.story на каждом классе.
"""

from pathlib import Path

import allure
import pytest

import config as cfg_mod
from config import (
    DEFAULT_CONFIG,
    _as_path,
    _read_toml,
    _soft_parse,
    _toml_value,
    config_path,
    load_config,
    save_config,
)


pytestmark = [
    pytest.mark.config,
    pytest.mark.epic('ok-helper-tui'),
    pytest.mark.feature('Конфигурация'),
]


@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    """Перенаправляет config_path() в tmp_path."""
    path = tmp_path / 'ok-helper-tui' / 'config.toml'
    monkeypatch.setattr(cfg_mod, 'config_path', lambda: path)
    return path


# ─────────────────────────────────────────────────────────────────
# 1. config_path — путь к файлу по ОС
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Путь к конфигу по ОС')
class TestConfigPath:
    @pytest.mark.smoke
    def test_windows_uses_appdata(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cfg_mod.sys, 'platform', 'win32')
        monkeypatch.setenv('APPDATA', str(tmp_path / 'AppData'))

        assert config_path() == \
            tmp_path / 'AppData' / 'ok-helper-tui' / 'config.toml'

    def test_windows_without_appdata_falls_back_to_home(self, monkeypatch):
        monkeypatch.setattr(cfg_mod.sys, 'platform', 'win32')
        monkeypatch.delenv('APPDATA', raising=False)

        assert config_path() == \
            Path.home() / 'ok-helper-tui' / 'config.toml'

    @pytest.mark.smoke
    def test_macos_application_support(self, monkeypatch):
        monkeypatch.setattr(cfg_mod.sys, 'platform', 'darwin')

        assert config_path() == (Path.home() / 'Library'
                                 / 'Application Support'
                                 / 'ok-helper-tui' / 'config.toml')

    def test_linux_uses_xdg_config_home(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cfg_mod.sys, 'platform', 'linux')
        monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'xdg'))

        assert config_path() == \
            tmp_path / 'xdg' / 'ok-helper-tui' / 'config.toml'

    def test_linux_without_xdg_uses_dot_config(self, monkeypatch):
        monkeypatch.setattr(cfg_mod.sys, 'platform', 'linux')
        monkeypatch.delenv('XDG_CONFIG_HOME', raising=False)

        assert config_path() == \
            Path.home() / '.config' / 'ok-helper-tui' / 'config.toml'

    def test_ends_with_toml(self):
        assert config_path().name == 'config.toml'

    def test_parent_directory_name(self):
        assert config_path().parent.name == 'ok-helper-tui'


# ─────────────────────────────────────────────────────────────────
# 2. _toml_value — сериализация значений
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Сериализация TOML')
class TestTomlValue:
    @pytest.mark.parametrize('value,expected', [
        (True, 'true'), (False, 'false'),
    ])
    def test_bool(self, value, expected):
        assert _toml_value('ftp_readonly', value) == expected

    def test_int(self):
        assert _toml_value('ftp_port', 2121) == '2121'

    @pytest.mark.smoke
    def test_path_uses_literal_single_quotes(self):
        """README: пути пишутся в одинарных кавычках, чтобы '\\' не
        приходилось удваивать."""
        value = _toml_value('root_dir', r'C:\Users\you\Tracks')

        assert value == r"'C:\Users\you\Tracks'"
        assert '\\\\' not in value

    @pytest.mark.parametrize('key', [
        'root_dir', 'downloads_dir',
        'ftp_host', 'ftp_user', 'ftp_password', 'ftp_path',
    ])
    def test_all_literal_keys_use_single_quotes(self, key):
        assert _toml_value(key, r'a\b').startswith("'")

    def test_literal_key_with_quote_falls_back_to_escaped(self):
        value = _toml_value('root_dir', "it's here")

        assert value.startswith('"')
        assert value == '"it\'s here"'

    def test_literal_key_with_newline_falls_back(self):
        value = _toml_value('root_dir', 'a\nb')

        assert value == '"a\\nb"'

    def test_non_literal_key_escapes_backslashes(self):
        value = _toml_value('theme', r'a\b')

        assert value == '"a\\\\b"'

    def test_non_literal_key_escapes_quotes(self):
        assert _toml_value('theme', 'say "hi"') == '"say \\"hi\\""'

    @pytest.mark.parametrize('raw,expected', [
        ('\r', '\\r'), ('\t', '\\t'),
    ])
    def test_control_characters_escaped(self, raw, expected):
        assert _toml_value('theme', raw) == f'"{expected}"'

    def test_result_is_parseable_toml(self):
        import tomllib

        for key, value in (
            ('root_dir', r'C:\Users\you\Tracks'),
            ('theme', 'auto'),
            ('ftp_port', 21),
            ('ftp_readonly', False),
        ):
            text = f'k = {_toml_value(key, value)}'
            assert tomllib.loads(text)['k'] == value


# ─────────────────────────────────────────────────────────────────
# 3. _soft_parse — аварийный разбор
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Мягкий разбор TOML')
class TestSoftParse:
    @pytest.mark.smoke
    def test_simple_pairs(self):
        result = _soft_parse("root_dir = 'C:\\Tracks'\ntheme = 'dark'\n")

        assert result == {'root_dir': 'C:\\Tracks', 'theme': 'dark'}

    def test_comments_and_sections_skipped(self):
        text = (
            '# комментарий\n'
            '\n'
            '[section]\n'
            'theme = "light"\n'
        )
        assert _soft_parse(text) == {'theme': 'light'}

    def test_lines_without_equals_skipped(self):
        assert _soft_parse('мусор\nkey = 1\n') == {'key': '1'}

    def test_inline_comment_stripped(self):
        assert _soft_parse('ftp_port = 2121 # порт\n') == {'ftp_port': '2121'}

    def test_double_quoted_value_unwrapped(self):
        assert _soft_parse('ftp_path = "/upload"\n') == {'ftp_path': '/upload'}

    def test_single_quoted_value_unwrapped(self):
        assert _soft_parse("ftp_path = '/upload'\n") == {'ftp_path': '/upload'}

    def test_empty_text(self):
        assert _soft_parse('') == {}

    def test_value_with_equals_sign(self):
        assert _soft_parse('ftp_password = a=b\n') == {'ftp_password': 'a=b'}

    def test_values_are_strings(self):
        result = _soft_parse('ftp_port = 21\nftp_readonly = false\n')

        assert result == {'ftp_port': '21', 'ftp_readonly': 'false'}
        assert all(isinstance(v, str) for v in result.values())


# ─────────────────────────────────────────────────────────────────
# 4. _read_toml
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Чтение TOML')
class TestReadToml:
    def test_missing_file_returns_none(self, tmp_path, capsys):
        assert _read_toml(tmp_path / 'nope.toml') is None
        assert 'не прочитать' in capsys.readouterr().err

    @pytest.mark.smoke
    def test_valid_file(self, tmp_path):
        p = tmp_path / 'config.toml'
        p.write_text("theme = 'dark'\nftp_port = 2121\n", encoding='utf-8')

        assert _read_toml(p) == {'theme': 'dark', 'ftp_port': 2121}

    def test_broken_file_falls_back_to_soft_parse(self, tmp_path, capsys):
        """Ошибка TOML (одинарные кавычки с '\\') — мягкий разбор."""
        p = tmp_path / 'config.toml'
        p.write_text('root_dir = "C:\\Users\\you"\ntheme = "dark"\n',
                     encoding='utf-8')

        data = _read_toml(p)
        assert data is not None
        assert data['theme'] == 'dark'
        assert 'пробую мягкий разбор' in capsys.readouterr().err

    def test_broken_and_unparseable_file_returns_none(self, tmp_path, capsys):
        """Ни tomllib, ни мягкий разбор не дали ни одной пары."""
        p = tmp_path / 'config.toml'
        p.write_text('мусор без знака равно\n', encoding='utf-8')

        assert _read_toml(p) is None
        capsys.readouterr()

    def test_utf8_bom_accepted(self, tmp_path):
        p = tmp_path / 'config.toml'
        p.write_bytes("theme = 'dark'\n".encode('utf-8-sig'))

        assert _read_toml(p) == {'theme': 'dark'}


# ─────────────────────────────────────────────────────────────────
# 5. _as_path
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('_as_path')
class TestAsPath:
    def test_returns_resolved_path(self, tmp_path):
        assert _as_path(str(tmp_path)) == tmp_path.resolve()

    def test_expands_user(self):
        assert _as_path('~') == Path.home().resolve()

    def test_falls_back_when_resolve_fails(self, monkeypatch, tmp_path):
        def boom(self, *a, **kw):
            raise OSError('нет доступа')

        monkeypatch.setattr(Path, 'resolve', boom)

        result = _as_path(str(tmp_path / 'x'))
        assert result == Path(str(tmp_path / 'x')).expanduser()

    def test_falls_back_on_runtime_error(self, monkeypatch, tmp_path):
        def boom(self, *a, **kw):
            raise RuntimeError('цикл ссылок')

        monkeypatch.setattr(Path, 'resolve', boom)

        assert _as_path(str(tmp_path / 'x')).name == 'x'


# ─────────────────────────────────────────────────────────────────
# 6. load_config — значения по умолчанию и валидация
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('load_config')
class TestLoadConfig:
    @pytest.mark.smoke
    def test_defaults_when_file_absent(self, cfg_file):
        cfg = load_config()

        assert cfg['theme'] == 'auto'
        assert cfg['ftp_port'] == 21
        assert cfg['ftp_path'] == '/'
        assert cfg['ftp_readonly'] is False
        assert cfg['watch_downloads'] is False
        assert cfg['help_visible_at_start'] is True
        assert cfg['first_run'] is True

    @pytest.mark.smoke
    def test_ui_state_defaults(self, cfg_file):
        """Новые ключи состояния интерфейса есть даже в старом конфиге."""
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text("theme = 'dark'\n", encoding='utf-8')

        cfg = load_config()

        assert cfg['window_geometry'] == ''
        assert cfg['splitter_state'] == ''
        assert cfg['tree_column_widths'] == ''
        assert cfg['log_visible'] is True
        assert cfg['blocking_errors'] is False

    def test_ui_state_is_read_from_file(self, cfg_file):
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text(
            "window_geometry = '1000,700,10,20'\n"
            "splitter_state = 'aabb'\n"
            "tree_column_widths = '300,80,120'\n"
            "log_visible = false\n"
            "blocking_errors = true\n",
            encoding='utf-8')

        cfg = load_config()

        assert cfg['window_geometry'] == '1000,700,10,20'
        assert cfg['splitter_state'] == 'aabb'
        assert cfg['tree_column_widths'] == '300,80,120'
        assert cfg['log_visible'] is False
        assert cfg['blocking_errors'] is True

    def test_creates_config_file(self, cfg_file):
        assert not cfg_file.exists()
        load_config()

        assert cfg_file.exists()
        assert 'root_dir' in cfg_file.read_text(encoding='utf-8')

    def test_resolved_paths_are_paths(self, cfg_file):
        cfg = load_config()

        assert isinstance(cfg['root_dir_resolved'], Path)
        assert isinstance(cfg['downloads_dir_resolved'], Path)
        assert cfg['root_dir_resolved'] == Path(cfg['root_dir']).expanduser().resolve()

    def test_path_of_config_is_exposed(self, cfg_file):
        assert load_config()['_path'] == cfg_file

    @pytest.mark.smoke
    def test_reads_existing_values(self, cfg_file):
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text(
            "root_dir = '/tmp/tracks'\n"
            "downloads_dir = '/tmp/dl'\n"
            "watch_downloads = true\n"
            "theme = 'dark'\n"
            "ftp_host = 'ftp.example.com'\n"
            "ftp_port = 2121\n"
            "ftp_user = 'user'\n"
            "ftp_password = 'secret'\n"
            "ftp_path = '/upload'\n"
            "ftp_readonly = true\n"
            "help_visible_at_start = false\n"
            "first_run = false\n",
            encoding='utf-8',
        )

        cfg = load_config()

        assert cfg['theme'] == 'dark'
        assert cfg['ftp_host'] == 'ftp.example.com'
        assert cfg['ftp_port'] == 2121
        assert cfg['ftp_password'] == 'secret'
        assert cfg['ftp_readonly'] is True
        assert cfg['watch_downloads'] is True
        assert cfg['help_visible_at_start'] is False
        assert cfg['first_run'] is False

    @pytest.mark.parametrize('raw,expected', [
        ('neon', 'auto'),
        ('DARK', 'auto'),
        ('', 'auto'),
        ('light', 'light'),
        ('dark', 'dark'),
        ('auto', 'auto'),
    ])
    def test_theme_whitelist(self, cfg_file, raw, expected):
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text(f"theme = '{raw}'\n", encoding='utf-8')

        assert load_config()['theme'] == expected

    @pytest.mark.parametrize('raw,expected', [
        ('abc', 21),
        ('0', 0),
        ('-5', -5),
        ('2121', 2121),
        ('21.5', 21),          # int('21.5') → ValueError → 21
    ])
    def test_bad_port_is_coerced(self, cfg_file, raw, expected):
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text(f'ftp_port = "{raw}"\n', encoding='utf-8')

        assert load_config()['ftp_port'] == expected

    def test_int_port_stays_int(self, cfg_file):
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text('ftp_port = 2121\n', encoding='utf-8')

        port = load_config()['ftp_port']
        assert port == 2121
        assert isinstance(port, int)

    @pytest.mark.parametrize('key', [
        'root_dir', 'downloads_dir',
        'ftp_host', 'ftp_user', 'ftp_password', 'ftp_path',
    ])
    def test_string_keys_are_strings(self, cfg_file, key):
        assert isinstance(load_config()[key], str)

    def test_unknown_keys_ignored(self, cfg_file):
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text("unknown_key = 'x'\ntheme = 'dark'\n",
                            encoding='utf-8')

        cfg = load_config()
        assert 'unknown_key' not in cfg
        assert cfg['theme'] == 'dark'

    def test_all_default_keys_present(self, cfg_file):
        cfg = load_config()

        for key in DEFAULT_CONFIG:
            assert key in cfg

    def test_saved_file_is_valid_toml(self, cfg_file):
        import tomllib

        load_config()
        data = tomllib.loads(cfg_file.read_text(encoding='utf-8'))

        assert data['theme'] == 'auto'
        assert data['ftp_port'] == 21


# ─────────────────────────────────────────────────────────────────
# 7. save_config
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('save_config')
class TestSaveConfig:
    @pytest.mark.smoke
    def test_round_trip_keeps_windows_path(self, cfg_file):
        """Главная причина одинарных кавычек: '\\' в пути не удваивается."""
        win_path = r'C:\Users\you\Tracks'
        cfg = dict(DEFAULT_CONFIG)
        cfg.update({'_path': cfg_file, 'root_dir': win_path,
                    'theme': 'dark', 'ftp_port': 2121})

        save_config(cfg)
        text = cfg_file.read_text(encoding='utf-8')

        assert f"root_dir        = '{win_path}'" in text
        assert load_config()['root_dir'] == win_path

    def test_round_trip_all_fields(self, cfg_file):
        cfg = dict(DEFAULT_CONFIG)
        cfg.update({
            '_path': cfg_file,
            'root_dir': '/tmp/tracks',
            'downloads_dir': '/tmp/dl',
            'watch_downloads': True,
            'theme': 'light',
            'ftp_host': 'h',
            'ftp_port': 2121,
            'ftp_user': 'u',
            'ftp_password': 'p@ss "word"',
            'ftp_path': '/upload',
            'ftp_readonly': True,
            'help_visible_at_start': False,
            'first_run': False,
        })

        save_config(cfg)
        again = load_config()

        for key in ('root_dir', 'downloads_dir', 'watch_downloads', 'theme',
                    'ftp_host', 'ftp_port', 'ftp_user', 'ftp_password',
                    'ftp_path', 'ftp_readonly', 'help_visible_at_start',
                    'first_run'):
            assert again[key] == cfg[key], key

    def test_ui_state_round_trip(self, cfg_file):
        """Состояние интерфейса переживает запись и чтение конфига."""
        cfg = dict(DEFAULT_CONFIG)
        cfg.update({
            '_path': cfg_file,
            'window_geometry': '1000,700,10,20',
            'splitter_state': 'aabbcc',
            'tree_column_widths': '300,80,120',
            'log_visible': False,
            'blocking_errors': True,
        })

        save_config(cfg)
        again = load_config()

        for key in ('window_geometry', 'splitter_state',
                    'tree_column_widths'):
            assert again[key] == cfg[key], key
        assert again['log_visible'] is False
        assert again['blocking_errors'] is True

    def test_invalid_theme_is_written_as_auto(self, cfg_file):
        cfg = dict(DEFAULT_CONFIG)
        cfg.update({'_path': cfg_file, 'theme': 'neon'})

        save_config(cfg)

        assert 'theme           = "auto"' in \
            cfg_file.read_text(encoding='utf-8')

    def test_invalid_port_is_written_as_21(self, cfg_file):
        cfg = dict(DEFAULT_CONFIG)
        cfg.update({'_path': cfg_file, 'ftp_port': 'abc'})

        save_config(cfg)

        assert 'ftp_port        = 21' in cfg_file.read_text(encoding='utf-8')

    def test_creates_missing_directories(self, tmp_path):
        cfg = dict(DEFAULT_CONFIG)
        cfg['_path'] = tmp_path / 'a' / 'b' / 'config.toml'

        save_config(cfg)

        assert cfg['_path'].exists()

    def test_header_comment_present(self, cfg_file):
        save_config({'_path': cfg_file})

        text = cfg_file.read_text(encoding='utf-8')
        assert text.startswith('# Конфиг ok-helper-tui')
        assert '# --- FTP ---' in text
        assert '# --- Окна при старте ---' in text

    def test_mkdir_failure_does_not_raise(self, tmp_path, capsys):
        blocker = tmp_path / 'blocker'
        blocker.write_text('я файл, а не папка', encoding='utf-8')
        cfg = dict(DEFAULT_CONFIG)
        cfg['_path'] = blocker / 'config.toml'

        save_config(cfg)          # не должно бросить исключение

        assert 'не создать' in capsys.readouterr().err

    def test_write_failure_does_not_raise(self, cfg_file, monkeypatch,
                                         capsys):
        def boom(self, *a, **kw):
            raise OSError('диск переполнен')

        monkeypatch.setattr(Path, 'write_text', boom)

        save_config({'_path': cfg_file})   # не должно бросить исключение

        assert 'не записать' in capsys.readouterr().err

    def test_default_path_used_when_missing(self, cfg_file, monkeypatch):
        """Без '_path' берётся config_path()."""
        monkeypatch.setattr(cfg_mod, 'config_path', lambda: cfg_file)

        save_config({'theme': 'dark'})

        assert 'dark' in cfg_file.read_text(encoding='utf-8')

    def test_written_booleans_are_toml_bools(self, cfg_file):
        import tomllib

        cfg = dict(DEFAULT_CONFIG)
        cfg.update({'_path': cfg_file, 'ftp_readonly': True,
                    'watch_downloads': False})

        save_config(cfg)
        data = tomllib.loads(cfg_file.read_text(encoding='utf-8'))

        assert data['ftp_readonly'] is True
        assert data['watch_downloads'] is False
