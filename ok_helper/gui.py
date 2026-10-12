"""GUI-версия ok-helper на PySide6 (Qt 6).

Полностью заменяет tkinter-версию. Логика анализа имён и FTP берётся
из существующих модулей ok_helper.*.

Особенности:
  • современный вид: Fusion-стиль Qt с QSS, скруглённые панели,
    акцентные кнопки, тонкие заголовки, hover-эффекты;
  • светлая / тёмная / авто тема (по системной через реестр Windows);
  • рекурсивное дерево файлов с ленивой загрузкой;
  • пометки ошибок и «залито на FTP» прямо в списке;
  • длительные операции FTP — в фоновых потоках, GUI не блокируется;
  • модель-представление — корректная работа с большими папками.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from PySide6.QtCore import (
    QEvent, QModelIndex, QObject, QPointF, QSize, Qt, QTimer, QUrl,
    Signal, Slot,
)
from PySide6.QtGui import (
    QAction, QColor, QDesktopServices, QFontMetrics, QIcon, QImage,
    QKeySequence, QPainter, QPen, QStandardItem, QStandardItemModel,
    QTextBlockFormat, QTextCharFormat, QTextCursor, QTextOption,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDockWidget, QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu,
    QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy, QSplitter,
    QStyle, QToolButton, QStatusBar, QTabWidget, QToolBar, QTreeView,
    QVBoxLayout, QWidget,
)

from config import config_path, save_config
from ok_helper.analyzer import (
    analyze_file, human_size, normalize_callsign, sanitize_callsign,
    validate_new_folder_name,
)
from ok_helper.clipboard import copy_to_clipboard
from ok_helper.ftp import (
    delete_file, find_target_folder, is_uploadable, list_folder,
    upload_extensions_hint, upload_file, upload_reason_for,
    validate_folder_name, validate_upload_target,
)
from ok_helper.handlers import DirHandler, DownloadsHandler
from ok_helper.translit import transliterate

from watchdog.observers import Observer


STUB_TEXT = '(загрузка…)'
ROLE_PATH = Qt.UserRole + 1
ROLE_FTP_NAME = Qt.UserRole + 2

# Ключи ввода-вывода состояния интерфейса в конфиге.
UI_STATE_KEYS = ('window_geometry', 'splitter_state', 'tree_column_widths',
                 'log_visible')

# Отступ журнала под отметку времени: продолжение длинной записи
# переносится с этим отступом, а не под цифрами времени.
LOG_INDENT = 62

# Периодическая подстраховка к watchdog: события файловой системы могут
# не дойти (сетевой диск, OneDrive, антивирус, сон машины), поэтому
# папку загрузок дополнительно перечитываем по таймеру.
DOWNLOAD_POLL_MS = 3000

# Запас к ширине текста кнопки: внутренние отступы (6px 14px) и рамка
# из таблицы стилей плюс небольшой люфт под системный шрифт.
BUTTON_WIDTH_MARGIN = 40


def _ui_geometry(width: int, height: int, x: int, y: int) -> str:
    return f'{int(width)},{int(height)},{int(x)},{int(y)}'


def _parse_ui_geometry(text: str) -> Optional[Tuple[int, int, int, int]]:
    """Разбирает 'ШxВ+X+Y', записанную _ui_geometry.

    Возвращает None, если строка пустая или испорченная, чтобы окно
    открылось с размерами по умолчанию и не потерялось за экраном.
    """
    if not text or text.count(',') != 3:
        return None
    try:
        width, height, x, y = (int(part) for part in text.split(','))
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height, x, y


def _parse_ui_columns(text: str) -> Optional[Tuple[int, int, int]]:
    """Разбирает ширины колонок дерева 'имя,размер,статус'."""
    if not text or text.count(',') != 2:
        return None
    try:
        widths = tuple(int(part) for part in text.split(','))
    except ValueError:
        return None
    if any(width < 0 for width in widths):
        return None
    return widths  # type: ignore[return-value]


def _std_icon(sp: QStyle.StandardPixmap) -> QIcon:
    """Иконка из набора стиля Qt.

    Своих ресурсов в проекте нет, поэтому берём системные глифы: они
    уже многоразмерные и согласованы с платформой, без лишних
    зависимостей вроде QtSvg.
    """
    style = QApplication.style()
    if style is None:
        return QIcon()
    return style.standardIcon(sp)


# ====================== иконки для QSS ======================

# QSS берёт картинки только по имени файла: data-URI Qt не читает
# (QImageReader отвечает «File not found»), поэтому галочка и стрелки
# рисуются в PNG при старте и подставляются в таблицу стилей путями.
QSS_ICON_SIZE = 16
_qss_icon_cache: Dict[str, Dict[str, str]] = {}
_qss_icon_dir: Optional[Path] = None


def _icon_candidates() -> List[Path]:
    """Куда можно положить сгенерированные иконки, по порядку."""
    base = config_path().parent / 'icons'
    temp = Path(tempfile.gettempdir()) / 'ok-helper-icons'
    return [base, temp]


def qss_icon_dir() -> Optional[Path]:
    """Каталог для иконок QSS: первый, в который удалось записать.

    Рядом с конфигом может не быть прав на запись (например, в
    песочнице) — тогда берём временный каталог. None означает, что
    писать некуда: правила с картинками просто не применятся.
    """
    global _qss_icon_dir
    if _qss_icon_dir is not None:
        return _qss_icon_dir
    for candidate in _icon_candidates():
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        _qss_icon_dir = candidate
        return candidate
    return None


def _render_icon_png(path: Path, size: int, draw) -> bool:
    """Рисует иконку в PNG. False — если записать не удалось."""
    try:
        image = QImage(size, size, QImage.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            draw(painter)
        finally:
            painter.end()
        return bool(image.save(str(path), 'PNG'))
    except Exception:
        return False


def _icon_url(path: Path) -> str:
    """Путь для QSS: url() требует прямые слэши и кавычки."""
    return 'url("{}")'.format(path.as_posix())


def _pen(color: str, width: float) -> QPen:
    pen = QPen(QColor(color))
    pen.setWidthF(width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def build_qss_icons(theme: str) -> Dict[str, str]:
    """Готовит PNG-иконки для темы и возвращает их QSS-пути.

    Ключи: check, chevron_down, chevron_right. При ошибке записи ключ
    получает пустую строку: правило с пустым image просто не
    применяется, интерфейс не ломается.
    """
    theme_name = theme if theme in ('light', 'dark') else 'light'
    cached = _qss_icon_cache.get(theme_name)
    if cached is not None:
        return cached

    # Рисовать можно только при живом QApplication. Без него отдаём
    # пустые пути: таблицу стилей всё равно собирать безопасно.
    if QApplication.instance() is None:
        return {'check': '', 'chevron_down': '', 'chevron_right': ''}

    colors = THEMES[theme_name]
    dim = colors['text_dim']
    on_accent = '#ffffff'
    size = QSS_ICON_SIZE
    directory = qss_icon_dir()

    def draw_check(painter):
        painter.setPen(_pen(on_accent, 2.0))
        painter.drawPolyline([QPointF(3.5, 8.5), QPointF(6.5, 11.5),
                              QPointF(12.5, 4.5)])

    def draw_chevron_down(painter):
        painter.setPen(_pen(dim, 1.8))
        painter.drawPolyline([QPointF(4, 6.5), QPointF(8, 10.5),
                              QPointF(12, 6.5)])

    def draw_chevron_right(painter):
        painter.setPen(_pen(dim, 1.8))
        painter.drawPolyline([QPointF(6.5, 4), QPointF(10.5, 8),
                              QPointF(6.5, 12)])

    draw_fns = {
        'check.png': draw_check,
        'chevron-down.png': draw_chevron_down,
        'chevron-right.png': draw_chevron_right,
    }
    paths: Dict[str, str] = {}
    for filename, draw in draw_fns.items():
        if directory is None:
            paths[filename] = ''
            continue
        target = directory / f'{theme_name}-{filename}'
        # Уже нарисовано (например, темой по умолчанию при старте) —
        # повторно не пишем: build_stylesheet зовут на каждое F10.
        if target.is_file() and target.stat().st_size > 0:
            paths[filename] = str(target)
            continue
        if _render_icon_png(target, size, draw):
            paths[filename] = str(target)
        else:
            paths[filename] = ''

    result = {
        'check': _icon_url(Path(paths['check.png']))
        if paths['check.png'] else '',
        'chevron_down': _icon_url(Path(paths['chevron-down.png']))
        if paths['chevron-down.png'] else '',
        'chevron_right': _icon_url(Path(paths['chevron-right.png']))
        if paths['chevron-right.png'] else '',
    }
    _qss_icon_cache[theme_name] = result
    return result


def button_text_width(button) -> int:
    """Минимальная ширина кнопки, при которой её текст помещается.

    В QSS у нас задан min-width, а QStyleSheetStyle считает его не
    нижней границей, а самой шириной кнопки: текст длиннее отведённого
    места обрезается. Поэтому ширину считаем по метрикам шрифта с
    запасом на внутренние отступы и рамку из таблицы стилей.
    """
    text = button.text()
    if not text:
        return 0
    font = getattr(button, 'font', None)
    if not callable(font):
        return 0
    metrics = QFontMetrics(font())
    return metrics.horizontalAdvance(text) + BUTTON_WIDTH_MARGIN


def set_min_button_widths(box, buttons=None):
    """Задаёт кнопкам ширину по их тексту.

    Нужно для QMessageBox: там кнопки добавляются по одной и не
    получают подсказку по размеру, поэтому длинные подписи вроде
    «Переместить в корень» обрезались.

    buttons можно передать явно — тогда обход виджетов не нужен (и
    работает с заменами QMessageBox в тестах).
    """
    if buttons is None:
        finder = getattr(box, 'findChildren', None)
        if finder is None:
            return box
        buttons = finder(QPushButton)
    for button in buttons:
        text = getattr(button, 'text', None)
        if not callable(text) or not callable(getattr(button, 'font', None)):
            # Не кнопка (в тестах встречаются замены QMessageBox).
            continue
        width = button_text_width(button)
        if width:
            button.setMinimumWidth(width)
    return box


def _message_box(parent, icon, title: str, text: str,
                 buttons=None, default=None):
    """QMessageBox с кнопками, ширина которых вмещает их текст.

    Оставлено для явных случаев; обычные QMessageBox.information() и
    QMessageBox.critical() подгоняются фильтром _MessageBoxSizer.
    """
    msg = QMessageBox(parent)
    msg.setIcon(icon)
    msg.setWindowTitle(title)
    msg.setText(text)
    if buttons is not None:
        msg.setStandardButtons(buttons)
        set_min_button_widths(msg)
    if default is not None:
        msg.setDefaultButton(default)
    return msg


class _MessageBoxSizer(QObject):
    """Подгоняет ширину кнопок у модальных окон Qt.

    QSS задаёт min-width, а QStyleSheetStyle считает его не нижней
    границей, а самой шириной кнопки: подписи вроде «Переместить в
    корень» обрезались. Ждать Show от самой кнопки надёжнее, чем
    трогать статические QMessageBox.information()/critical(): так
    правка работает для любого модального окна, включая будущие.
    """

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Show and isinstance(obj, QPushButton):
            width = button_text_width(obj)
            if width and obj.minimumWidth() < width:
                obj.setMinimumWidth(width)
        return False


def install_message_box_sizer(app) -> _MessageBoxSizer:
    """Ставит фильтр, подгоняющий кнопки модальных окон."""
    sizer = _MessageBoxSizer(app)
    app.installEventFilter(sizer)
    return sizer


def reveal_in_file_manager(path: Path) -> bool:
    """Открывает системный файловый менеджер, выделяя path.

    Windows — Проводник, macOS — Finder, Linux — xdg-open на папке.
    Возвращает False, если система не поддерживается или запуск не
    удался: вызывающий сам решает, что записать в журнал.
    """
    try:
        if sys.platform == 'win32':
            subprocess.Popen(
                ['explorer', f'/select,{path}'],
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            return True
        if sys.platform == 'darwin':
            subprocess.Popen(['open', '-R', str(path)])
            return True
        return bool(QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(path.parent))))
    except Exception:
        return False


# ====================== тема ======================

def _system_is_dark() -> bool:
    if sys.platform == 'win32':
        try:
            import winreg  # type: ignore
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r'Software\Microsoft\Windows\CurrentVersion'
                r'\Themes\Personalize',
            ) as key:
                value, _ = winreg.QueryValueEx(key, 'AppsUseLightTheme')
                return int(value) == 0
        except Exception:
            return False
    return False


def resolve_theme(setting: str) -> str:
    if setting == 'auto':
        return 'dark' if _system_is_dark() else 'light'
    return setting if setting in ('dark', 'light') else 'light'


# Палитра: холодные нейтрали, раздельные border/border_soft
# (жёсткая рамка у полей, мягкая у разделителей), hover — отдельный тон,
# accent_p — состояние нажатия.
THEMES: Dict[str, Dict[str, str]] = {
    'dark': {
        'bg':          '#17181c',
        'bg_alt':      '#1e2025',
        'bg_elev':     '#272a31',
        'text':        '#e8eaed',
        'text_dim':    '#9aa1ad',
        'border':      '#31343c',
        'border_soft': '#26282f',
        'accent':      '#3b82f6',
        'accent_h':    '#5b97f8',
        'accent_p':    '#2f6fd0',
        'sel_bg':      '#2c4a77',
        'sel_fg':      '#ffffff',
        'hover':       '#2b2f37',
        'tree_dir':    '#e2a94a',
        'tree_file':   '#e8eaed',
        'tree_err':    '#f2746c',
        'tree_up':     '#5cc98b',
        'tree_warn':   '#d9a441',
        'log_bg':      '#141519',
        'log_fg':      '#dfe3e8',
    },
    'light': {
        'bg':          '#f4f6f9',
        'bg_alt':      '#ffffff',
        'bg_elev':     '#f8fafc',
        'text':        '#1b1f26',
        'text_dim':    '#6b7280',
        'border':      '#e2e6ec',
        'border_soft': '#edf0f4',
        'accent':      '#2563eb',
        'accent_h':    '#3b82f6',
        'accent_p':    '#1d4ed8',
        'sel_bg':      '#dbeafe',
        'sel_fg':      '#12305e',
        'hover':       '#eef2f7',
        'tree_dir':    '#b45309',
        'tree_file':   '#1b1f26',
        'tree_err':    '#c62828',
        'tree_up':     '#0f7b3f',
        'tree_warn':   '#8a6100',
        'log_bg':      '#ffffff',
        'log_fg':      '#24292f',
    },
}


def build_stylesheet(name: str) -> str:
    c = THEMES[name]
    icons = build_qss_icons(name)
    # Отметки, которые Qt не рисует сам, приходят готовыми PNG. Пустая
    # строка означает, что файл создать не удалось: правило с пустым
    # image просто не применяется, фон остаётся акцентным.
    check_mark = icons['check']
    chevron = icons['chevron_down']
    chevron_right = icons['chevron_right']
    return f"""
        QWidget {{
            background: {c['bg']};
            color: {c['text']};
            font-family: 'Segoe UI', sans-serif;
            font-size: 10pt;
        }}
        QMainWindow, QDialog {{
            background: {c['bg']};
        }}
        QLabel {{ background: transparent; }}
        QLabel#Heading {{
            font-size: 11pt;
            font-weight: 600;
            color: {c['text']};
            padding: 2px 0 4px 0;
        }}
        QLabel#Dim {{ color: {c['text_dim']}; }}
        QLabel#Suggestion {{
            color: {c['accent']};
            font-weight: 600;
            padding: 1px 0;
        }}
        QLabel#SectionTitle {{
            font-size: 9pt;
            font-weight: 600;
            color: {c['text_dim']};
            padding: 2px 0;
        }}
        QFrame#Card {{
            background: {c['bg_alt']};
            border: 1px solid {c['border']};
            border-radius: 10px;
        }}
        QFrame#Separator {{
            background: {c['border_soft']};
            border: none;
            max-height: 1px;
        }}
        QLineEdit, QComboBox {{
            background: {c['bg_alt']};
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 7px;
            padding: 6px 10px;
            min-height: 20px;
            selection-background-color: {c['accent']};
            selection-color: #ffffff;
        }}
        QLineEdit:hover, QComboBox:hover {{
            border-color: {c['text_dim']};
        }}
        QLineEdit:focus, QComboBox:focus {{
            border: 1px solid {c['accent']};
            background: {c['bg_elev']};
        }}
        QLineEdit:disabled, QComboBox:disabled {{
            color: {c['text_dim']};
            background: {c['bg']};
            border-color: {c['border_soft']};
        }}
        QComboBox::drop-down {{
            border: none;
            width: 22px;
            subcontrol-origin: padding;
            subcontrol-position: center right;
        }}
        QComboBox::down-arrow {{
            image: {chevron};
            width: 14px;
            height: 14px;
        }}
        QPushButton {{
            background: {c['bg_alt']};
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 7px;
            padding: 6px 14px;
            min-height: 20px;
            min-width: 60px;
        }}
        QPushButton:hover {{
            background: {c['bg_elev']};
            border-color: {c['accent']};
        }}
        QPushButton:pressed {{
            background: {c['hover']};
            border-color: {c['accent_p']};
        }}
        QPushButton:focus {{
            border: 1px solid {c['accent']};
        }}
        QPushButton:disabled {{
            color: {c['text_dim']};
            background: {c['bg']};
            border-color: {c['border_soft']};
        }}
        QPushButton#Accent {{
            background: {c['accent']};
            color: #ffffff;
            border: 1px solid {c['accent']};
            font-weight: 600;
        }}
        QPushButton#Accent:hover {{
            background: {c['accent_h']};
            border-color: {c['accent_h']};
        }}
        QPushButton#Accent:pressed {{
            background: {c['accent_p']};
            border-color: {c['accent_p']};
        }}
        QPushButton#Accent:disabled {{
            background: {c['border']};
            border-color: {c['border']};
            color: {c['text_dim']};
        }}
        QPushButton:default {{
            border: 1px solid {c['accent']};
        }}
        QToolBar {{
            background: {c['bg_alt']};
            border: none;
            border-bottom: 1px solid {c['border']};
            spacing: 3px;
            padding: 6px 8px;
        }}
        QToolBar::separator {{
            background: {c['border']};
            width: 1px;
            margin: 5px 6px;
        }}
        QToolBar QToolButton {{
            background: transparent;
            color: {c['text']};
            border: 1px solid transparent;
            border-radius: 7px;
            padding: 6px 11px;
        }}
        QToolBar QToolButton:hover {{
            background: {c['hover']};
            border-color: {c['border']};
        }}
        QToolBar QToolButton:pressed, QToolBar QToolButton:checked {{
            background: {c['sel_bg']};
            border-color: {c['accent']};
            color: {c['sel_fg']};
        }}
        QTabWidget::pane {{
            border: 1px solid {c['border']};
            border-radius: 10px;
            top: -1px;
            background: {c['bg_alt']};
        }}
        QTabBar {{ qproperty-drawBase: 0; }}
        QTabBar::tab {{
            background: transparent;
            color: {c['text_dim']};
            padding: 8px 18px;
            border: none;
            border-bottom: 2px solid transparent;
            margin-right: 2px;
        }}
        QTabBar::tab:hover {{ color: {c['text']}; }}
        QTabBar::tab:selected {{
            color: {c['text']};
            font-weight: 600;
            border-bottom: 2px solid {c['accent']};
        }}
        QTreeView, QListView, QTableView {{
            background: {c['bg_alt']};
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 10px;
            selection-background-color: {c['sel_bg']};
            selection-color: {c['sel_fg']};
            alternate-background-color: {c['bg']};
            outline: 0;
        }}
        QTreeView::item {{
            padding: 5px 4px;
            border: none;
        }}
        QTreeView::item:hover {{
            background: {c['hover']};
        }}
        QTreeView::item:selected {{
            background: {c['sel_bg']};
            color: {c['sel_fg']};
        }}
        QTreeView::branch:has-children:closed {{
            image: {chevron_right};
        }}
        QTreeView::branch:has-children:open {{
            image: {chevron};
        }}
        QHeaderView::section {{
            background: {c['bg_elev']};
            color: {c['text_dim']};
            border: none;
            border-right: 1px solid {c['border_soft']};
            border-bottom: 1px solid {c['border']};
            padding: 8px 10px;
            font-weight: 600;
            font-size: 9pt;
        }}
        QHeaderView::section:hover {{ color: {c['text']}; }}
        QTableCornerButton::section {{
            background: {c['bg_elev']};
            border: none;
        }}
        QPlainTextEdit {{
            background: {c['log_bg']};
            color: {c['log_fg']};
            border: 1px solid {c['border']};
            border-radius: 10px;
            padding: 6px 8px;
            font-family: 'Cascadia Mono', Consolas, monospace;
            font-size: 9.5pt;
            selection-background-color: {c['accent']};
        }}
        QStatusBar {{
            background: {c['bg_alt']};
            color: {c['text_dim']};
            border-top: 1px solid {c['border']};
            padding: 2px 6px;
        }}
        QStatusBar::item {{ border: none; }}
        QStatusBar QLabel {{
            font-size: 9pt;
            color: {c['text_dim']};
        }}
        QLabel#Counter {{ font-size: 9pt; color: {c['text_dim']}; }}
        QLabel#CounterOk {{ font-size: 9pt; color: {c['tree_up']}; }}
        QLabel#CounterErr {{ font-size: 9pt; color: {c['tree_err']}; }}
        QLabel#CounterWarn {{ font-size: 9pt; color: {c['tree_warn']}; }}
        QMenu {{
            background: {c['bg_alt']};
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 8px;
            padding: 6px;
        }}
        QMenu::item {{
            padding: 6px 26px 6px 20px;
            border-radius: 6px;
        }}
        QMenu::item:selected {{
            background: {c['accent']};
            color: #ffffff;
        }}
        QMenu::item:disabled {{ color: {c['text_dim']}; }}
        QMenu::separator {{
            height: 1px;
            background: {c['border']};
            margin: 5px 8px;
        }}
        QMenuBar {{
            background: {c['bg_alt']};
            color: {c['text']};
            padding: 2px 4px;
            border-bottom: 1px solid {c['border_soft']};
        }}
        QMenuBar::item {{
            padding: 6px 12px;
            border-radius: 6px;
            background: transparent;
        }}
        QMenuBar::item:selected {{
            background: {c['accent']};
            color: #ffffff;
        }}
        QToolTip {{
            background: {c['bg_elev']};
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 6px;
            padding: 5px 8px;
        }}
        QSplitter::handle {{
            background: {c['border_soft']};
        }}
        QSplitter::handle:hover {{
            background: {c['accent']};
        }}
        QSplitter::handle:horizontal {{ width: 3px; }}
        QSplitter::handle:vertical {{ height: 3px; }}
        QCheckBox {{ spacing: 8px; }}
        QCheckBox::indicator {{
            width: 17px; height: 17px;
            border: 1.5px solid {c['text_dim']};
            border-radius: 5px;
            background: {c['bg_alt']};
        }}
        QCheckBox::indicator:hover {{
            border-color: {c['accent']};
        }}
        QCheckBox::indicator:checked {{
            background: {c['accent']};
            border-color: {c['accent']};
            image: {check_mark};
        }}
        QCheckBox::indicator:disabled {{
            border-color: {c['border']};
            background: {c['bg']};
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 12px;
            margin: 2px;
        }}
        QScrollBar::handle:vertical {{
            background: {c['border']};
            border-radius: 5px;
            min-height: 28px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {c['text_dim']};
        }}
        QScrollBar:horizontal {{
            background: transparent;
            height: 12px;
            margin: 2px;
        }}
        QScrollBar::handle:horizontal {{
            background: {c['border']};
            border-radius: 5px;
            min-width: 28px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background: {c['text_dim']};
        }}
        QScrollBar::add-line, QScrollBar::sub-line {{
            height: 0; width: 0;
        }}
        QScrollBar::add-page, QScrollBar::sub-page {{
            background: transparent;
        }}
        /* Выпадающий список ComboBox: без этого в тёмной теме он
           остаётся системным светлым. */
        QComboBox QAbstractItemView {{
            background: {c['bg_alt']};
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 8px;
            padding: 4px;
            selection-background-color: {c['accent']};
            selection-color: #ffffff;
            outline: 0;
        }}
        /* Плашка предсказуемого отказа над содержимым. */
        QFrame#Notice {{
            background: {c['bg_elev']};
            border: 1px solid {c['border']};
            border-radius: 10px;
        }}
        QFrame#Notice[level="info"] {{ border-left: 3px solid {c['accent']}; }}
        QFrame#Notice[level="warn"] {{ border-left: 3px solid {c['tree_warn']}; }}
        QFrame#Notice[level="error"] {{ border-left: 3px solid {c['tree_err']}; }}
        QFrame#Notice QLabel {{ background: transparent; padding: 2px 0; }}
        QToolButton#NoticeClose {{
            border: none;
            background: transparent;
            color: {c['text_dim']};
            padding: 2px 8px;
            border-radius: 6px;
            font-size: 11pt;
        }}
        QToolButton#NoticeClose:hover {{
            color: {c['text']};
            background: {c['hover']};
        }}
        /* Док журнала: заголовок в тон панелей, кнопка закрытия — как
           остальные иконки. */
        QDockWidget {{
            color: {c['text']};
            font-size: 10pt;
        }}
        QDockWidget::title {{
            background: {c['bg_alt']};
            border-top: 1px solid {c['border']};
            padding: 6px 10px;
            text-align: left;
        }}
        QDockWidget::close-button, QDockWidget::float-button {{
            border: none;
            background: transparent;
            padding: 2px;
        }}
        QDockWidget::close-button:hover, QDockWidget::float-button:hover {{
            background: {c['hover']};
            border-radius: 4px;
        }}
        QDialogButtonBox QPushButton {{ min-width: 92px; }}
    """


# ====================== модель дерева ======================

class FileTreeModel(QStandardItemModel):
    COL_NAME = 0
    COL_SIZE = 1
    COL_STATUS = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHorizontalHeaderLabels(['Имя', 'Размер', 'Статус'])

        self.root_path: Optional[Path] = None
        self.expanded_paths: Set[Path] = set()
        self.ftp_sync_folder: str = ''
        self.ftp_sync_names: Set[str] = set()
        self._theme_name = 'light'

    def set_theme(self, name: str):
        self._theme_name = name
        self.refresh()

    def set_root(self, path: Path):
        self.root_path = path.resolve()
        self.expanded_paths = {self.root_path}
        self.refresh()

    def set_ftp_sync(self, folder: str, names: Set[str]):
        self.ftp_sync_folder = folder
        self.ftp_sync_names = set(names)
        self.refresh()

    def clear_ftp_sync(self):
        self.ftp_sync_folder = ''
        self.ftp_sync_names = set()
        self.refresh()

    def refresh(self):
        if self.root_path is None:
            return
        self.removeRows(0, self.rowCount())

        c = THEMES[self._theme_name]
        style = QApplication.style()

        root_item = QStandardItem(self.root_path.name or str(self.root_path))
        root_item.setEditable(False)
        root_item.setData(str(self.root_path), ROLE_PATH)
        root_item.setIcon(style.standardIcon(QStyle.SP_DirIcon))
        f = root_item.font()
        f.setBold(True)
        root_item.setFont(f)
        root_item.setForeground(QColor(c['tree_dir']))

        size_item = QStandardItem('')
        size_item.setEditable(False)

        status_item = QStandardItem(str(self.root_path))
        status_item.setEditable(False)
        status_item.setForeground(QColor(c['text_dim']))

        self.appendRow([root_item, size_item, status_item])
        self._populate(root_item, self.root_path)

    def find_item(self, path: Path) -> Optional[QStandardItem]:
        if self.root_path is None:
            return None
        try:
            path = path.resolve()
        except Exception:
            return None
        if not path.is_relative_to(self.root_path):
            return None
        if path == self.root_path:
            return self.item(0)

        parts = path.relative_to(self.root_path).parts
        item = self.item(0)
        if item is None:
            return None
        for part in parts:
            found = None
            for row in range(item.rowCount()):
                child = item.child(row)
                p_str = child.data(ROLE_PATH)
                if p_str and Path(p_str).name == part:
                    found = child
                    break
            if found is None:
                return None
            self.load_children_if_stub(found)
            item = found
        return item

    def load_children_if_stub(self, item: QStandardItem):
        if item.rowCount() != 1:
            return
        if item.child(0).text() != STUB_TEXT:
            return
        item.removeRow(0)
        p_str = item.data(ROLE_PATH)
        if not p_str:
            return
        path = Path(p_str)
        if not path.is_dir():
            return
        self._populate(item, path)

    # ---------- наполнение ----------
    def _is_uploaded(self, path: Path) -> bool:
        f = self.ftp_sync_folder
        if not f or '/' in f:
            return False
        if path.parent.name != f:
            return False
        return path.name in self.ftp_sync_names

    def _populate(self, parent_item: QStandardItem, path: Path):
        c = THEMES[self._theme_name]
        style = QApplication.style()
        dir_icon = style.standardIcon(QStyle.SP_DirIcon)
        file_icon = style.standardIcon(QStyle.SP_FileIcon)

        try:
            children = sorted(
                path.iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
        except OSError:
            return

        for child_path in children:
            if child_path.is_dir():
                name_item = QStandardItem(child_path.name)
                name_item.setEditable(False)
                name_item.setData(str(child_path), ROLE_PATH)
                name_item.setIcon(dir_icon)
                f = name_item.font()
                f.setBold(True)
                name_item.setFont(f)
                name_item.setForeground(QColor(c['tree_dir']))

                size_item = QStandardItem('')
                size_item.setEditable(False)

                status_item = QStandardItem('папка')
                status_item.setEditable(False)
                status_item.setForeground(QColor(c['text_dim']))

                parent_item.appendRow([name_item, size_item, status_item])

                try:
                    has_children = any(child_path.iterdir())
                except OSError:
                    has_children = False
                if has_children:
                    stub = QStandardItem(STUB_TEXT)
                    stub.setEditable(False)
                    stub.setForeground(QColor(c['text_dim']))
                    name_item.appendRow(
                        [stub, QStandardItem(''), QStandardItem('')])
                continue

            info = analyze_file(child_path)
            try:
                size_str = human_size(child_path.stat().st_size)
            except OSError:
                size_str = '?'

            uploaded = self._is_uploaded(child_path)

            status_parts: List[str] = []
            if uploaded:
                status_parts.append('✓ на FTP')
            if info['has_error']:
                if info['new_name']:
                    status_parts.append(f'правка → {info["new_name"]}')
                else:
                    status_parts.append(info['reason'] or 'ошибка')
            if is_uploadable(child_path, info):
                status_parts.append('можно на FTP')

            name_item = QStandardItem(child_path.name)
            name_item.setEditable(False)
            name_item.setData(str(child_path), ROLE_PATH)
            name_item.setIcon(file_icon)

            if info['has_error']:
                name_item.setForeground(QColor(c['tree_err']))
                f = name_item.font()
                f.setBold(True)
                name_item.setFont(f)
            elif uploaded:
                name_item.setForeground(QColor(c['tree_up']))
            else:
                name_item.setForeground(QColor(c['tree_file']))

            size_item = QStandardItem(size_str)
            size_item.setEditable(False)
            size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

            status_item = QStandardItem('; '.join(status_parts))
            status_item.setEditable(False)
            if info['has_error']:
                status_item.setForeground(QColor(c['tree_err']))
            elif uploaded:
                status_item.setForeground(QColor(c['tree_up']))
            else:
                status_item.setForeground(QColor(c['text_dim']))

            parent_item.appendRow([name_item, size_item, status_item])


# ====================== модель FTP ======================

class FtpListModel(QStandardItemModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHorizontalHeaderLabels(['Имя', 'Размер'])
        self._theme_name = 'light'
        self._entries: List[Tuple[str, bool, int]] = []

    def set_theme(self, name: str):
        self._theme_name = name
        self.refresh()

    def set_entries(self, entries: List[Tuple[str, bool, int]]):
        self._entries = entries
        self.refresh()

    def entry_for_name(self, name: str) -> Optional[Tuple[str, bool, int]]:
        for n, is_dir, size in self._entries:
            if n == name:
                return (n, is_dir, size)
        return None

    def refresh(self):
        self.removeRows(0, self.rowCount())
        c = THEMES[self._theme_name]
        style = QApplication.style()
        dir_icon = style.standardIcon(QStyle.SP_DirIcon)
        file_icon = style.standardIcon(QStyle.SP_FileIcon)

        for name, is_dir, size in self._entries:
            if is_dir:
                name_item = QStandardItem(name)
                name_item.setIcon(dir_icon)
                f = name_item.font()
                f.setBold(True)
                name_item.setFont(f)
                name_item.setForeground(QColor(c['tree_dir']))
                size_item = QStandardItem('')
            else:
                name_item = QStandardItem(name)
                name_item.setIcon(file_icon)
                name_item.setForeground(QColor(c['tree_file']))
                size_item = QStandardItem(
                    human_size(size) if size else '')
                size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            name_item.setEditable(False)
            size_item.setEditable(False)
            name_item.setData(name, ROLE_FTP_NAME)
            self.appendRow([name_item, size_item])


# ====================== фоновые задачи ======================

class FtpListTask(QObject):
    done = Signal(list, str, str)   # entries, err, folder

    def __init__(self, parent, host, port, user, password, base, folder):
        super().__init__(parent)
        self._args = (host, port, user, password, base, folder)

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        host, port, user, password, base, folder = self._args
        try:
            entries = list_folder(host, port, user, password, base, folder)
            self.done.emit(entries, '', folder)
        except Exception as e:
            self.done.emit([], str(e), folder)


class FtpUploadTask(QObject):
    finished = Signal(str, str, str)   # folder, filename, err

    def __init__(self, parent, host, port, user, password,
                 base, folder, src: Path, readonly: bool,
                 log_cb):
        super().__init__(parent)
        self._args = (host, port, user, password, base, folder, src)
        self._readonly = readonly
        self._log_cb = log_cb

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        host, port, user, password, base, folder, src = self._args

        def cb(msg: str):
            self._log_cb(msg)

        try:
            upload_file(host, port, user, password, base, folder,
                        src, cb, readonly=self._readonly)
            if not self._readonly:
                self.finished.emit(folder, src.name, '')
            else:
                self.finished.emit('', '', '')
        except Exception as e:
            self.finished.emit(folder, src.name, str(e))


class FtpDeleteTask(QObject):
    finished = Signal(str, str, str)   # folder, filename, err

    def __init__(self, parent, host, port, user, password,
                 base, folder, filename):
        super().__init__(parent)
        self._args = (host, port, user, password, base, folder, filename)

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        host, port, user, password, base, folder, filename = self._args
        try:
            delete_file(host, port, user, password, base, folder, filename)
            self.finished.emit(folder, filename, '')
        except Exception as e:
            self.finished.emit(folder, filename, str(e))


# ====================== главное окно ======================

class MainWindow(QMainWindow):
    def __init__(self, config: dict, initial_dir: Path):
        super().__init__()
        self.config = config
        self.tree_root = initial_dir.resolve()
        self.watch_dir = self.tree_root
        self.observer: Optional[Observer] = None

        self.downloads_observer: Optional[Observer] = None
        self.downloads_poll_timer: Optional[QTimer] = None
        # Время запуска слежения: файлы старше не предлагаем переносить.
        self._downloads_baseline = 0.0
        # Защита от повторного показа диалога для одного и того же файла.
        self._seen_downloads: Set[Path] = set()

        self.ftp_folder = ''
        self.ftp_connected = False
        self.ftp_loading = False

        self._refresh_pending = False

        self.setWindowTitle('ok-helper')
        # Иконка на самом окне: тесты и превью создают QApplication
        # сами, поэтому полагаться на run_gui нельзя.
        self.setWindowIcon(_std_icon(QStyle.SP_FileDialogDetailedView))
        saved_geometry = _parse_ui_geometry(
            str(config.get('window_geometry', '')))

        self._build_ui()
        self._build_menu()
        self._build_toolbar()
        self._build_statusbar()

        self.apply_theme()

        # Размеры и положение из прошлого запуска; при испорченной
        # записи остаются размеры по умолчанию.
        if saved_geometry:
            width, height, x, y = saved_geometry
            self.resize(width, height)
            self.move(x, y)
        else:
            self.resize(1280, 800)

        self._restore_ui_state()

        self._start_watch()
        self._start_downloads_watch()
        self.set_tree_root(self.tree_root, save=False)
        self.scan_dir(self.tree_root)

        # Ширины колонок ставим после первой загрузки дерева: иначе
        # режим «по содержимому» пересчитывает их и перебивает наши.
        self._restore_tree_columns()

        # Высоту дока журнала задаём последней: до показа окна Qt ещё
        # не знает итоговую компоновку и молча теряет resizeDocks.
        QTimer.singleShot(0, self._restore_log_dock_height)

    # ---------- UI ----------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        # Плашка предсказуемых отказов: не прерывает работу, в отличие
        # от модальных окон. Скрыта, пока нет сообщения.
        self.notice_bar = self._build_notice_bar()
        outer.addWidget(self.notice_bar)

        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(self._on_tab_changed)
        outer.addWidget(self.tabs, 1)

        self._build_files_tab()
        self._build_ftp_tab()
        self._build_log_dock()

    def _build_log_dock(self):
        """Журнал — сворачиваемая панель внизу, а не колонка слева.

        Внизу он не отнимает ширину у дерева, а длинные записи
        помещаются целиком: список строк без переноса.
        """
        dock = QDockWidget('Журнал', self)
        dock.setObjectName('LogDock')
        dock.setAllowedAreas(Qt.BottomDockWidgetArea)
        # Кнопки закрытия и отрыва не нужны: панель управляется
        # переключателем «Журнал» (Ctrl+L).
        dock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        self.log_dock = dock

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(8, 4, 8, 8)
        layout.setSpacing(6)

        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumBlockCount(2000)
        # Перенос длинных записей с висячим отступом: продолжение
        # строки не уезжает под колонку времени, как при обычном wrap.
        self.log_edit.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        layout.addWidget(self.log_edit, 1)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addStretch(1)

        copy_btn = QPushButton('Копировать журнал')
        copy_btn.setToolTip('Скопировать журнал целиком (Ctrl+Shift+L)')
        copy_btn.clicked.connect(self.on_copy_log)
        row.addWidget(copy_btn)

        save_btn = QPushButton('Сохранить в файл…')
        save_btn.setToolTip('Записать журнал в .txt')
        save_btn.clicked.connect(self.on_save_log)
        row.addWidget(save_btn)

        clear_btn = QPushButton('Очистить')
        clear_btn.setToolTip('Очистить журнал')
        clear_btn.clicked.connect(self.on_clear_log)
        row.addWidget(clear_btn)

        layout.addLayout(row)
        dock.setWidget(content)

        self.addDockWidget(Qt.BottomDockWidgetArea, dock)
        # Переключатель видимости работает с доком целиком.
        self.log_frame = dock

    def _build_files_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        nav = QHBoxLayout()
        nav.setSpacing(6)

        self.btn_up = QPushButton('↑ Вверх')
        self.btn_up.setToolTip('Перейти в родительскую папку')
        self.btn_up.clicked.connect(self.on_up)
        nav.addWidget(self.btn_up)

        nav.addWidget(QLabel('Путь:'))
        self.path_edit = QLineEdit(str(self.tree_root))
        self.path_edit.returnPressed.connect(self.on_path_entered)
        nav.addWidget(self.path_edit, 1)

        self.btn_browse = QPushButton('Обзор…')
        self.btn_browse.setToolTip('Выбрать директорию (Ctrl+O)')
        self.btn_browse.clicked.connect(self.on_browse_dir)
        nav.addWidget(self.btn_browse)

        layout.addLayout(nav)

        self.tree_model = FileTreeModel(self)
        self.tree_model.set_theme(resolve_theme(self.config.get('theme', 'auto')))

        self.tree_view = QTreeView()
        self.tree_view.setModel(self.tree_model)
        self.tree_view.setAlternatingRowColors(True)
        self.tree_view.setUniformRowHeights(True)
        self.tree_view.setSelectionBehavior(QTreeView.SelectRows)
        self.tree_view.setEditTriggers(QTreeView.NoEditTriggers)
        self.tree_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree_view.customContextMenuRequested.connect(
            self.on_tree_context_menu)

        header = self.tree_view.header()
        header.setSectionResizeMode(
            FileTreeModel.COL_NAME, QHeaderView.Stretch)
        header.setSectionResizeMode(
            FileTreeModel.COL_SIZE, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(
            FileTreeModel.COL_STATUS, QHeaderView.ResizeToContents)

        self.tree_view.expanded.connect(self.on_tree_expanded)
        self.tree_view.collapsed.connect(self.on_tree_collapsed)
        self.tree_view.doubleClicked.connect(self.on_tree_double_click)
        self.tree_view.activated.connect(self.on_tree_activated)

        layout.addWidget(self.tree_view, 1)

        # Отдельной панели действий здесь нет: те же команды живут в
        # toolbar, в меню «Правка» и в контекстном меню дерева.
        hint = QLabel(
            'Двойной клик по папке — раскрыть / свернуть, по файлу — меню '
            'действий; Enter — сделать корнем дерева; правый клик — меню.')
        hint.setObjectName('Dim')
        layout.addWidget(hint)

        self.tabs.addTab(tab, 'Файлы')


    def _build_ftp_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        nav = QHBoxLayout()
        nav.setSpacing(6)

        nav.addWidget(QLabel('Путь:'))
        self.ftp_path_edit = QLineEdit()
        self.ftp_path_edit.returnPressed.connect(self.on_ftp_connect)
        nav.addWidget(self.ftp_path_edit, 1)

        btn_connect = QPushButton('Подключиться')
        btn_connect.setObjectName('Accent')
        btn_connect.setToolTip('Прочитать листинг папки на сервере')
        btn_connect.clicked.connect(self.on_ftp_connect)
        nav.addWidget(btn_connect)

        btn_refresh = QPushButton('Обновить')
        btn_refresh.setIcon(_std_icon(QStyle.SP_BrowserReload))
        btn_refresh.setToolTip('Обновить листинг FTP')
        btn_refresh.clicked.connect(self.on_ftp_refresh)
        nav.addWidget(btn_refresh)

        layout.addLayout(nav)

        follow_row = QHBoxLayout()
        self.follow_checkbox = QCheckBox(
            'Следить за папкой из вкладки «Файлы» '
            '(автоподстановка при переходе)')
        self.follow_checkbox.setChecked(True)
        self.follow_checkbox.toggled.connect(self._on_follow_changed)
        follow_row.addWidget(self.follow_checkbox)
        follow_row.addStretch(1)
        layout.addLayout(follow_row)

        self.ftp_model = FtpListModel(self)
        self.ftp_model.set_theme(resolve_theme(self.config.get('theme', 'auto')))

        self.ftp_view = QTreeView()
        self.ftp_view.setModel(self.ftp_model)
        self.ftp_view.setAlternatingRowColors(True)
        self.ftp_view.setUniformRowHeights(True)
        self.ftp_view.setSelectionBehavior(QTreeView.SelectRows)
        self.ftp_view.setEditTriggers(QTreeView.NoEditTriggers)
        self.ftp_view.doubleClicked.connect(self.on_ftp_double_click)

        header = self.ftp_view.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)

        layout.addWidget(self.ftp_view, 1)

        actions = QHBoxLayout()
        actions.setSpacing(6)

        self.btn_ftp_delete = QPushButton('Удалить выделенное')
        self.btn_ftp_delete.setIcon(_std_icon(QStyle.SP_TrashIcon))
        self.btn_ftp_delete.clicked.connect(self.on_ftp_delete_selected)
        actions.addWidget(self.btn_ftp_delete)

        btn_up = QPushButton('Загрузить файл сюда…')
        btn_up.setIcon(_std_icon(QStyle.SP_ArrowUp))
        btn_up.clicked.connect(self.on_ftp_upload_here)
        actions.addWidget(btn_up)

        actions.addStretch(1)
        layout.addLayout(actions)

        hint = QLabel(
            'Двойной клик по папке — войти. '
            'Удаление доступно только при ftp_readonly = false.')
        hint.setObjectName('Dim')
        layout.addWidget(hint)

        self.tabs.addTab(tab, 'FTP')
        self._sync_ftp_actions()

    def _sync_ftp_actions(self):
        """Согласует доступность кнопок FTP с режимом эмуляции.

        В read-only удаление всё равно запрещено, поэтому кнопка честно
        выключена, а не выдаёт отказ по нажатию.
        """
        button = getattr(self, 'btn_ftp_delete', None)
        if button is None:
            return
        readonly = self._cfg_bool(self.config, 'ftp_readonly', False)
        button.setEnabled(not readonly)
        button.setToolTip(
            'Удаление недоступно: ftp_readonly = true' if readonly
            else 'Удалить выделенный файл на сервере (Del)')

    def _sync_downloads_actions(self):
        """Согласует меню «Загрузки» с настройками слежения."""
        watch = self._cfg_bool(self.config, 'watch_downloads', False)
        action = getattr(self, 'watch_downloads_action', None)
        if action is not None and action.isChecked() != watch:
            action.blockSignals(True)
            action.setChecked(watch)
            action.blockSignals(False)

        check = getattr(self, 'downloads_action', None)
        if check is None:
            return
        if not watch:
            check.setToolTip('Слежение выключено '
                             '(watch_downloads = false)')
        elif self.downloads_watch_active():
            folder = self.config.get('downloads_dir_resolved')
            check.setToolTip(f'Проверить папку загрузок: {folder} (Ctrl+D)')
        else:
            check.setToolTip('Слежение не удалось запустить — '
                             'подробности в журнале')


    def _build_notice_bar(self) -> QFrame:
        """Плашка над содержимым вместо модального окна для отказов."""
        bar = QFrame()
        bar.setObjectName('Notice')
        bar.setProperty('level', 'info')
        bar.setVisible(False)

        row = QHBoxLayout(bar)
        row.setContentsMargins(10, 6, 6, 6)
        row.setSpacing(8)

        self.notice_label = QLabel('')
        self.notice_label.setWordWrap(True)
        row.addWidget(self.notice_label, 1)

        close_btn = QToolButton()
        close_btn.setObjectName('NoticeClose')
        close_btn.setText('✕')
        close_btn.setToolTip('Скрыть сообщение')
        close_btn.setAutoRaise(True)
        close_btn.clicked.connect(self.hide_notice)
        row.addWidget(close_btn)

        return bar

    def show_notice(self, text: str, level: str = 'info'):
        """Показывает неблокирующее сообщение над содержимым.

        level: info | warn | error — влияет только на цвет полосы.
        """
        if level not in ('info', 'warn', 'error'):
            level = 'info'
        self.notice_label.setText(text)
        self.notice_bar.setProperty('level', level)
        # Переполировка нужна, чтобы QSS подхватил новый level.
        style = self.notice_bar.style()
        style.unpolish(self.notice_bar)
        style.polish(self.notice_bar)
        self.notice_bar.setVisible(True)

    def hide_notice(self):
        self.notice_bar.setVisible(False)
        self.notice_label.setText('')

    def on_settings_saved_log_visibility(self):
        """Синхронизирует переключатель после сохранения настроек."""
        self._apply_log_visible(
            self._cfg_bool(self.config, 'log_visible', True))
        self._commit_log_visibility()

    def _show_notice_or_box(self, title: str, text: str,
                            level: str = 'warn'):
        """Отказ: плашкой, а если включён предсказуемый режим — окном.

        blocking_errors = true возвращает модальное окно, к которому
        привыкли раньше.
        """
        if self._cfg_bool(self.config, 'blocking_errors', False):
            QMessageBox.critical(self, title, text)
        else:
            self.show_notice(text, level)

    # ---------- состояние интерфейса ----------
    @staticmethod
    def _cfg_bool(config: dict, key: str, default: bool = True) -> bool:
        """Читает логический ключ конфига, перенося старые конфиги.

        Ключа может не быть в файле, созданном прежней версией, тогда
        берётся значение по умолчанию.
        """
        try:
            return bool(config.get(key, default))
        except (TypeError, ValueError):
            return default

    def _restore_ui_state(self):
        """Возвращает видимость и высоту панели журнала."""
        self._apply_log_visible(
            self._cfg_bool(self.config, 'log_visible', True))
        self._commit_log_visibility()

    def _restore_tree_columns(self):
        """Возвращает сохранённые ширины колонок дерева файлов.

        Колонки «Размер» и «Статус» по умолчанию считаются по
        содержимому: в этом режиме resizeSection игнорируется, поэтому
        сначала переводим все колонки в ручной режим.
        """
        widths = _parse_ui_columns(
            str(self.config.get('tree_column_widths', '')))
        if not widths:
            return
        header = self.tree_view.header()
        for column in range(header.count()):
            header.setSectionResizeMode(column, QHeaderView.Interactive)
        for column, width in enumerate(widths):
            if width > 0:
                header.resizeSection(column, width)

    def _save_ui_state(self):
        """Пишет размеры окна, высоту журнала и колонок в конфиг."""
        normal = self.normalGeometry() if self.isMaximized() \
            else self.geometry()
        self.config['window_geometry'] = _ui_geometry(
            normal.width(), normal.height(), normal.x(), normal.y())
        # splitter_state — ключ прежних версий; пишем пустое значение,
        # чтобы старые конфиги не хранили мусор.
        self.config['splitter_state'] = ''
        height = self._dock_height()
        if height > 0:
            self.config['log_dock_height'] = height
        header = self.tree_view.header()
        self.config['tree_column_widths'] = ','.join(
            str(header.sectionSize(column))
            for column in range(header.count()))
        try:
            save_config(self.config)
        except Exception:
            pass

    def _dock_height(self) -> int:
        """Текущая высота панели журнала; 0 — панель скрыта."""
        dock = getattr(self, 'log_dock', None)
        if dock is None:
            return 0
        try:
            if not dock.isVisible():
                return 0
            return int(dock.height())
        except RuntimeError:
            # C++-объект уже удалён (окно закрыто) — высоты нет.
            return 0

    def _restore_log_dock_height(self):
        """Возвращает сохранённую высоту панели журнала."""
        dock = getattr(self, 'log_dock', None)
        if dock is None:
            return
        try:
            if not dock.isVisible():
                return
        except RuntimeError:
            return
        try:
            height = int(self.config.get('log_dock_height', 180))
        except (TypeError, ValueError):
            height = 180
        self.resizeDocks([dock], [max(80, min(height, 1200))],
                         Qt.Vertical)
        self._log_height_applied = True

    def showEvent(self, event):
        """Применяет высоту журнала, когда окно уже показано.

        До первого показа Qt ещё не знает компоновку и молча теряет
        resizeDocks, поэтому одной установки в конструкторе мало.
        """
        super().showEvent(event)
        if not getattr(self, '_log_height_applied', False):
            QTimer.singleShot(0, self._restore_log_dock_height)

    def _apply_log_visible(self, visible: bool):
        """Показывает или прячет панель журнала внизу."""
        dock = getattr(self, 'log_dock', None)
        if dock is not None:
            try:
                dock.setVisible(bool(visible))
            except RuntimeError:
                return
            if visible:
                self._restore_log_dock_height()
        self.config['log_visible'] = bool(visible)

    def _commit_log_visibility(self):
        """Синхронизирует переключатель «Журнал» с фактическим состоянием.

        setChecked() шлёт toggled, поэтому ставим только при расхождении
        и приглушаем сигнал: иначе получается рекурсия.
        """
        action = getattr(self, 'log_action', None)
        if action is None:
            return
        visible = self.log_frame.isVisibleTo(self)
        if action.isChecked() == visible:
            return
        action.blockSignals(True)
        action.setChecked(visible)
        action.blockSignals(False)

    def on_toggle_log(self, checked: bool):
        self._apply_log_visible(bool(checked))
        self.log(f'[i] Панель журнала: {"показана" if checked else "скрыта"}',
                 'info')

    # ---------- журнал: копирование и сохранение ----------
    def on_copy_log(self):
        text = self.log_edit.toPlainText()
        if not text:
            return
        if copy_to_clipboard(text):
            self.log('[БУФЕР] Журнал скопирован', 'ok')
        else:
            self.log('[ОШБ] Буфер обмена недоступен', 'error')

    def on_save_log(self):
        default = str(Path.home() / 'ok-helper-log.txt')
        name, _ = QFileDialog.getSaveFileName(
            self, 'Сохранить журнал', default, 'Текстовые файлы (*.txt)')
        if not name:
            return
        try:
            Path(name).write_text(self.log_edit.toPlainText(),
                                  encoding='utf-8')
        except OSError as e:
            self._show_notice_or_box('Журнал', f'Не сохранить журнал: {e}',
                                     'error')
            return
        self.log(f'[OK] Журнал сохранён: {name}', 'ok')

    def _build_menu(self):
        menubar = self.menuBar()

        m_file = menubar.addMenu('Файл')
        self._act(m_file, 'Выбрать директорию…', 'Ctrl+O',
                  self.on_browse_dir, QStyle.SP_DirOpenIcon)
        m_file.addSeparator()
        self._act(m_file, 'Настройки…', 'F10', self.open_settings,
                  QStyle.SP_FileDialogDetailedView)
        m_file.addSeparator()
        self._act(m_file, 'Выход', 'Ctrl+Q', self.close)

        m_edit = menubar.addMenu('Правка')
        self._act(m_edit, 'Обновить', 'F5', self.on_refresh,
                  QStyle.SP_BrowserReload)
        m_edit.addSeparator()
        self._act(m_edit, 'Создать папку…', 'Ctrl+N', self.on_new_folder,
                  QStyle.SP_FileDialogNewFolder)
        self._act(m_edit, 'Переименовать…', 'F2', self.on_rename)
        self._act(m_edit, 'Переместить…', 'Ctrl+X', self.on_move,
                  QStyle.SP_FileDialogToParent)
        self._act(m_edit, 'Копировать имя', 'Ctrl+Y', self.on_copy_name)
        self._act(m_edit, 'Копировать путь', 'Ctrl+Shift+C',
                  self.on_copy_path)
        self._act(m_edit, 'Открыть', None, self.on_open_selected)
        self._act(m_edit, 'Показать в проводнике', 'Ctrl+E',
                  self.on_reveal_selected, QStyle.SP_DirOpenIcon)
        m_edit.addSeparator()
        self._act(m_edit, 'Применить автоправку', None, self.on_apply_fix,
                  QStyle.SP_DialogApplyButton)
        self._act(m_edit, 'Исправить все', 'Ctrl+F5', self.on_fix_all,
                  QStyle.SP_DialogApplyButton)
        m_edit.addSeparator()
        self._act(m_edit, 'Копировать журнал', 'Ctrl+Shift+L',
                  self.on_copy_log)
        self._act(m_edit, 'Сохранить журнал в файл…', None, self.on_save_log,
                  QStyle.SP_DialogSaveButton)

        m_ftp = menubar.addMenu('FTP')
        self._act(m_ftp, 'Загрузить выделенный на FTP', 'F12',
                  self.on_upload_selected, QStyle.SP_ArrowUp)
        self._act(m_ftp, 'Загрузить файл в текущую папку FTP…', None,
                  self.on_ftp_upload_here)
        m_ftp.addSeparator()
        self._act(m_ftp, 'Обновить листинг FTP', None, self.on_ftp_refresh,
                  QStyle.SP_BrowserReload)
        self._act(m_ftp, 'Удалить файл на FTP', 'Del',
                  self.on_ftp_delete_selected, QStyle.SP_TrashIcon)

        m_help = menubar.addMenu('Справка')
        self._act(m_help, 'Справка', 'F1', self.open_help,
                  QStyle.SP_DialogHelpButton)
        self._act(m_help, 'О программе', None, self.open_about,
                  QStyle.SP_MessageBoxInformation)

        # Папка загрузок: слежение включается в F10, здесь — ручная
        # проверка и понятное состояние.
        m_downloads = menubar.addMenu('Загрузки')
        self.downloads_action = self._act(
            m_downloads, 'Проверить папку загрузок', 'Ctrl+D',
            self.on_check_downloads, QStyle.SP_DialogOpenButton)
        self.watch_downloads_action = QAction('Следить за папкой загрузок',
                                              self)
        self.watch_downloads_action.setCheckable(True)
        self.watch_downloads_action.setChecked(
            self._cfg_bool(self.config, 'watch_downloads', False))
        self.watch_downloads_action.toggled.connect(
            self.on_watch_downloads_toggled)
        m_downloads.addAction(self.watch_downloads_action)
        self.addAction(self.watch_downloads_action)
        m_downloads.addSeparator()
        self._act(m_downloads, 'Открыть папку загрузок', None,
                  self.on_open_downloads_dir, QStyle.SP_DirOpenIcon)
        self._act(m_downloads, 'Папка загрузок в настройках…', None,
                  self.open_settings, QStyle.SP_FileDialogDetailedView)

        # Быстрое переключение вкладок: подсказка в плашке ссылается
        # на Ctrl+1, значит сочетание должно работать.
        m_view = menubar.addMenu('Вид')
        self._act(m_view, 'Вкладка «Файлы»', 'Ctrl+1',
                  lambda: self.tabs.setCurrentIndex(0))
        self._act(m_view, 'Вкладка «FTP»', 'Ctrl+2',
                  lambda: self.tabs.setCurrentIndex(1))
        m_view.addSeparator()
        # Ctrl+L висит на самом переключателе в тулбаре; здесь — без
        # второго сочетания, иначе Qt считает shortcut неоднозначным.
        self._act(m_view, 'Показать / скрыть журнал', None,
                  lambda: self.log_action.toggle())

    def _act(self, menu: QMenu, text: str, shortcut: Optional[str], slot,
             icon: Optional[QStyle.StandardPixmap] = None):
        a = QAction(text, self)
        if icon is not None:
            a.setIcon(_std_icon(icon))
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.triggered.connect(slot)
        menu.addAction(a)
        self.addAction(a)
        return a

    def _build_toolbar(self):
        tb = QToolBar('Основные')
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        tb.setIconSize(QSize(16, 16))
        self.addToolBar(tb)
        self.toolbar = tb

        def add(text, slot, tooltip=None, icon=None):
            a = QAction(text, self)
            if icon is not None:
                a.setIcon(_std_icon(icon))
            a.triggered.connect(slot)
            if tooltip:
                a.setToolTip(tooltip)
            tb.addAction(a)
            return a

        add('Вверх', self.on_up, 'Перейти в родительскую папку',
            QStyle.SP_ArrowUp)
        tb.addSeparator()
        add('Обновить', self.on_refresh, 'Перечитать содержимое (F5)',
            QStyle.SP_BrowserReload)
        add('Исправить все', self.on_fix_all,
            'Переименовать все уверенные случаи (Ctrl+F5)',
            QStyle.SP_DialogApplyButton)
        tb.addSeparator()
        add('Папка', self.on_new_folder, 'Создать папку (Ctrl+N)',
            QStyle.SP_FileDialogNewFolder)
        add('Переименовать', self.on_rename, 'Переименовать файл (F2)',
            QStyle.SP_FileDialogDetailedView)
        add('Переместить', self.on_move, 'Переместить в другую папку (Ctrl+X)',
            QStyle.SP_FileDialogToParent)
        add('Копировать имя', self.on_copy_name,
            'Скопировать имя в буфер обмена (Ctrl+Y)',
            QStyle.SP_FileIcon)
        tb.addSeparator()
        add('На FTP', self.on_upload_selected,
            'Загрузить выделенный файл на FTP (F12)',
            QStyle.SP_ArrowUp)
        add('В проводнике', self.on_reveal_selected,
            'Открыть папку с выделением файла (Ctrl+E)',
            QStyle.SP_DirOpenIcon)
        tb.addSeparator()
        add('Проверить загрузки', self.on_check_downloads,
            'Проверить папку загрузок и предложить перенос (Ctrl+D)',
            QStyle.SP_DialogOpenButton)
        tb.addSeparator()

        # Переключатель панели журнала: состояние сохраняется в конфиге.
        self.log_action = QAction('Журнал', self)
        self.log_action.setCheckable(True)
        self.log_action.setToolTip('Показать или скрыть панель журнала (Ctrl+L)')
        self.log_action.setShortcut(QKeySequence('Ctrl+L'))
        self.log_action.toggled.connect(self.on_toggle_log)
        tb.addAction(self.log_action)
        self.addAction(self.log_action)

    def _build_statusbar(self):
        sb = QStatusBar()
        self.setStatusBar(sb)
        sb.setSizeGripEnabled(True)

        # Слева — сообщение о текущей операции, справа постоянно живёт
        # блок «счётчики + путь»: он не затирается сообщениями.
        self.status_label = QLabel('Готово.')
        sb.addWidget(self.status_label)
        sb.addWidget(QLabel(''), 1)

        # Счётчики отдельными подписями: у ошибок и заливки свой цвет.
        self.count_files_label = QLabel('')
        self.count_errors_label = QLabel('')
        self.count_ftp_label = QLabel('')
        for label in (self.count_files_label, self.count_errors_label,
                      self.count_ftp_label):
            label.setObjectName('Counter')
            sb.addPermanentWidget(label)

        self.path_label = QLabel('')
        self.path_label.setObjectName('Dim')
        self.path_label.setToolTip('Корень дерева файлов')
        sb.addPermanentWidget(self.path_label)
        self._set_counters(0, 0, 0)

    def status_line(self) -> str:
        """Счётчики одной строкой: файлов, ошибок, залитых на FTP."""
        counts = '   ·   '.join(label.text() for label in (
            self.count_files_label, self.count_errors_label,
            self.count_ftp_label))
        return counts

    def _set_counters(self, total: int, errors: int, uploaded: int):
        """Обновляет счётчики, подсвечивая проблемы."""
        self.count_files_label.setText(f'Файлов: {total}')
        self.count_files_label.setObjectName('Counter')
        self.count_errors_label.setText(f'ошибок: {errors}')
        self.count_errors_label.setObjectName(
            'CounterErr' if errors else 'Counter')
        self.count_ftp_label.setText(f'залито на FTP: {uploaded}')
        self.count_ftp_label.setObjectName(
            'CounterOk' if uploaded else 'Counter')
        for label in (self.count_files_label, self.count_errors_label,
                      self.count_ftp_label):
            style = label.style()
            style.unpolish(label)
            style.polish(label)


    # ---------- тема ----------
    def apply_theme(self):
        name = resolve_theme(self.config.get('theme', 'auto'))
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_stylesheet(name))
        if hasattr(self, 'tree_model'):
            self.tree_model.set_theme(name)
        if hasattr(self, 'ftp_model'):
            self.ftp_model.set_theme(name)
        if hasattr(self, 'log_edit'):
            # Держим inline-стиль панели журнала: она одна представляет
            # собственный «терминальный» фон, отличный от панелей.
            c = THEMES[name]
            self.log_edit.setStyleSheet(
                f'background:{c["log_bg"]};color:{c["log_fg"]};'
                f'border:1px solid {c["border"]};border-radius:10px;'
                f'padding:6px 8px;'
                f"font-family:'Cascadia Mono',Consolas,monospace;"
            )
        # Счётчики статусбара перекрашиваются вместе с темой.
        self._refresh_counters()

    def _refresh_counters(self):
        """Перечитывает счётчики из бейджей и перекрашивает их.

        Нужно после смены темы: objectName управляет цветом, и QSS
        перестаёт применяться, пока виджет не переполирован.
        """
        if not hasattr(self, 'count_files_label'):
            return

        def value(label: QLabel) -> int:
            tail = label.text().partition(': ')[2]
            try:
                return int(tail)
            except ValueError:
                return 0

        self._set_counters(value(self.count_files_label),
                           value(self.count_errors_label),
                           value(self.count_ftp_label))

    # ---------- лог ----------
    def log(self, msg: str, level: str = 'info'):
        if threading.current_thread() is threading.main_thread():
            self._append_log(msg, level)
        else:
            QTimer.singleShot(0, lambda: self._append_log(msg, level))

    def _append_log(self, msg: str, level: str):
        name = resolve_theme(self.config.get('theme', 'auto'))
        c = THEMES[name]
        color_map = {
            'info':  c['log_fg'],
            'ok':    c['tree_up'],
            'warn':  c['tree_warn'],
            'error': c['tree_err'],
            'cfg':   c['accent_h'],
        }
        color = color_map.get(level, c['log_fg'])

        # Прокручиваем только если пользователь и так внизу: иначе
        # чтение истории срывалось бы на каждой новой записи.
        bar = self.log_edit.verticalScrollBar()
        at_value = bar.value()
        at_bottom = at_value >= bar.maximum()

        ts = datetime.now().strftime('%H:%M:%S')
        cursor = self.log_edit.textCursor()
        cursor.movePosition(QTextCursor.End)

        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor.insertText(f'{ts}  {msg}\n', fmt)
        # Висячий отступ: продолжение длинной записи начинается под
        # текстом сообщения, а не под отметкой времени.
        block_fmt = QTextBlockFormat()
        block_fmt.setLeftMargin(LOG_INDENT)
        block_fmt.setTextIndent(-LOG_INDENT)
        cursor.mergeBlockFormat(block_fmt)

        self.log_edit.setTextCursor(cursor)
        if at_bottom:
            self.log_edit.ensureCursorVisible()
            bar.setValue(bar.maximum())
        else:
            # setTextCursor сам подтягивает вид к курсору — возвращаем
            # позицию, чтобы чтение истории не срывалось.
            bar.setValue(at_value)

    def set_status(self, text: str):
        self.status_label.setText(text)

    def on_clear_log(self):
        self.log_edit.clear()
        self.log('Журнал очищен', 'info')

    # ---------- watchdog ----------
    def _start_watch(self):
        self._stop_watch()
        try:
            handler = DirHandler(self._on_fs_event)
            obs = Observer()
            obs.schedule(handler, str(self.watch_dir), recursive=True)
            obs.start()
            self.observer = obs
        except Exception as e:
            self.log(f'[watchdog] не удалось запустить: {e}', 'error')

    def _stop_watch(self):
        if self.observer:
            try:
                self.observer.stop()
                self.observer.join(timeout=2)
            except Exception:
                pass
            self.observer = None

        # ---------- watchdog: папка загрузок ----------
    def _downloads_dir(self) -> Optional[Path]:
        """Папка загрузок для слежения или None, если следить не за чем."""
        d = self.config.get('downloads_dir_resolved')
        if not d:
            return None
        try:
            d = Path(d)
        except TypeError:
            return None
        if not d.is_dir():
            return None
        # Не дублировать, если папка загрузок совпадает с корнем.
        try:
            if d.resolve() == self.tree_root.resolve():
                return None
        except OSError:
            pass
        return d

    def downloads_watch_active(self) -> bool:
        """Идёт ли сейчас слежение за папкой загрузок."""
        return self.downloads_observer is not None

    def _download_candidates(self) -> List[Path]:
        """Файлы в папке загрузок, с которыми работает сервис.

        Список отсортирован по времени изменения: свежие — первыми.
        """
        d = self._downloads_dir()
        if d is None:
            return []
        interesting = DownloadsHandler._INTERESTING_SUFFIXES
        found: List[Tuple[float, Path]] = []
        try:
            for p in d.iterdir():
                if not p.is_file():
                    continue
                if p.suffix.lower() not in interesting:
                    continue
                try:
                    found.append((p.stat().st_mtime, p))
                except OSError:
                    continue
        except OSError as e:
            self.log(f'[ЗАГРУЗКИ] Не прочитать папку: {e}', 'warn')
            return []
        found.sort(key=lambda item: item[0], reverse=True)
        return [p for _, p in found]

    def _start_downloads_watch(self):
        """Запускает слежение за downloads_dir, если оно включено.

        Пишет в журнал понятный итог: слежение включено, выключено или
        папка недоступна — иначе непонятно, почему нет уведомлений.
        """
        self._stop_downloads_watch()

        if not self._cfg_bool(self.config, 'watch_downloads', False):
            self.log('[ЗАГРУЗКИ] Слежение выключено '
                     '(watch_downloads = false в F10)', 'cfg')
            return

        configured = self.config.get('downloads_dir') or '~'
        d = self.config.get('downloads_dir_resolved')
        if not d:
            self.log(f'[ЗАГРУЗКИ] Папка загрузок не задана в F10: '
                     f'{configured} — слежение не запущено', 'warn')
            return

        d = Path(d)
        if not d.is_dir():
            self.log(f'[ЗАГРУЗКИ] Папки нет: {d} — слежение не запущено. '
                     f'Укажите существующую папку в F10 '
                     f'(«Папка загрузок»)', 'warn')
            return

        if self._downloads_dir() is None:
            # Единственная причина здесь — папка совпала с корнем дерева.
            self.log('[ЗАГРУЗКИ] Папка загрузок совпадает с корнем '
                     'дерева — слежение не нужно', 'cfg')
            return

        try:
            handler = DownloadsHandler(self._on_download_event)
            obs = Observer()
            obs.schedule(handler, str(d), recursive=False)
            obs.start()
            self.downloads_observer = obs
        except Exception as e:
            self.log(f'[ЗАГРУЗКИ] Не запустить наблюдение за {d}: {e}',
                     'error')
            return

        self.log(f'[ЗАГРУЗКИ] Слежение включено: {d} '
                 f'(.gpx, .plt, .wpt; проверка каждые '
                 f'{DOWNLOAD_POLL_MS // 1000} с)', 'cfg')

        # Файлы, скачанные до запуска сервиса, не предлагаем: иначе
        # при каждом старте всплывало бы всё, что лежит в загрузках.
        # Допуск в секунду — из-за гранулярности времени изменения
        # файла: свежий файл может оказаться на доли миллисекунды
        # «старше» метки запуска и без допуска был бы пропущен.
        self._downloads_baseline = time.time() - 1.0

        # Подстраховка к событиям файловой системы.
        self.downloads_poll_timer = QTimer(self)
        self.downloads_poll_timer.setInterval(DOWNLOAD_POLL_MS)
        self.downloads_poll_timer.timeout.connect(
            lambda: self.poll_downloads(ignore_before=self._downloads_baseline))
        self.downloads_poll_timer.start()

    def _stop_downloads_watch(self):
        if self.downloads_poll_timer is not None:
            try:
                self.downloads_poll_timer.stop()
            except Exception:
                pass
            self.downloads_poll_timer = None
        if self.downloads_observer:
            try:
                self.downloads_observer.stop()
                self.downloads_observer.join(timeout=2)
            except Exception:
                pass
            self.downloads_observer = None

    def poll_downloads(self, ignore_before: Optional[float] = None,
                       limit: Optional[int] = None) -> int:
        """Ищет новые файлы в папке загрузок и предлагает перенос.

        Вызывается таймером-подстраховкой и вручную кнопкой
        «Проверить загрузки». ignore_before позволяет не предлагать
        файлы, скачанные до запуска сервиса. Возвращает число файлов,
        по которым предложен перенос.
        """
        offered = 0
        for path in self._download_candidates():
            if path in self._seen_downloads:
                continue
            if limit is not None and offered >= limit:
                return offered
            if ignore_before is not None:
                try:
                    if path.stat().st_mtime < ignore_before:
                        # Скачано до запуска — просто запоминаем.
                        self._seen_downloads.add(path)
                        continue
                except OSError:
                    continue
            self._handle_new_download(path)
            offered += 1
        return offered

    def on_check_downloads(self):
        """Ручная проверка папки загрузок (кнопка «Проверить загрузки»)."""
        d = self._downloads_dir()
        if d is None:
            self.log('[ЗАГРУЗКИ] Слежение недоступно: папка загрузок '
                     'не задана или совпадает с корнем дерева', 'warn')
            return

        files = self._download_candidates()
        if not files:
            self.log(f'[ЗАГРУЗКИ] В {d} нет файлов .gpx, .plt, .wpt', 'info')
            return

        self.log(f'[ЗАГРУЗКИ] В {d} найдено {len(files)}, проверяю',
                 'info')
        offered = self.poll_downloads(limit=10)
        if not offered:
            self.log(f'[ЗАГРУЗКИ] Новых файлов нет: все {len(files)} уже '
                     f'предлагались в этой сессии', 'info')

    def on_watch_downloads_toggled(self, checked: bool):
        """Переключатель слежения за загрузками из меню «Загрузки»."""
        self.config['watch_downloads'] = bool(checked)
        try:
            save_config(self.config)
        except Exception:
            pass
        self._start_downloads_watch()
        self.log(f'[ЗАГРУЗКИ] Слежение '
                 f'{"включено" if checked else "выключено"}', 'cfg')

    def on_open_downloads_dir(self):
        """Открывает папку загрузок в системном файловом менеджере."""
        d = self.config.get('downloads_dir_resolved')
        if not d or not Path(d).is_dir():
            self.show_notice('Папка загрузок не задана или не существует. '
                             'Укажите её в F10 («Папка загрузок»).', 'warn')
            return
        if not reveal_in_file_manager(Path(d)):
            self.show_notice('Не удалось открыть системный файловый '
                             'менеджер.', 'error')
            return
        self.log(f'[ЗАГРУЗКИ] Открыта папка: {d}', 'info')

    def _on_download_event(self, path: Path):
        """Вызывается из потока watchdog — уходим в главный поток."""
        QTimer.singleShot(0, lambda: self._handle_new_download(path))

    def _handle_new_download(self, path: Path):
        if path in self._seen_downloads:
            return
        self._seen_downloads.add(path)
        # Список только растёт; старые записи не нужны.
        if len(self._seen_downloads) > 400:
            self._seen_downloads = {path}

        # Небольшая задержка: дать браузеру дописать файл и
        # отпустить блокировку.
        QTimer.singleShot(1200,
                          lambda: self._offer_move_download(path))

    def _offer_move_download(self, path: Path):
        if not path.exists() or not path.is_file():
            return

        self.log(f'[ЗАГРУЗКИ] Новый файл: {path.name}', 'info')

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Question)
        msg.setWindowTitle('Новый файл в загрузках')
        msg.setText(f'Обнаружен новый файл:\n{path.name}')
        msg.setInformativeText(
            f'Перенести его в дерево?\n{self.tree_root}')

        btn_move = msg.addButton('Переместить в корень',
                                 QMessageBox.AcceptRole)
        btn_choose = msg.addButton('Выбрать папку…',
                                   QMessageBox.ActionRole)
        btn_skip = msg.addButton('Игнорировать', QMessageBox.RejectRole)
        set_min_button_widths(msg, [btn_move, btn_choose, btn_skip])
        msg.setDefaultButton(btn_move)
        msg.exec()

        clicked = msg.clickedButton()
        if clicked is btn_skip:
            return

        if clicked is btn_move:
            target_dir = self.tree_root
        else:
            dlg = MoveDialog(self, path, self.tree_root)
            if dlg.exec() != QDialog.Accepted or dlg.result is None:
                return
            target_dir = dlg.result

        dst = target_dir / path.name
        if dst.exists():
            self.log(f'[ОШБ] Уже существует: {dst}', 'error')
            return
        try:
            shutil.move(str(path), str(dst))
            self.log(f'[ЗАГРУЗКИ] {path.name} → {target_dir}', 'ok')
        except OSError as e:
            self.log(f'[ОШБ] Перенос: {e}', 'error')
            return
        self.rebuild_tree()

    def _on_fs_event(self, _path: Optional[Path]):
        QTimer.singleShot(0, self._schedule_refresh)

    def _schedule_refresh(self):
        if self._refresh_pending:
            return
        self._refresh_pending = True
        QTimer.singleShot(200, self._do_refresh)

    def _do_refresh(self):
        self._refresh_pending = False
        self.rebuild_tree()

    # ---------- дерево ----------
    def rebuild_tree(self):
        self.tree_model.refresh()
        self._restore_expansion()
        self.path_edit.setText(str(self.tree_root))
        self.update_status()

    def _restore_expansion(self):
        for p in sorted(self.tree_model.expanded_paths,
                        key=lambda x: len(x.parts)):
            item = self.tree_model.find_item(p)
            if item is None:
                continue
            idx = self.tree_model.indexFromItem(item)
            self.tree_view.setExpanded(idx, True)

    def _selected_path(self) -> Optional[Path]:
        idx = self.tree_view.currentIndex()
        if not idx.isValid():
            return None
        item = self.tree_model.itemFromIndex(idx)
        if item is None:
            return None
        p_str = item.data(ROLE_PATH)
        if not p_str:
            return None
        return Path(p_str)

    def _path_from_index(self, index: QModelIndex) -> Optional[Path]:
        item = self.tree_model.itemFromIndex(index)
        if item is None:
            return None
        p_str = item.data(ROLE_PATH)
        return Path(p_str) if p_str else None

    def on_tree_expanded(self, index: QModelIndex):
        item = self.tree_model.itemFromIndex(index)
        if item is None:
            return
        p_str = item.data(ROLE_PATH)
        if not p_str:
            return
        self.tree_model.expanded_paths.add(Path(p_str))
        self.tree_model.load_children_if_stub(item)

    def on_tree_collapsed(self, index: QModelIndex):
        item = self.tree_model.itemFromIndex(index)
        if item is None:
            return
        p_str = item.data(ROLE_PATH)
        if not p_str:
            return
        self.tree_model.expanded_paths.discard(Path(p_str))

    def on_tree_double_click(self, index: QModelIndex):
        path = self._path_from_index(index)
        if path is None:
            return
        if path.is_dir():
            item = self.tree_model.itemFromIndex(index)
            if item is not None:
                self.tree_model.load_children_if_stub(item)
        else:
            self.show_file_menu(index)

    def on_tree_activated(self, index: QModelIndex):
        path = self._path_from_index(index)
        if path is None:
            return
        if path.is_dir():
            self.set_tree_root(path)

    def on_tree_context_menu(self, pos):
        index = self.tree_view.indexAt(pos)
        if not index.isValid():
            return
        path = self._path_from_index(index)
        if path is None:
            return

        self.show_file_menu(index, global_pos=self.tree_view.viewport()
                            .mapToGlobal(pos))

    def build_element_menu(self, index: QModelIndex, path: Path) -> QMenu:
        """Собирает меню действий, доступных для элемента дерева.

        Используется и правым кликом, и двойным кликом по файлу.
        """
        menu = QMenu(self)
        if path.is_dir():
            a = menu.addAction('Сделать корнем дерева')
            a.triggered.connect(lambda: self.set_tree_root(path))
            a2 = menu.addAction('Раскрыть / скрыть')
            a2.triggered.connect(
                lambda: self.tree_view.setExpanded(
                    index, not self.tree_view.isExpanded(index)))
            menu.addSeparator()
        a3 = menu.addAction('Копировать имя')
        a3.triggered.connect(self.on_copy_name)
        a4 = menu.addAction('Копировать путь')
        a4.triggered.connect(self.on_copy_path)
        a5 = menu.addAction('Показать в проводнике')
        a5.triggered.connect(self.on_reveal_path)
        if path.is_file():
            menu.addSeparator()
            menu.addAction('Переименовать…', self.on_rename)
            menu.addAction('Переместить…', self.on_move)
            menu.addAction('Открыть', self.on_open_selected)
        return menu

    def show_file_menu(self, index: QModelIndex, global_pos=None):
        """Показывает меню действий для файла (двойной клик по файлу).

        Пункт «Открыть» внутри меню открывает файл системным приложением.
        """
        path = self._path_from_index(index)
        if path is None:
            return

        self.tree_view.setCurrentIndex(index)

        if global_pos is None:
            rect = self.tree_view.visualRect(index)
            global_pos = self.tree_view.viewport().mapToGlobal(rect.center())

        menu = self.build_element_menu(index, path)
        try:
            menu.exec(global_pos)
        finally:
            self._dispose_menu(menu)

    @staticmethod
    def _dispose_menu(menu):
        """Освобождает меню после показа.

        У QMenu (и его замены в тестах) может не быть deleteLater —
        освобождение в этом случае лишь необязательная уборка.
        """
        delete_later = getattr(menu, 'deleteLater', None)
        if callable(delete_later):
            delete_later()

    def set_tree_root(self, p: Path, save: bool = True):
        try:
            p = p.resolve()
        except Exception:
            return
        if not p.is_dir():
            return
        self.tree_root = p
        self.watch_dir = p
        self.config['root_dir'] = str(p)
        if save:
            try:
                save_config(self.config)
            except Exception:
                pass
        self._start_watch()
        self.tree_model.set_root(p)
        self._restore_expansion()
        self.path_edit.setText(str(p))
        self.scan_dir(p)
        self.update_status()

        if (self.tabs.currentIndex() == 1
                and self.follow_checkbox.isChecked()):
            self._on_tab_changed(1)

    # ---------- статус ----------
    def update_status(self):
        total = 0
        errors = 0
        uploaded = 0

        for p in self._iter_loaded_files():
            total += 1
            info = analyze_file(p)
            if info['has_error']:
                errors += 1
            if self._is_uploaded_to_ftp(p):
                uploaded += 1

        self._set_counters(total, errors, uploaded)
        self.path_label.setText(str(self.tree_root))

    def _iter_loaded_files(self):
        def walk(parent: QStandardItem):
            for row in range(parent.rowCount()):
                child = parent.child(row)
                p_str = child.data(ROLE_PATH)
                if p_str:
                    p = Path(p_str)
                    if p.is_file():
                        yield p
                yield from walk(child)
        root = self.tree_model.invisibleRootItem()
        yield from walk(root)

    def _is_uploaded_to_ftp(self, path: Path) -> bool:
        f = self.tree_model.ftp_sync_folder
        if not f or '/' in f:
            return False
        if path.parent.name != f:
            return False
        return path.name in self.tree_model.ftp_sync_names

    # ---------- навигация ----------
    def on_up(self):
        parent = self.tree_root.parent
        if parent == self.tree_root:
            return
        self.set_tree_root(parent)

    def on_browse_dir(self):
        d = QFileDialog.getExistingDirectory(
            self, 'Выберите директорию', str(self.tree_root))
        if not d:
            return
        self.set_tree_root(Path(d))

    def on_path_entered(self):
        text = self.path_edit.text().strip()
        p = Path(text).expanduser()
        if not p.is_dir():
            QMessageBox.critical(self, 'Ошибка', f'Не директория:\n{p}')
            return
        self.set_tree_root(p)

    def on_refresh(self):
        self.rebuild_tree()
        self.scan_dir(self.tree_root)

    # ---------- scan ----------
    def scan_dir(self, path: Path):
        if not path.is_dir():
            return
        problems = 0
        for p in sorted(path.iterdir()):
            if not p.is_file():
                continue
            info = analyze_file(p)
            if info['has_error']:
                problems += 1
                tag = {'high': 'правка', 'medium': 'правка',
                       'low': 'правка?', 'none': '—'}[info['confidence']]
                self.log(f'[!] {p.name} — {info["reason"]} [{tag}]', 'warn')
        if problems:
            self.log(f'[КАТАЛОГ] Проблем: {problems}', 'info')
        else:
            self.log('[КАТАЛОГ] Все имена корректны', 'ok')

    # ---------- вкладки ----------
    def _files_tab_active(self) -> bool:
        return self.tabs.currentIndex() == 0

    def _ftp_tab_active(self) -> bool:
        return self.tabs.currentIndex() == 1

    def _warn_wrong_tab(self, action: str) -> bool:
        """Предупреждает, если файловая команда вызвана на вкладке FTP.

        Горячие клавиши висят на окне, поэтому без этой проверки F2 на
        вкладке FTP открывал бы переименование локального файла.
        Возвращает True, если действие нужно прервать.
        """
        if self._files_tab_active():
            return False
        self.show_notice(
            f'«{action}» работает с файлами на вкладке «Файлы». '
            f'Переключитесь на неё (Ctrl+1).', 'warn')
        return True

    def _on_tab_changed(self, idx: int):
        if idx != 1:
            return
        if not self.follow_checkbox.isChecked():
            return

        start: Optional[Path] = None
        sel = self._selected_path()
        if sel is not None:
            start = sel.parent if sel.is_file() else sel
        if start is None:
            start = self.tree_root

        folder, found = find_target_folder(start)
        if not found:
            self.log(
                f'[FTP] Автоподстановка: начиная от «{start}» не найдено '
                f'имя вида ГГГГ-ММ-ДД_Место. Выделите нужную папку или '
                f'введите путь вручную.', 'warn')
            return
        if folder == self.ftp_folder and self.ftp_connected:
            return
        if folder == self.ftp_path_edit.text().strip() and self.ftp_loading:
            return

        self.ftp_path_edit.setText(folder)
        self.log(f'[FTP] Автоподстановка папки: {folder} '
                 f'(источник: {start})', 'cfg')
        self.ftp_load(folder)

    def _on_follow_changed(self, checked: bool):
        self.log(f'[FTP] Слежение за папкой из «Файлы»: '
                 f'{"вкл" if checked else "выкл"}', 'cfg')

    # ---------- операции с файлами ----------
    def on_new_folder(self):
        dlg = NewFolderDialog(self, self.config, self.tree_root)
        if dlg.exec() != QDialog.Accepted or dlg.result is None:
            return
        target = dlg.result
        if target.exists():
            QMessageBox.critical(self, 'Ошибка',
                                 f'Уже существует: {target.name}')
            return
        try:
            target.mkdir(parents=False, exist_ok=False)
            self.log(f'[OK] Создана папка: {target}', 'ok')
        except OSError as e:
            self.log(f'[ОШБ] Не создать папку: {e}', 'error')
            return
        if dlg.with_10_tracks:
            sub = target / '10-Tracks'
            try:
                sub.mkdir(parents=False, exist_ok=False)
                self.log(f'[OK] Создана папка: {sub}', 'ok')
            except OSError as e:
                self.log(f'[ОШБ] Не создать 10-Tracks: {e}', 'error')
        self.rebuild_tree()

    def on_rename(self):
        if self._warn_wrong_tab('Переименовать'):
            return
        p = self._selected_path()
        if p is None or not p.is_file():
            QMessageBox.information(self, 'Переименование',
                                    'Выберите файл')
            return
        dlg = RenameDialog(self, p)
        if dlg.exec() != QDialog.Accepted or dlg.result is None:
            return
        new_name = dlg.result
        if new_name == p.name:
            return
        new_path = p.with_name(new_name)
        if new_path.exists():
            QMessageBox.critical(self, 'Ошибка',
                                 f'Уже существует: {new_name}')
            return
        try:
            p.rename(new_path)
            self.log(f'[OK] {p.name} -> {new_name}', 'ok')
        except OSError as e:
            self.log(f'[ОШБ] {e}', 'error')
            return
        self.rebuild_tree()

    def on_move(self):
        if self._warn_wrong_tab('Переместить'):
            return
        p = self._selected_path()
        if p is None or not p.is_file():
            QMessageBox.information(self, 'Перемещение', 'Выберите файл')
            return
        dlg = MoveDialog(self, p, self.tree_root)
        if dlg.exec() != QDialog.Accepted or dlg.result is None:
            return
        dst_dir = dlg.result
        dst = dst_dir / p.name
        if dst.exists():
            QMessageBox.critical(self, 'Ошибка', f'Уже существует: {dst}')
            return
        try:
            shutil.move(str(p), str(dst))
            self.log(f'[ПЕРЕНОС] {p.name} -> {dst_dir}', 'ok')
        except OSError as e:
            self.log(f'[ОШБ] Перенос: {e}', 'error')
            return
        self.rebuild_tree()

    def on_copy_name(self):
        p = self._selected_path()
        if p is None:
            idx = self.ftp_view.currentIndex()
            if idx.isValid():
                item = self.ftp_model.itemFromIndex(idx)
                if item is not None:
                    name = (item.data(ROLE_FTP_NAME)
                            or item.text())
                    if copy_to_clipboard(name):
                        self.log(f'[БУФЕР] Скопировано: {name}', 'ok')
                    else:
                        self.log('[ОШБ] Буфер обмена недоступен', 'error')
            return
        if copy_to_clipboard(p.name):
            self.log(f'[БУФЕР] Скопировано: {p.name}', 'ok')
        else:
            self.log('[ОШБ] Буфер обмена недоступен', 'error')

    def on_copy_path(self):
        """Копирует полный путь выделенного элемента."""
        p = self._selected_path()
        if p is None:
            self.show_notice('Выберите файл или папку, чтобы скопировать '
                             'путь.', 'info')
            return
        if copy_to_clipboard(str(p)):
            self.log(f'[БУФЕР] Путь скопирован: {p}', 'ok')
        else:
            self.log('[ОШБ] Буфер обмена недоступен', 'error')

    def on_reveal_selected(self):
        p = self._selected_path()
        if p is None:
            return
        if not reveal_in_file_manager(p):
            self._show_notice_or_box(
                'Проводник',
                'Не удалось открыть системный файловый менеджер.', 'error')
            return
        self.log(f'[ОТКР] Проводник: {p}', 'ok')

    def on_reveal_path(self):
        """Пункт контекстного меню: показать элемент в файловом менеджере."""
        self.on_reveal_selected()

    def open_path(self, p: Path):
        try:
            os.startfile(str(p))  # type: ignore[attr-defined]
            self.log(f'[ОТКР] {p.name}', 'ok')
        except Exception as e:
            self.log(f'[ОШБ] Открыть не удалось: {e}', 'error')

    def on_open_selected(self):
        p = self._selected_path()
        if p is None or not p.is_file():
            return
        self.open_path(p)

    def on_apply_fix(self):
        p = self._selected_path()
        if p is None or not p.is_file():
            QMessageBox.information(self, 'Правка', 'Выберите файл')
            return
        info = analyze_file(p)
        if not info['has_error']:
            self.log(f'[i] {p.name} — ок', 'info')
            return
        if not info['new_name']:
            self.log(f'[!] {p.name} — {info["reason"]}', 'warn')
            return
        new_path = p.with_name(info['new_name'])
        if new_path.exists():
            self.log(f'[ОШБ] Уже существует: {info["new_name"]}', 'error')
            return
        try:
            p.rename(new_path)
            self.log(f'[OK] {p.name} -> {info["new_name"]}', 'ok')
        except OSError as e:
            self.log(f'[ОШБ] {e}', 'error')
            return
        self.rebuild_tree()

    def on_fix_all(self):
        files: List[Tuple[Path, str]] = []
        for p in self._iter_loaded_files():
            info = analyze_file(p)
            if (info['has_error'] and info['new_name']
                    and info['confidence'] in ('high', 'medium')):
                files.append((p, info['new_name']))
        if not files:
            self.log('[i] Нет файлов для автоисправления', 'info')
            return
        if QMessageBox.question(
                self, 'Исправить все',
                f'Переименовать {len(files)} файлов?'
        ) != QMessageBox.Yes:
            return
        count = 0
        for path, new_name in files:
            new_path = path.with_name(new_name)
            if new_path.exists():
                self.log(f'[ОШБ] Уже существует: {new_name}', 'error')
                continue
            try:
                path.rename(new_path)
                self.log(f'[OK] {path.name} -> {new_name}', 'ok')
                count += 1
            except OSError as e:
                self.log(f'[ОШБ] {path.name}: {e}', 'error')
        self.log(f'[OK] Исправлено: {count}', 'ok')
        self.rebuild_tree()

    # ---------- FTP ----------
    def on_ftp_connect(self):
        folder = self.ftp_path_edit.text().strip()
        if not folder:
            QMessageBox.information(self, 'FTP', 'Укажите папку на сервере')
            return
        first = folder.split('/')[0]
        ok, reason = validate_folder_name(first)
        if not ok:
            QMessageBox.critical(self, 'FTP',
                                 f'Первый компонент пути: {reason}')
            return
        self.ftp_load(folder)

    def on_ftp_refresh(self):
        folder = self.ftp_path_edit.text().strip()
        if not folder:
            QMessageBox.information(self, 'FTP', 'Укажите папку на сервере')
            return
        self.ftp_load(folder)

    def ftp_load(self, folder: str):
        host = self.config.get('ftp_host', '')
        user = self.config.get('ftp_user', '')
        if not host or not user:
            QMessageBox.critical(
                self, 'FTP',
                'Задайте ftp_host и ftp_user в настройках (F10)')
            return
        if self.ftp_loading:
            return

        port = int(self.config.get('ftp_port', 21))
        password = self.config.get('ftp_password', '')
        base = self.config.get('ftp_path', '/') or '/'

        self.ftp_loading = True
        self.set_status(f'Подключение к FTP: {folder}…')
        self.log(f'[FTP] Обновление папки {folder}', 'cfg')

        task = FtpListTask(self, host, port, user, password, base, folder)
        task.done.connect(self._on_ftp_result)
        task.start()

    @Slot(list, str, str)
    def _on_ftp_result(self, entries, err, folder):
        self.ftp_loading = False
        if err:
            self.log(f'[FTP] Ошибка: {err}', 'error')
            self.set_status('FTP: ошибка')
            self.ftp_connected = False
            self.tree_model.clear_ftp_sync()
            self.ftp_model.set_entries([])
            self.rebuild_tree()
            return

        self.ftp_connected = True
        self.ftp_folder = folder

        self.ftp_model.set_entries(entries)

        if folder and '/' not in folder:
            self.tree_model.set_ftp_sync(
                folder, {n for n, is_d, _ in entries if not is_d})
        else:
            self.tree_model.clear_ftp_sync()

        self.log(f'[FTP] Открыта папка {folder} '
                 f'({len(entries)} элементов)', 'ok')
        self.set_status(f'FTP: {folder}')
        self.rebuild_tree()

    def on_ftp_double_click(self, index: QModelIndex):
        item = self.ftp_model.itemFromIndex(index)
        if item is None:
            return
        name = item.data(ROLE_FTP_NAME)
        if not name:
            return
        entry = self.ftp_model.entry_for_name(name)
        if entry is None:
            return
        _, is_dir, _ = entry
        if is_dir:
            current = self.ftp_path_edit.text().strip()
            new_folder = f'{current}/{name}' if current else name
            self.ftp_path_edit.setText(new_folder)
            self.ftp_load(new_folder)

    def on_ftp_delete_selected(self):
        if self.tabs.currentIndex() != 1:
            return
        index = self.ftp_view.currentIndex()
        if not index.isValid():
            QMessageBox.information(self, 'FTP', 'Выберите файл на сервере')
            return
        item = self.ftp_model.itemFromIndex(index)
        if item is None:
            return
        name = item.data(ROLE_FTP_NAME)
        if not name:
            return
        entry = self.ftp_model.entry_for_name(name)
        if entry is None:
            return
        _, is_dir, _ = entry
        if is_dir:
            QMessageBox.information(self, 'FTP',
                                    'Удаление папок не поддерживается')
            return
        if self.config.get('ftp_readonly', False):
            QMessageBox.information(
                self, 'FTP',
                'Удаление запрещено: ftp_readonly = true')
            return
        if QMessageBox.question(
                self, 'Подтверждение',
                f'Удалить файл на сервере?\n{name}'
        ) != QMessageBox.Yes:
            return

        host = self.config.get('ftp_host', '')
        port = int(self.config.get('ftp_port', 21))
        user = self.config.get('ftp_user', '')
        password = self.config.get('ftp_password', '')
        base = self.config.get('ftp_path', '/') or '/'
        folder = self.ftp_folder

        task = FtpDeleteTask(self, host, port, user, password,
                             base, folder, name)
        task.finished.connect(self._on_ftp_delete_done)
        task.start()

    @Slot(str, str, str)
    def _on_ftp_delete_done(self, folder: str, name: str, err: str):
        if err:
            self.log(f'[FTP] Ошибка удаления {name}: {err}', 'error')
        else:
            self.log(f'[FTP] Удалён: {folder}/{name}', 'ok')
            if self.tree_model.ftp_sync_folder == folder:
                self.tree_model.ftp_sync_names.discard(name)
                self.rebuild_tree()
        self.ftp_load(folder)

    def on_ftp_upload_here(self):
        folder = self.ftp_path_edit.text().strip()
        if not folder:
            QMessageBox.information(
                self, 'FTP', 'Сначала укажите папку на сервере')
            return
        path, _ = QFileDialog.getOpenFileName(
            self, 'Файл для загрузки на FTP', str(self.tree_root))
        if not path:
            return
        p = Path(path)
        info = analyze_file(p)
        if not is_uploadable(p, info):
            reason = upload_reason_for(p, info)
            QMessageBox.critical(
                self, 'FTP',
                f'Нельзя отправить {p.name}\n{reason}\n\n'
                f'Разрешены: {upload_extensions_hint()}')
            return
        self.do_upload(p, folder)

    def on_upload_selected(self):
        if self._warn_wrong_tab('Загрузить на FTP'):
            return
        p = self._selected_path()
        if p is None or not p.is_file():
            QMessageBox.information(self, 'FTP', 'Выберите файл')
            return
        info = analyze_file(p)
        if not is_uploadable(p, info):
            # Предсказуемый отказ: не модальное окно, а плашка.
            reason = upload_reason_for(p, info)
            self.log(f'[FTP] {p.name} — {reason}', 'warn')
            self._show_notice_or_box(
                'FTP',
                f'Нельзя отправить {p.name}: {reason}. '
                f'Разрешены: {upload_extensions_hint()}')
            return
        folder, found = find_target_folder(p.parent)
        if not found:
            folder, ok = QInputDialog.getText(
                self, 'FTP',
                'Папка не найдена по имени. Введите вручную '
                '(ГГГГ-ММ-ДД_Место):')
            folder = folder.strip()
            if not ok or not folder:
                return
        ok, reason = validate_folder_name(folder)
        if not ok:
            self._show_notice_or_box('FTP', reason)
            return
        base = self.config.get('ftp_path', '/') or '/'
        ok, reason = validate_upload_target(base, folder)
        if not ok:
            self._show_notice_or_box('FTP', reason)
            return
        self.hide_notice()
        self.do_upload(p, folder)

    def do_upload(self, src: Path, folder: str):
        host = self.config.get('ftp_host', '')
        port = int(self.config.get('ftp_port', 21))
        user = self.config.get('ftp_user', '')
        password = self.config.get('ftp_password', '')
        base = self.config.get('ftp_path', '/') or '/'
        readonly = bool(self.config.get('ftp_readonly', False))

        if readonly:
            self.log(f'[FTP] [эмуляция] {src.name} → {base}/{folder}/',
                     'cfg')
        else:
            self.log(f'[FTP] Начинаю загрузку {src.name} → '
                     f'{base}/{folder}/', 'cfg')

        task = FtpUploadTask(self, host, port, user, password,
                             base, folder, src, readonly, self.log)
        task.finished.connect(self._on_upload_done)
        task.start()

    @Slot(str, str, str)
    def _on_upload_done(self, folder: str, filename: str, err: str):
        if err:
            self.log(f'[FTP] Ошибка: {err}', 'error')
            return
        if not folder:
            return
        self.log(f'[FTP] Успешно: {folder}/{filename}', 'ok')
        if '/' not in folder and \
                self.tree_model.ftp_sync_folder == folder:
            self.tree_model.ftp_sync_names.add(filename)
            self.rebuild_tree()

    # ---------- настройки / справка ----------
    def open_settings(self):
        dlg = SettingsDialog(self, self.config)
        if dlg.exec() == QDialog.Accepted:
            self._on_settings_saved()

    def _on_settings_saved(self):
        try:
            save_config(self.config)
        except Exception as e:
            self.log(f'[НАСТР] Не сохранить конфиг: {e}', 'error')
            return
        self.log(f'[НАСТР] Сохранено: {config_path()}', 'ok')
        self.apply_theme()
        # Режим эмуляции мог измениться прямо из F10.
        self._sync_ftp_actions()

        # Настройки могли изменить видимость журнала прямо из F10.
        self.on_settings_saved_log_visibility()

        # Слежение за загрузками применяем всегда: могли сменить саму
        # папку, а не только флажок.
        self._start_downloads_watch()
        self._sync_downloads_actions()

        new_root = Path(self.config['root_dir']).expanduser().resolve()
        if new_root.is_dir() and new_root != self.tree_root:
            self.set_tree_root(new_root)

    def open_help(self):
        dlg = HelpDialog(self, self.config)
        dlg.exec()
        try:
            save_config(self.config)
        except Exception:
            pass

    def open_about(self):
        QMessageBox.information(
            self, 'О программе',
            'ok-helper (Qt GUI)\n\n'
            'Транслит и умная проверка имён файлов; работа с FTP.\n'
            'Интерфейс на PySide6.\n\n'
            f'Конфиг: {config_path()}')

    # ---------- close ----------
    def closeEvent(self, event):
        self._save_ui_state()
        self._stop_watch()
        self._stop_downloads_watch()
        super().closeEvent(event)

# ====================== диалоги ======================

class SettingsDialog(QDialog):
    def __init__(self, parent, config: dict):
        super().__init__(parent)
        self.setWindowTitle('Настройки')
        # Минимум — под маленькие экраны; удобный размер задаётся resize.
        self.setMinimumWidth(440)
        self.resize(520, 640)
        self.config = config

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.fields: Dict[str, QLineEdit] = {}

        def add_field(form: QFormLayout, label: str, key: str,
                      password=False):
            e = QLineEdit(str(config.get(key, '')))
            if password:
                e.setEchoMode(QLineEdit.Password)
            self.fields[key] = e
            form.addRow(label, e)

        def section(title: str) -> QVBoxLayout:
            """Заголовок раздела и карточка под ним."""
            lbl = QLabel(title)
            lbl.setObjectName('SectionTitle')
            layout.addWidget(lbl)

            card = QFrame()
            card.setObjectName('Card')
            inner = QVBoxLayout(card)
            inner.setContentsMargins(14, 12, 14, 12)
            inner.setSpacing(10)
            layout.addWidget(card)
            return inner

        # --- Папки ---
        folders = section('Папки')
        folders_form = QFormLayout()
        folders_form.setSpacing(8)
        add_field(folders_form, 'Корневая папка', 'root_dir')
        add_field(folders_form, 'Папка загрузок', 'downloads_dir')
        folders.addLayout(folders_form)

        self.watch_cb = QCheckBox('Следить за папкой загрузок')
        self.watch_cb.setChecked(bool(config.get('watch_downloads', False)))
        self.watch_cb.setToolTip(
            'Предлагать переносить новые файлы из папки загрузок')
        folders.addWidget(self.watch_cb)

        # --- Интерфейс ---
        ui = section('Интерфейс')

        theme_row = QHBoxLayout()
        theme_row.setSpacing(8)
        theme_row.addWidget(QLabel('Тема:'))
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(['auto', 'light', 'dark'])
        self.theme_combo.setCurrentText(config.get('theme', 'auto'))
        self.theme_combo.setToolTip('auto — по системной теме Windows')
        theme_row.addWidget(self.theme_combo)

        dim = QLabel('auto — по системной теме')
        dim.setObjectName('Dim')
        theme_row.addWidget(dim)
        theme_row.addStretch(1)
        ui.addLayout(theme_row)

        self.help_cb = QCheckBox('Показывать справку при старте')
        self.help_cb.setChecked(
            bool(config.get('help_visible_at_start', True)))
        ui.addWidget(self.help_cb)

        self.log_visible_cb = QCheckBox('Показывать панель журнала')
        self.log_visible_cb.setChecked(
            bool(config.get('log_visible', True)))
        self.log_visible_cb.setToolTip('Панель журнала внизу окна (Ctrl+L)')
        ui.addWidget(self.log_visible_cb)

        self.blocking_errors_cb = QCheckBox(
            'Показывать отказы модальным окном (иначе — плашкой сверху)')
        self.blocking_errors_cb.setChecked(
            bool(config.get('blocking_errors', False)))
        ui.addWidget(self.blocking_errors_cb)

        # --- FTP ---
        ftp = section('FTP')
        ftp_form = QFormLayout()
        ftp_form.setSpacing(8)
        add_field(ftp_form, 'Хост', 'ftp_host')
        add_field(ftp_form, 'Порт', 'ftp_port')
        add_field(ftp_form, 'Логин', 'ftp_user')
        add_field(ftp_form, 'Пароль', 'ftp_password', password=True)
        add_field(ftp_form, 'Базовый путь', 'ftp_path')
        ftp.addLayout(ftp_form)

        self.ftp_readonly_cb = QCheckBox('Режим эмуляции (read-only)')
        self.ftp_readonly_cb.setChecked(
            bool(config.get('ftp_readonly', False)))
        self.ftp_readonly_cb.setToolTip(
            'Подключаться и читать листинг, но не создавать, '
            'не загружать и не удалять файлы')
        ftp.addWidget(self.ftp_readonly_cb)

        layout.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText('Сохранить')
        buttons.button(QDialogButtonBox.Save).setObjectName('Accent')
        buttons.button(QDialogButtonBox.Cancel).setText('Отмена')
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self):
        root = self.fields['root_dir'].text().strip()
        if not Path(root).expanduser().is_dir():
            QMessageBox.critical(self, 'Ошибка',
                                 f'root_dir не директория:\n{root}')
            return
        dl = self.fields['downloads_dir'].text().strip()
        if not Path(dl).expanduser().is_dir():
            QMessageBox.critical(self, 'Ошибка',
                                 f'downloads_dir не директория:\n{dl}')
            return
        try:
            port = int(self.fields['ftp_port'].text() or 21)
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            QMessageBox.critical(self, 'Ошибка',
                                 'Некорректный FTP порт')
            return

        theme = self.theme_combo.currentText().strip() or 'auto'
        if theme not in ('auto', 'dark', 'light'):
            theme = 'auto'

        c = self.config
        c['root_dir'] = root
        c['downloads_dir'] = dl
        c['watch_downloads'] = bool(self.watch_cb.isChecked())
        c['theme'] = theme
        c['help_visible_at_start'] = bool(self.help_cb.isChecked())
        c['log_visible'] = bool(self.log_visible_cb.isChecked())
        c['blocking_errors'] = bool(self.blocking_errors_cb.isChecked())
        c['ftp_host'] = self.fields['ftp_host'].text().strip()
        c['ftp_port'] = port
        c['ftp_user'] = self.fields['ftp_user'].text().strip()
        c['ftp_password'] = self.fields['ftp_password'].text()
        c['ftp_path'] = self.fields['ftp_path'].text().strip() or '/'
        c['ftp_readonly'] = bool(self.ftp_readonly_cb.isChecked())
        c['root_dir_resolved'] = Path(root).expanduser().resolve()
        c['downloads_dir_resolved'] = Path(dl).expanduser().resolve()

        self.accept()


class HelpDialog(QDialog):
    HELP_TEXT = """\
Горячие клавиши:
  F1        Справка
  Ctrl+O    Выбрать директорию
  F5        Обновить список файлов
  Ctrl+F5   Исправить все файлы
  F2        Переименовать выделенный файл
  Ctrl+N    Создать папку
  Ctrl+X    Переместить файл в другую папку
  Ctrl+Y    Копировать имя выделенного
  Ctrl+Shift+C  Копировать полный путь
  Ctrl+E    Показать файл в проводнике
  Ctrl+L    Показать / скрыть панель журнала
  Ctrl+D    Проверить папку загрузок
  Ctrl+1    Вкладка «Файлы»
  Ctrl+2    Вкладка «FTP»
  F12       Загрузить файл на FTP
  Delete    Удалить файл на FTP (во вкладке FTP)
  F10       Настройки

Команды файлов (F2, Ctrl+X, Ctrl+Y, F12 и другие) работают на
вкладке «Файлы». Если вызвать их на вкладке FTP, появится
напоминание над списком, а не диалог для локального файла.

Работа с деревом «Файлы»:
  Двойной клик по папке  — раскрыть / свернуть
  Двойной клик по файлу  — меню доступных действий, файл открывает
                           пункт «Открыть» в этом меню
  Enter на папке         — сделать корнем дерева
  Правый клик            — контекстное меню: открыть, переименовать,
                           переместить, копировать имя или путь,
                           показать в проводнике

Журнал слева:
  Панель можно показать или скрыть панель журнала кнопкой «Журнал»
  на панели инструментов (Ctrl+L) — состояние запоминается между
  запусками. Журнал сам прокручивается вниз только если вы уже
  внизу, поэтому чтение истории не срывается. Ctrl+Shift+L копирует
  журнал целиком, «Правка → Сохранить журнал в файл…» пишет его
  в .txt.

Размеры окна, разделитель и ширины колонок запоминаются в конфиге
при закрытии окна.

Папка загрузок:
  Слежение включается в F10 («Следить за папкой загрузок») или в меню
  «Загрузки». Когда в папке появляется новый .gpx, .plt или .wpt,
  сервис предлагает перенести его в корень дерева или выбрать папку.
  Временные файлы браузеров (.crdownload, .part и подобные) не
  считаются готовыми и не предлагаются.

  Папка проверяется и по событиям системы, и дополнительно каждые
  несколько секунд — события могут не дойти на сетевом диске,
  в OneDrive или после сна машины. Кнопка «Проверить загрузки»
  (Ctrl+D) запускает проверку вручную; «Загрузки → Открыть папку
  загрузок» открывает саму папку.

  Файлы, скачанные до запуска сервиса, не предлагаются — иначе при
  каждом старте всплывало бы всё содержимое папки. Итог слежения
  всегда виден в журнале: включено, выключено или папка недоступна.

FTP — автоподстановка пути:
  На вкладке FTP есть чек-бокс «Следить за папкой из «Файлы»».
  Если он включён, при переходе на вкладку FTP сервис ищет вверх по
  дереву папку вида ГГГГ-ММ-ДД_Место от выделенного элемента (или от
  корня, если ничего не выделено). Если находит — подставляет её в
  путь FTP и грузит листинг.

Правила именования:
  .gpx / .plt — формат ГГГГММДД_Позывной.
    – первая буква позывного — заглавная (lisa → Lisa);
    – хвостовой номер — минимум 2 разряда, разделитель _/- перед
      цифрами убирается:
        lisa_1        → Lisa01
        lisa1         → Lisa01
        lisa-2        → Lisa02
        lisa_12       → Lisa12
      то же для номера в середине:
        Lisa_03_Test  → Lisa03_Test
        Lisa_04_1     → Lisa04_1
    – если база уже оканчивается цифрой — хвостовая группа _N/-N не
      трогается: Lisa01_1 → Lisa01_1;
    – дата может быть взята из содержимого .gpx (последняя <time>),
      если её нет в имени;
    – имена вида <1-4 цифры>m.gpx (100m.gpx) не проверяются.

  .wpt — только Waypoints_ГГГГММДД.
  Остальные расширения не проверяются.

FTP — что можно отправить:
  • .plt — с корректным именем;
  • .wpt — только Waypoints_ГГГГММДД;
  • .gpx — только <1-4 цифры>m.gpx;
  • файлы из папки 10-Tracks не отправляются;
  • папка назначения обязательна (залить в корень нельзя).

FTP-синхронизация:
  Файлы, залитые на FTP, помечаются в дереве «Файлы» значком ✓.
  Сверка выполняется при каждой загрузке листинга FTP и после каждой
  успешной загрузки файла.

Режим эмуляции FTP:
  ftp_readonly = true — сервис реально подключается и читает папки,
  но не создаёт папки, не загружает и не удаляет файлы.

Тема интерфейса:
  Настройка «theme» в F10:
    auto  — по системной теме Windows;
    light — светлая;
    dark  — тёмная.
"""

    def __init__(self, parent, config: dict):
        super().__init__(parent)
        self.setWindowTitle('Справка')
        self.setMinimumSize(520, 360)
        self.resize(760, 620)
        self.config = config

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        self.show_cb = QCheckBox('Показывать при старте')
        self.show_cb.setChecked(
            bool(config.get('help_visible_at_start', True)))
        self.show_cb.toggled.connect(
            lambda v: self.config.__setitem__(
                'help_visible_at_start', bool(v)))
        layout.addWidget(self.show_cb)

        edit = QPlainTextEdit()
        edit.setPlainText(self.HELP_TEXT)
        edit.setReadOnly(True)
        layout.addWidget(edit, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText('Закрыть')
        buttons.rejected.connect(self.accept)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class RenameDialog(QDialog):
    def __init__(self, parent, path: Path):
        super().__init__(parent)
        self.setWindowTitle('Переименование')
        self.setMinimumWidth(420)
        self.path = path
        self.result: Optional[str] = None

        info = analyze_file(path)
        self.suggestion = info['new_name'] or path.name
        changed = self.suggestion != path.name

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(8)

        lbl_old = QLabel(path.name)
        lbl_old.setObjectName('Dim')
        form.addRow('Было:', lbl_old)

        # Предложение и итоговое поле визуально разные: раньше обе
        # строки выглядели одинаково, и правка была незаметна.
        if changed:
            lbl_sugg = QLabel(self.suggestion)
            lbl_sugg.setObjectName('Suggestion')
        else:
            lbl_sugg = QLabel('имя уже корректно')
            lbl_sugg.setObjectName('Dim')
        form.addRow('Предложение:', lbl_sugg)

        self.edit = QLineEdit(self.suggestion)
        self.edit.selectAll()
        form.addRow('Новое имя:', self.edit)

        layout.addLayout(form)

        if not changed:
            note = QLabel('Файл уже назван по правилам — можно оставить как есть.')
            note.setObjectName('Dim')
            layout.addWidget(note)

        hint = QLabel('Ctrl+T — транслит + нормализация')
        hint.setObjectName('Dim')
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText('Применить')
        buttons.button(QDialogButtonBox.Cancel).setText('Отмена')
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        act = QAction(self)
        act.setShortcut(QKeySequence('Ctrl+T'))
        act.triggered.connect(self._translit)
        self.addAction(act)

        self.edit.setFocus()

    def _translit(self):
        text = self.edit.text()
        new = transliterate(text)
        stem, dot, suffix = new.partition('.')
        if '_' in stem:
            date, _, callsign = stem.partition('_')
            if date.isdigit() and len(date) == 8 and callsign:
                callsign = normalize_callsign(sanitize_callsign(callsign))
                new = f'{date}_{callsign}{dot}{suffix}'
        self.edit.setText(new)

    def _apply(self):
        value = self.edit.text().strip()
        if not value:
            return
        self.result = value
        self.accept()


class NewFolderDialog(QDialog):
    def __init__(self, parent, config: dict, default_dir: Path):
        super().__init__(parent)
        self.setWindowTitle('Новая папка')
        self.setMinimumWidth(420)
        self.config = config
        self.default_dir = default_dir
        self.result: Optional[Path] = None
        self.with_10_tracks = False

        is_root = False
        root_dir = config.get('root_dir_resolved')
        if root_dir:
            try:
                is_root = (Path(root_dir).resolve()
                           == default_dir.resolve())
            except Exception:
                is_root = False
        self.is_root = is_root

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        form = QFormLayout()
        form.setSpacing(8)

        lbl_dir = QLabel(str(default_dir))
        lbl_dir.setObjectName('Dim')
        form.addRow('Где:', lbl_dir)

        initial = datetime.now().strftime('%Y-%m-%d_') if is_root else ''
        self.edit = QLineEdit(initial)
        self.edit.setFocus()
        form.addRow('Имя:', self.edit)
        layout.addLayout(form)

        hint = QLabel('Ctrl+T — транслит в латиницу')
        hint.setObjectName('Dim')
        layout.addWidget(hint)

        if is_root:
            self.tracks_cb = QCheckBox('Создать внутри 10-Tracks')
            self.tracks_cb.setChecked(False)
            layout.addWidget(self.tracks_cb)
        else:
            self.tracks_cb = None

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText('Создать')
        buttons.button(QDialogButtonBox.Cancel).setText('Отмена')
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        act = QAction(self)
        act.setShortcut(QKeySequence('Ctrl+T'))
        act.triggered.connect(
            lambda: self.edit.setText(transliterate(self.edit.text())))
        self.addAction(act)

    def _apply(self):
        value = self.edit.text().strip()
        if not value:
            return
        ok, reason = validate_new_folder_name(value)
        if not ok:
            QMessageBox.critical(self, 'Ошибка', reason)
            return
        self.result = self.default_dir / value
        if self.tracks_cb is not None:
            self.with_10_tracks = bool(self.tracks_cb.isChecked())
        self.accept()


class MoveDialog(QDialog):
    def __init__(self, parent, src: Path, initial_dir: Path):
        super().__init__(parent)
        self.setWindowTitle('Перемещение файла')
        self.setMinimumSize(420, 320)
        self.resize(620, 480)
        self.src = src
        self.result: Optional[Path] = None
        self.current_dir = initial_dir.resolve()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        lbl_file = QLabel(f'Файл: {src.name}')
        f = lbl_file.font()
        f.setBold(True)
        lbl_file.setFont(f)
        layout.addWidget(lbl_file)

        lbl_from = QLabel(f'Из: {src.parent}')
        lbl_from.setObjectName('Dim')
        layout.addWidget(lbl_from)

        nav = QHBoxLayout()
        btn_up = QPushButton('↑ Вверх')
        btn_up.clicked.connect(self._up)
        nav.addWidget(btn_up)
        self.path_lbl = QLabel(str(self.current_dir))
        self.path_lbl.setObjectName('Dim')
        nav.addWidget(self.path_lbl, 1)
        layout.addLayout(nav)

        self.model = QStandardItemModel(self)
        self.model.setHorizontalHeaderLabels(['Имя', 'Тип'])
        self.tree = QTreeView()
        self.tree.setModel(self.model)
        self.tree.setUniformRowHeights(True)
        self.tree.doubleClicked.connect(self._enter)
        self.tree.activated.connect(self._enter)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        layout.addWidget(self.tree, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText('Переместить сюда')
        buttons.button(QDialogButtonBox.Cancel).setText('Отмена')
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._populate()

    def _populate(self):
        self.model.removeRows(0, self.model.rowCount())
        self.path_lbl.setText(str(self.current_dir))
        style = QApplication.style()
        try:
            children = sorted(
                self.current_dir.iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
        except OSError:
            return
        for p in children:
            name_item = QStandardItem(p.name)
            name_item.setEditable(False)
            name_item.setData(str(p), Qt.UserRole + 3)
            if p.is_dir():
                name_item.setIcon(style.standardIcon(QStyle.SP_DirIcon))
                type_item = QStandardItem('папка')
            else:
                name_item.setIcon(style.standardIcon(QStyle.SP_FileIcon))
                type_item = QStandardItem('файл')
            type_item.setEditable(False)
            self.model.appendRow([name_item, type_item])

    def _up(self):
        parent = self.current_dir.parent
        if parent == self.current_dir:
            return
        self.current_dir = parent
        self._populate()

    def _enter(self, index: QModelIndex):
        item = self.model.itemFromIndex(index)
        if item is None:
            return
        p_str = item.data(Qt.UserRole + 3)
        if not p_str:
            return
        p = Path(p_str)
        if p.is_dir():
            self.current_dir = p
            self._populate()

    def _accept(self):
        if self.current_dir.resolve() == self.src.parent.resolve():
            QMessageBox.information(self, 'Перемещение',
                                    'Файл уже в этой папке')
            return
        self.result = self.current_dir
        self.accept()


# ====================== точка входа ======================

def run_gui(initial_dir: Path, config: dict):
    config = dict(config)
    if initial_dir and initial_dir.is_dir():
        config['root_dir_resolved'] = initial_dir

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setApplicationName('ok-helper')
    # Кнопки модальных окон: QSS-овый min-width обрезал длинные
    # подписи, фильтр подгоняет ширину при показе окна.
    install_message_box_sizer(app)
    # Своей иконки в проекте нет: берём системную, иначе в заголовке
    # окна и на панели задач остаётся значок Python.
    app.setWindowIcon(_std_icon(QStyle.SP_FileDialogDetailedView))

    window = MainWindow(config, initial_dir)
    window.show()

    if config.get('first_run', True):
        config['first_run'] = False
        try:
            save_config(config)
        except Exception:
            pass

        def on_first_run():
            dlg = SettingsDialog(window, config)
            if dlg.exec() == QDialog.Accepted:
                window._on_settings_saved()
            if config.get('help_visible_at_start', True):
                HelpDialog(window, config).exec()
                try:
                    save_config(config)
                except Exception:
                    pass

        QTimer.singleShot(150, on_first_run)
    elif config.get('help_visible_at_start', True):
        QTimer.singleShot(150,
                          lambda: HelpDialog(window, config).exec())

    sys.exit(app.exec())