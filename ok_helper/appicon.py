"""Иконка приложения: тёмная папка с надписью «OK».

Рисуется кодом, а не хранится картинкой: так иконка одинакова в окне,
в панели задач и в собранном .exe (`--icon`) и не зависит от того,
положили ли PNG рядом с бинарником.
"""

from PySide6.QtCore import QBuffer, QByteArray, QRectF, Qt
from PySide6.QtGui import (
    QColor, QFont, QFontDatabase, QFontMetricsF, QIcon, QImage,
    QPainter, QPainterPath, QPixmap,
)

# Размеры, которые кладём в .ico (Windows выбирает под масштаб/DPI).
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)

_INK = '#000000'
_PAPER = '#ffffff'


def _preferred_font() -> QFont:
    """Максимально жирный шрифт из доступных."""
    families = set(QFontDatabase.families())
    for name in ('Arial Black', 'Arial', 'Segoe UI', 'DejaVu Sans'):
        if name in families:
            font = QFont(name)
            font.setWeight(QFont.Weight.Black)
            return font
    font = QFont()
    font.setWeight(QFont.Weight.Black)
    return font


def draw_icon_image(size: int) -> QImage:
    """Рисует иконку в квадрат size×size (прозрачный фон)."""
    size = max(8, int(size))
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)

    s = float(size)
    left = 0.07 * s
    right = 0.93 * s
    tab_top = 0.14 * s
    tab_right = 0.46 * s
    diag_x = 0.57 * s
    body_top = 0.32 * s
    bottom = 0.90 * s
    radius = max(2.0, 0.10 * s)

    # Тело папки со скруглёнными углами.
    body = QPainterPath()
    body.addRoundedRect(
        QRectF(left, body_top, right - left, bottom - body_top),
        radius, radius)

    # Язычок папки с диагональю к телу.
    tab = QPainterPath()
    tab.moveTo(left + radius, tab_top)
    tab.lineTo(tab_right, tab_top)
    tab.lineTo(diag_x, body_top)
    tab.lineTo(left, body_top)
    tab.lineTo(left, tab_top + radius)
    tab.quadTo(left, tab_top, left + radius, tab_top)
    tab.closeSubpath()

    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(_INK))
    painter.drawPath(body.united(tab))

    # Надпись «OK» по центру тела папки.
    font = _preferred_font()
    font.setPixelSize(max(6, int(0.66 * s)))
    metrics = QFontMetricsF(font)
    target_width = 0.60 * s
    advance = metrics.horizontalAdvance('OK') or 1.0
    font.setPixelSize(max(6, int(round(font.pixelSize()
                                       * target_width / advance))))

    painter.setFont(font)
    painter.setPen(QColor(_PAPER))
    painter.drawText(
        QRectF(left, body_top, right - left, bottom - body_top),
        Qt.AlignCenter, 'OK')
    painter.end()
    return image


def app_icon() -> QIcon:
    """QIcon со всеми размерами для окна и панели задач."""
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(QPixmap.fromImage(draw_icon_image(size)))
    return icon


def png_bytes(size: int) -> bytes:
    """PNG-представление иконки (для записи .ico / .png)."""
    buffer = QByteArray()
    device = QBuffer(buffer)
    device.open(QBuffer.WriteOnly)
    draw_icon_image(size).save(device, 'PNG')
    device.close()
    return bytes(buffer)
