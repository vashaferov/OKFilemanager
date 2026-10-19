"""Конфиг ok-filemanager.

Файл config.toml лежит в системной папке конфигов:
  Linux:   $XDG_CONFIG_HOME/ok-filemanager/config.toml
  macOS:   ~/Library/Application Support/ok-filemanager/config.toml
  Windows: %APPDATA%\\ok-filemanager\\config.toml
"""

import os
import shutil
import sys
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None


DEFAULT_CONFIG = {
    'root_dir': '~',
    'downloads_dir': '~/Downloads',
    'watch_downloads': False,
    'theme': 'auto',
    'ftp_host': '',
    'ftp_port': 21,
    'ftp_user': '',
    'ftp_password': '',
    'ftp_path': '/',
    'ftp_readonly': False,
    # Базовый URL для ссылки на папку FTP: base_url/имя_папки.
    'base_url': 'https://tracks.lizaalert.ru',
    # Показывать ли окно подсказок (F1) при каждом запуске.
    # Управляется чек-боксом в самом окне подсказок.
    'help_visible_at_start': True,
    # Первый запуск сервиса. После первого запуска ставится в False,
    # чтобы не показывать окно настроек при каждом старте.
    'first_run': True,
    # --- состояние интерфейса (пишется при закрытии окна) ---
    # 'ШxВ+X+Y' главного окна; пустая строка — размеры по умолчанию.
    'window_geometry': '',
    # Состояние сплиттера «журнал / вкладки» (QSplitter.saveState).
    'splitter_state': '',
    # Ширина колонок дерева файлов: 'имя,размер,статус'.
    'tree_column_widths': '',
    # Показывать панель журнала (док внизу окна).
    'log_visible': True,
    # Высота панели журнала в пикселях.
    'log_dock_height': 180,
    # Показывать предсказуемые отказы (не тот формат, нет папки)
    # блокирующим окном вместо плашки над содержимым.
    'blocking_errors': False,
    # --- интеграция с OziExplorer 3.95 ---
    # Путь к oziexp.exe. Пусто — искать автоматически (PATH, типовые
    # папки установки, реестр Windows).
    'ozi_exe': '',
    # Путь к OziAPI.dll. Пусто — искать рядом с oziexp.exe.
    'ozi_api_dll': '',
    # Номер трека (1..75) для следующей загрузки трека (.plt, .gpx, .kml)
    # в открытое окно: у OziExplorer 75 треков, и загрузка в занятый номер
    # затирает его. После успешной загрузки номер увеличивается на число
    # загруженных треков, а спрашивать ли его каждый раз, решает
    # ozi_track_ask.
    'ozi_track_num': 1,
    'ozi_track_ask': True,
    # Что делать с уже загруженными точками при загрузке точек (.wpt,
    # .gpx, .kml): 'append' — добавить к ним, 'replace' — заменить набор.
    'ozi_wpt_mode': 'append',
    'ozi_wpt_ask': True,
}


# Логические настройки: при чтении приводятся к bool.
_BOOL_KEYS = ('watch_downloads', 'ftp_readonly', 'help_visible_at_start',
              'first_run', 'log_visible', 'blocking_errors',
              'ozi_track_ask', 'ozi_wpt_ask')

# Режимы загрузки точек (см. ok_helper.ozi.OZI_WPT_MODES).
_WPT_MODES = ('append', 'replace')

# Сколько треков у OziExplorer (см. ok_helper.ozi.OZI_TRACK_MAX).
_TRACK_MAX = 75


_LITERAL_KEYS = {
    'root_dir', 'downloads_dir',
    'ftp_host', 'ftp_user', 'ftp_password', 'ftp_path',
    'ozi_exe', 'ozi_api_dll',
}


def config_path() -> Path:
    if sys.platform == 'win32':
        base = Path(os.environ.get('APPDATA') or Path.home())
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library' / 'Application Support'
    else:
        xdg = os.environ.get('XDG_CONFIG_HOME')
        base = Path(xdg) if xdg else Path.home() / '.config'
    return base / 'ok-filemanager' / 'config.toml'


def legacy_config_path() -> Path:
    """Прежний путь конфига (приложение звалось ok-helper-tui).

    Нужен только для одноразового переноса настроек после переименования.
    Считается от config_path(), поэтому подмена пути в тестах не тянет
    реальный конфиг из %APPDATA%.
    """
    p = config_path()
    return p.parent.with_name('ok-helper-tui') / p.name


def _migrate_legacy_config(target: Path):
    """Копирует старый config.toml на новое место, если нового ещё нет."""
    legacy = legacy_config_path()
    if legacy == target or target.exists() or not legacy.exists():
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy, target)
    except OSError as e:
        print(f'[config] не перенести {legacy}: {e}', file=sys.stderr)


def _as_path(value: str) -> Path:
    try:
        return Path(value).expanduser().resolve()
    except (OSError, RuntimeError):
        return Path(value).expanduser()


def _toml_value(key: str, value) -> str:
    """Возвращает TOML-представление значения.

    Для путей (см. _LITERAL_KEYS) используем literal string —
    одинарные кавычки. Внутри них обратный слэш сохраняется как есть.
    """
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, int):
        return str(value)

    s = str(value)
    if key in _LITERAL_KEYS and "'" not in s and '\n' not in s:
        return f"'{s}'"

    esc = (s.replace('\\', '\\\\')
             .replace('"', '\\"')
             .replace('\n', '\\n')
             .replace('\r', '\\r')
             .replace('\t', '\\t'))
    return f'"{esc}"'


def _soft_parse(text: str) -> dict:
    """Аварийный построчный парсер TOML-подобного текста."""
    result: dict = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith('#') or s.startswith('['):
            continue
        if '=' not in s:
            continue
        key, _, val = s.partition('=')
        key = key.strip()
        val = val.strip()
        if ' #' in val:
            val = val.split(' #', 1)[0].rstrip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
            val = val[1:-1]
        result[key] = val
    return result


def _read_toml(p: Path):
    if tomllib is None:
        return None
    try:
        text = p.read_text(encoding='utf-8-sig')
    except OSError as e:
        print(f'[config] не прочитать {p}: {e}', file=sys.stderr)
        return None

    try:
        return tomllib.loads(text)
    except Exception as e:
        print(f'[config] {p}: {e}; пробую мягкий разбор',
              file=sys.stderr)

    soft = _soft_parse(text)
    return soft if soft else None


def load_config() -> dict:
    p = config_path()
    cfg = dict(DEFAULT_CONFIG)

    # Одноразовый перенос настроек из старого каталога ok-helper-tui.
    _migrate_legacy_config(p)

    if p.exists():
        data = _read_toml(p)
        if data is not None:
            for k in DEFAULT_CONFIG:
                if k in data:
                    cfg[k] = data[k]

    if cfg.get('theme') not in ('auto', 'dark', 'light'):
        cfg['theme'] = 'auto'

    try:
        cfg['ftp_port'] = int(cfg.get('ftp_port', 21))
    except (ValueError, TypeError):
        cfg['ftp_port'] = 21

    try:
        cfg['log_dock_height'] = int(cfg.get('log_dock_height', 180))
    except (ValueError, TypeError):
        cfg['log_dock_height'] = 180

    try:
        cfg['ozi_track_num'] = int(cfg.get('ozi_track_num', 1))
    except (ValueError, TypeError):
        cfg['ozi_track_num'] = 1
    cfg['ozi_track_num'] = min(max(cfg['ozi_track_num'], 1), _TRACK_MAX)

    if cfg.get('ozi_wpt_mode') not in _WPT_MODES:
        cfg['ozi_wpt_mode'] = DEFAULT_CONFIG['ozi_wpt_mode']

    for key in _BOOL_KEYS:
        cfg[key] = bool(cfg.get(key, DEFAULT_CONFIG[key]))

    for k in ('root_dir', 'downloads_dir', 'ftp_host', 'ftp_user',
              'ftp_password', 'ftp_path', 'base_url', 'window_geometry',
              'splitter_state', 'tree_column_widths',
              'ozi_exe', 'ozi_api_dll', 'ozi_wpt_mode'):
        cfg[k] = str(cfg.get(k, DEFAULT_CONFIG[k]))

    cfg['root_dir_resolved'] = _as_path(cfg['root_dir'])
    cfg['downloads_dir_resolved'] = _as_path(cfg['downloads_dir'])
    cfg['_path'] = p

    save_config(cfg)
    return cfg


def save_config(cfg: dict):
    p = cfg.get('_path') or config_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f'[config] не создать {p.parent}: {e}', file=sys.stderr)
        return

    try:
        port = int(cfg.get('ftp_port', 21))
    except (ValueError, TypeError):
        port = 21

    theme = cfg.get('theme', 'auto')
    if theme not in ('auto', 'dark', 'light'):
        theme = 'auto'

    bools = {key: bool(cfg.get(key, DEFAULT_CONFIG.get(key, False)))
             for key in _BOOL_KEYS}
    watch = bools['watch_downloads']
    readonly = bools['ftp_readonly']
    help_at_start = bools['help_visible_at_start']
    first_run = bools['first_run']
    log_visible = bools['log_visible']
    blocking_errors = bools['blocking_errors']
    try:
        log_dock_height = int(cfg.get('log_dock_height', 180))
    except (ValueError, TypeError):
        log_dock_height = 180
    if not 1 <= log_dock_height <= 10000:
        log_dock_height = 180
    window_geometry = str(cfg.get('window_geometry', ''))
    splitter_state = str(cfg.get('splitter_state', ''))
    tree_column_widths = str(cfg.get('tree_column_widths', ''))
    ozi_exe = str(cfg.get('ozi_exe', ''))
    ozi_api_dll = str(cfg.get('ozi_api_dll', ''))
    try:
        ozi_track_num = int(cfg.get('ozi_track_num', 1))
    except (ValueError, TypeError):
        ozi_track_num = 1
    ozi_track_num = min(max(ozi_track_num, 1), _TRACK_MAX)
    ozi_wpt_mode = str(cfg.get('ozi_wpt_mode',
                               DEFAULT_CONFIG['ozi_wpt_mode']))
    if ozi_wpt_mode not in _WPT_MODES:
        ozi_wpt_mode = DEFAULT_CONFIG['ozi_wpt_mode']
    ozi_track_ask = bools['ozi_track_ask']
    ozi_wpt_ask = bools['ozi_wpt_ask']

    lines = [
        '# Конфиг ok-filemanager',
        '#',
        '# root_dir        — корневая директория, открывается при запуске',
        '# downloads_dir   — папка загрузок для мониторинга',
        '# watch_downloads — true/false: предлагать переносить новые файлы',
        '# theme           — auto / dark / light',
        '#',
        '# Пути можно писать с ~ — это раскроется в домашнюю директорию.',
        '# Обратные слэши Windows пишутся без удвоения:',
        '#   root_dir = \'C:\\Users\\you\\Tracks\'',
        '',
        f'root_dir        = {_toml_value("root_dir", cfg.get("root_dir", "~"))}',
        f'downloads_dir   = {_toml_value("downloads_dir", cfg.get("downloads_dir", "~/Downloads"))}',
        f'watch_downloads = {_toml_value("watch_downloads", watch)}',
        f'theme           = {_toml_value("theme", theme)}',
        '',
        '# --- FTP ---',
        '# ftp_host      — адрес сервера',
        '# ftp_port      — порт (по умолчанию 21)',
        '# ftp_user      — логин',
        '# ftp_password  — пароль',
        '# ftp_path      — базовый путь на сервере',
        '# ftp_readonly  — true: режим эмуляции (чтение реальное,',
        '#                 изменения — только в журнал, без записи)',
        '# base_url      — базовый URL для ссылки на папку FTP',
        '#                 (ссылка = base_url/имя_папки)',
        '',
        f'ftp_host        = {_toml_value("ftp_host", cfg.get("ftp_host", ""))}',
        f'ftp_port        = {_toml_value("ftp_port", port)}',
        f'ftp_user        = {_toml_value("ftp_user", cfg.get("ftp_user", ""))}',
        f'ftp_password    = {_toml_value("ftp_password", cfg.get("ftp_password", ""))}',
        f'ftp_path        = {_toml_value("ftp_path", cfg.get("ftp_path", "/"))}',
        f'ftp_readonly    = {_toml_value("ftp_readonly", readonly)}',
        f'base_url        = {_toml_value("base_url", cfg.get("base_url", ""))}',
        '',
        '# --- OziExplorer ---',
        '# ozi_exe      — путь к oziexp.exe (пусто — искать автоматически:',
        '#                PATH, типовые папки установки, реестр Windows)',
        '# ozi_api_dll  — путь к OziAPI.dll (пусто — искать рядом с нашей',
        '#                программой, среди распакованных кнопкой',
        '#                «Распаковать OziAPI» и рядом с exe);',
        '#                DLL лежит в комплектном архиве assets/',
        '#                oziapi_dll.zip и сама не распаковывается — это',
        '#                делает кнопка в настройках;',
        '#                если DLL найдена, файл грузится в открытое окно',
        '#                через API (нужен ли запуск OziExplorer и когда он',
        '#                готов — решает программа), а без неё .map',
        '#                открывается запуском exe;',
        '# ozi_track_num — номер трека (1..75) для следующей загрузки',
        '#                трека (.plt, .gpx, .kml); у OziExplorer 75',
        '#                треков, загрузка в занятый номер затирает его,',
        '#                поэтому после успешной загрузки номер',
        '#                увеличивается (на число загруженных треков);',
        '# ozi_track_ask — спрашивать номер трека перед загрузкой',
        '#                (снимается галочкой «Запомнить» в диалоге);',
        '# ozi_wpt_mode  — append / replace: при загрузке точек (.wpt,',
        '#                .gpx, .kml) добавлять их к уже загруженным или',
        '#                заменять набор;',
        '# ozi_wpt_ask   — спрашивать режим перед загрузкой точек',
        '#                (снимается галочкой «Запомнить» в диалоге).',
        '',
        f'ozi_exe         = {_toml_value("ozi_exe", ozi_exe)}',
        f'ozi_api_dll     = {_toml_value("ozi_api_dll", ozi_api_dll)}',
        f'ozi_track_num   = {ozi_track_num}',
        f'ozi_track_ask   = {_toml_value("ozi_track_ask", ozi_track_ask)}',
        f'ozi_wpt_mode    = {_toml_value("ozi_wpt_mode", ozi_wpt_mode)}',
        f'ozi_wpt_ask     = {_toml_value("ozi_wpt_ask", ozi_wpt_ask)}',
        '',
        '# --- Окна при старте ---',
        '# help_visible_at_start — показывать окно подсказок (F1)',
        '#                         при каждом запуске сервиса;',
        '#                         переключается чек-боксом в самом окне.',
        '# first_run             — внутренний флаг: показывать ли',
        '#                         окно настроек при запуске.',
        '#                         Ставится автоматически.',
        '',
        f'help_visible_at_start = {_toml_value("help_visible_at_start", help_at_start)}',
        f'first_run             = {_toml_value("first_run", first_run)}',
        '',
        '# --- Состояние интерфейса ---',
        '# Пишется автоматически при закрытии окна; можно удалить —',
        '# тогда окно откроется с размерами по умолчанию.',
        '',
        f'window_geometry     = {_toml_value("window_geometry", window_geometry)}',
        f'splitter_state      = {_toml_value("splitter_state", splitter_state)}',
        f'tree_column_widths  = {_toml_value("tree_column_widths", tree_column_widths)}',
        f'log_visible         = {_toml_value("log_visible", log_visible)}',
        f'log_dock_height     = {log_dock_height}',
        f'blocking_errors     = {_toml_value("blocking_errors", blocking_errors)}',
        '',
    ]
    try:
        p.write_text('\n'.join(lines), encoding='utf-8')
    except OSError as e:
        print(f'[config] не записать {p}: {e}', file=sys.stderr)
