"""Разбор файлов точек OziExplorer (``.wpt``).

Формат описан в справке OziExplorer («OziExplorer File Formats»):

* первые 4 строки — заголовок: тип файла, датум, «Reserved», набор
  символов GPS;
* дальше по строке на точку, поля через запятую (всего 24 поля);
* поле 5 — дата в виде числа Delphi TDateTime (дни с 30.12.1899);
* поле 15 — высота **в футах** (``-777``, если неизвестна), а API
  OziExplorer ждёт высоту в метрах;
* запятая внутри текста записывается символом 209 (0xD1);
* кодировка — ANSI (на русской Windows cp1251), но встречается и UTF-8.

Разбор нужен, чтобы **добавить** файл точек к уже загруженным: API умеет
грузить файл точек только целиком (``oziLoadWPfile`` заменяет набор), а
по одной точке — через ``oziCreateWPex``.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

# Символ, которым в тексте заменена запятая (chr(209)).
COMMA_SUBSTITUTE = chr(209)

# Высота неизвестна — одно и то же число и в .wpt, и в API.
ALTITUDE_UNKNOWN = -777.0

# Метров в футе (в .wpt высота в футах, в API — в метрах).
METERS_IN_FOOT = 0.3048

# Значение «взять умолчание OziExplorer» из документации API.
DEFAULT = -1

# Первая строка заголовка файла точек и его длина в строках.
_HEADER_START = 'oziexplorer'
_HEADER_LINES = 4


@dataclass
class Waypoint:
    """Точка из ``.wpt`` в терминах ``oziCreateWPex``.

    Числовые поля, которых в файле нет, остаются равными ``-1`` — это
    документированное «использовать умолчание OziExplorer».
    """

    name: str = ''
    lat: float = 0.0
    lon: float = 0.0
    date: float = float(DEFAULT)          # TDateTime; -1 — сегодня
    symbol: int = DEFAULT
    map_display_format: int = DEFAULT
    fore_color: int = DEFAULT
    back_color: int = DEFAULT
    description: str = ''
    pointer_direction: int = DEFAULT
    garmin_display_format: int = DEFAULT
    proximity_distance: int = DEFAULT
    altitude_m: float = ALTITUDE_UNKNOWN
    font_size: int = DEFAULT
    font_style: int = DEFAULT
    symbol_size: int = DEFAULT
    proximity_symbol_pos: int = DEFAULT
    proximity_time: float = float(DEFAULT)
    proximity_route: int = DEFAULT

    def as_api(self) -> dict:
        """Поля в том виде, в каком их ждёт мост (JSON-объект)."""
        return {
            'name': self.name,
            'symbol': int(self.symbol),
            'lat': float(self.lat),
            'lon': float(self.lon),
            'altitude': float(self.altitude_m),
            'date': float(self.date),
            'mapDisplayFormat': int(self.map_display_format),
            'pointerDirection': int(self.pointer_direction),
            'garminDisplayFormat': int(self.garmin_display_format),
            'foreColor': int(self.fore_color),
            'backColor': int(self.back_color),
            'proximityDistance': int(self.proximity_distance),
            'description': self.description,
            'fontSize': int(self.font_size),
            'fontStyle': int(self.font_style),
            'symbolSize': int(self.symbol_size),
            'proximitySymbolPos': int(self.proximity_symbol_pos),
            'proximityTime': float(self.proximity_time),
            'proximityRoute': int(self.proximity_route),
        }


def _text(value: str) -> str:
    """Текст поля: символ 209 — это записанная в файле запятая.

    В справке OziExplorer: «запятые в текстовых полях недопустимы, вместо
    них пишется символ 209, и запятая будет подставлена». Символ 209 —
    это байт 0xD1, а в cp1251 этот же байт — буква «С», и отличить
    служебный байт от буквы в русском тексте невозможно («Синарский
    район» и запятая после «район» — один и тот же байт). Поэтому байты
    не трогаем: OziExplorer получает от нас текст в той же кодировке и
    видит ровно то же, что увидел бы, читая файл сам. Замена ниже
    срабатывает только для западных кодировок, где 209 — буква «Ñ» и в
    обычном тексте не встречается.
    """
    return value.replace(COMMA_SUBSTITUTE, ',')


def _field(fields: List[str], index: int) -> str:
    return fields[index].strip() if index < len(fields) else ''


def _number(fields: List[str], index: int, default, cast=int):
    """Число из поля; пусто или мусор — умолчание."""
    raw = _field(fields, index)
    if not raw:
        return default
    try:
        return cast(float(raw))
    except ValueError:
        return default


def parse_line(line: str) -> Optional[Waypoint]:
    """Точка из одной строки файла; None — это не данные."""
    s = line.strip()
    if not s:
        return None
    fields = s.split(',')
    if len(fields) < 4:
        return None
    try:
        lat = float(_field(fields, 2))
        lon = float(_field(fields, 3))
    except ValueError:
        return None

    altitude_ft = _number(fields, 14, ALTITUDE_UNKNOWN, cast=float)
    altitude_m = (ALTITUDE_UNKNOWN if altitude_ft == ALTITUDE_UNKNOWN
                  else altitude_ft * METERS_IN_FOOT)
    return Waypoint(
        name=_text(_field(fields, 1)),
        lat=lat,
        lon=lon,
        date=_number(fields, 4, float(DEFAULT), cast=float),
        symbol=_number(fields, 5, DEFAULT),
        map_display_format=_number(fields, 7, DEFAULT),
        fore_color=_number(fields, 8, DEFAULT),
        back_color=_number(fields, 9, DEFAULT),
        description=_text(_field(fields, 10)),
        pointer_direction=_number(fields, 11, DEFAULT),
        garmin_display_format=_number(fields, 12, DEFAULT),
        proximity_distance=_number(fields, 13, DEFAULT),
        altitude_m=altitude_m,
        font_size=_number(fields, 15, DEFAULT),
        font_style=_number(fields, 16, DEFAULT),
        symbol_size=_number(fields, 17, DEFAULT),
        proximity_symbol_pos=_number(fields, 18, DEFAULT),
        proximity_time=_number(fields, 19, float(DEFAULT), cast=float),
        proximity_route=_number(fields, 20, DEFAULT),
    )


def parse_wpt(text: str) -> List[Waypoint]:
    """Все точки файла; заголовок (если он есть) пропускается."""
    lines = text.splitlines()
    start = _HEADER_LINES if (lines
                              and lines[0].lstrip().lower()
                              .startswith(_HEADER_START)) else 0
    points = []
    for line in lines[start:]:
        point = parse_line(line)
        if point is not None:
            points.append(point)
    return points


def decode_wpt(raw: bytes) -> str:
    """Текст ``.wpt``: OziExplorer пишет ANSI, но встречается и UTF-8."""
    for encoding in ('utf-8-sig', 'cp1251'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode('cp1251', errors='replace')  # pragma: no cover


def read_wpt(path) -> List[Waypoint]:
    """Точки из файла ``.wpt`` (пустой список, если данных нет)."""
    return parse_wpt(decode_wpt(Path(path).read_bytes()))
