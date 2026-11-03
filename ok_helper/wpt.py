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

from dataclasses import dataclass, field
from datetime import datetime, timedelta
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

# Сколько полей OziExplorer пишет в строке точки (Version 1.1).
# Порядок — как в справке OziExplorer, нумерация с нуля:
#   0 номер, 1 имя, 2 широта, 3 долгота, 4 дата, 5 символ,
#   6 статус, 7 формат показа, 8 цвет текста, 9 цвет фона,
#   10 описание, 11 направление указателя, 12 формат Garmin,
#   13 дистанция срабатывания, 14 высота (футы), 15 размер шрифта,
#   16 стиль шрифта, 17 размер символа, 18 позиция символа,
#   19 время срабатывания, 20 маршрут/срабатывание, 21 формат файла,
#   22 имя символа, 23 формат подписи, 24 служебное (обычно 300).
WPT_FIELD_COUNT = 25

# Заголовок, который пишется в новый файл точек.
DEFAULT_WPT_HEADER = [
    'OziExplorer Waypoint File Version 1.1',
    'WGS 84',
    'Reserved 2',
    'garmin',
]

# Начало отсчёта даты Delphi TDateTime (30.12.1899).
DELPHI_EPOCH = datetime(1899, 12, 30)

# Поля нового файла: те же значения, что пишет OziExplorer для обычной
# точки. Изменяются только координаты, имя, описание и дата.
_DEFAULT_FIELDS = [
    '-1', '', '0.000000', '0.000000', '0', '0', '1', '3', '0', '255',
    '', '0', '0', '0', str(int(ALTITUDE_UNKNOWN)), '6', '0', '17', '0',
    '10.0', '2', '', '', '', '300',
]


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


# ─────────────────────────────────────────────────────────────────
# Запись .wpt: строки файла сохраняются целиком
# ─────────────────────────────────────────────────────────────────

def detect_wpt_encoding(raw: bytes) -> str:
    """Кодировка файла: UTF-8 (с BOM или без) либо ANSI/cp1251.

    ``utf-8-sig`` без BOM не годится: при обратной записи он добавил бы
    BOM в файл, который его не имел.
    """
    if raw.startswith(b'\xef\xbb\xbf'):
        return 'utf-8-sig'
    try:
        raw.decode('utf-8')
        return 'utf-8'
    except UnicodeDecodeError:
        return 'cp1251'


def delphi_to_datetime(value) -> Optional[datetime]:
    """Дата из TDateTime; None — значение «дата неизвестна» (-1)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    # Округляем до секунды: double теряет доли секунды, и 12:30 после
    # обратного пересчёта иначе показывалось бы как 12:29:59.999…
    return DELPHI_EPOCH + timedelta(seconds=round(number * 86400))


def datetime_to_delphi(value: datetime) -> float:
    """TDateTime из даты/времени Python (дни с 30.12.1899)."""
    return (value - DELPHI_EPOCH).total_seconds() / 86400.0


def format_number(value: float, decimals: int = 6) -> str:
    """Число без хвостовых нулей — так его пишет OziExplorer."""
    text = f'{float(value):.{decimals}f}'.rstrip('0').rstrip('.')
    return text or '0'


# Кириллическая «с» неотличима на глаз от латинской «c», но в cp1251
# заглавная «С» — это байт 0xD1, которым OziExplorer записывает запятую
# внутри текста и показывает её как разделитель. Латинские буквы такой
# двусмысленности не создают, поэтому в именах и описаниях они и
# остаются.
CYRILLIC_S = {'\u0441': 'c', '\u0421': 'C'}


def normalize_text(value: str) -> str:
    """Кириллические «с»/«С» → латинские «c»/«C»."""
    return str(value).translate(str.maketrans(CYRILLIC_S))


def escape_text(value: str) -> str:
    """Текст для поля .wpt: запятая разделила бы поля.

    В справке OziExplorer для запятой зарезервирован символ 209, но в
    cp1251 этот же байт — буква «С», поэтому вместо неоднозначной
    подстановки запятая заменяется точкой с запятой. Кириллическая «с»
    при этом заменяется латинской — см. ``normalize_text``.
    """
    return normalize_text(value).replace(',', ';')


@dataclass
class WptRecord:
    """Строка точки файла ``.wpt`` со всеми 25 полями.

    ``raw`` — исходная строка файла. Пока запись не меняли, при записи
    используется она: так сохраняются форматирование и кодировка чисел,
    которые выставил сам OziExplorer.
    """

    fields: List[str] = field(default_factory=list)
    raw: Optional[str] = None

    # ---------- разбор и сериализация ----------

    @classmethod
    def from_line(cls, line: str) -> 'WptRecord':
        text = line.rstrip('\r\n')
        return cls(fields=[part.strip() for part in text.split(',')],
                   raw=text)

    @classmethod
    def new(cls, template: Optional['WptRecord'] = None,
            name: str = '', lat: float = 0.0, lon: float = 0.0,
            date: Optional[datetime] = None) -> 'WptRecord':
        """Новая точка в стиле существующей (шаблон — последняя запись)."""
        fields = (list(template.fields) if template is not None
                  else list(_DEFAULT_FIELDS))
        fields += [''] * (WPT_FIELD_COUNT - len(fields))
        record = cls(fields=fields)
        record.number = '-1'
        record.name = name
        record.lat = lat
        record.lon = lon
        record.description = ''
        record.altitude_ft = ALTITUDE_UNKNOWN
        record.date = datetime_to_delphi(date or datetime.now())
        # Текст подписи всегда чёрный — цвет точки задаётся фоном.
        record.fore_color = 0
        return record

    def to_line(self) -> str:
        fields = list(self.fields)
        if len(fields) < WPT_FIELD_COUNT:
            fields += [''] * (WPT_FIELD_COUNT - len(fields))
        return ','.join(fields)

    def normalize_text_fields(self) -> bool:
        """Кириллические «с»/«С» в имени и описании → латинские.

        Возвращает True, если что-то изменилось: тогда исходная строка
        файла (``raw``) больше не используется и запись пересобирается
        из полей. Значения остальных полей при этом не меняются.
        """
        changed = False
        for index in (1, 10):
            value = self._get(index)
            fixed = normalize_text(value)
            if fixed != value:
                self._set(index, fixed)
                changed = True
        return changed

    # ---------- поля ----------

    def _get(self, index: int) -> str:
        return self.fields[index].strip() if index < len(self.fields) else ''

    def _set(self, index: int, value: str):
        while len(self.fields) <= index:
            self.fields.append('')
        self.fields[index] = str(value)
        # Строка изменилась — исходный текст больше не актуален.
        self.raw = None

    def _get_float(self, index: int, default: float = 0.0) -> float:
        try:
            return float(self._get(index))
        except ValueError:
            return default

    @property
    def number(self) -> str:
        return self._get(0)

    @number.setter
    def number(self, value: str):
        self._set(0, value)

    @property
    def name(self) -> str:
        return _text(self._get(1))

    @name.setter
    def name(self, value: str):
        self._set(1, escape_text(str(value).strip()))

    @property
    def lat(self) -> float:
        return self._get_float(2)

    @lat.setter
    def lat(self, value: float):
        self._set(2, f'{float(value):.6f}')

    @property
    def lon(self) -> float:
        return self._get_float(3)

    @lon.setter
    def lon(self, value: float):
        self._set(3, f'{float(value):.6f}')

    @property
    def date(self) -> float:
        return self._get_float(4, float(DEFAULT))

    @date.setter
    def date(self, value: float):
        self._set(4, format_number(value, 7))

    @property
    def symbol(self) -> int:
        try:
            return int(float(self._get(5)))
        except ValueError:
            return DEFAULT

    @symbol.setter
    def symbol(self, value: int):
        self._set(5, str(int(value)))

    def _get_color(self, index: int) -> int:
        """Цвет поля: COLORREF (0x00BBGGRR); DEFAULT — «как в OziExplorer»."""
        try:
            return int(float(self._get(index)))
        except ValueError:
            return DEFAULT

    def _set_color(self, index: int, value: int):
        # Пустое поле — то же самое, что DEFAULT: так пишет и gpxkml.
        number = int(value)
        self._set(index, '' if number == DEFAULT else str(number))

    @property
    def fore_color(self) -> int:
        """Цвет текста точки (Windows COLORREF: BGR)."""
        return self._get_color(8)

    @fore_color.setter
    def fore_color(self, value: int):
        self._set_color(8, value)

    @property
    def back_color(self) -> int:
        """Цвет фона подписи точки (Windows COLORREF: BGR)."""
        return self._get_color(9)

    @back_color.setter
    def back_color(self, value: int):
        self._set_color(9, value)

    @property
    def color(self) -> int:
        """Основной цвет точки — цвет фона подписи (поле 9, COLORREF).

        Цвет текста при этом всегда чёрный: он не редактируется и не
        зависит от цвета точки.
        """
        return self.back_color

    @color.setter
    def color(self, value: int):
        self.back_color = value

    @property
    def description(self) -> str:
        return _text(self._get(10))

    @description.setter
    def description(self, value: str):
        self._set(10, escape_text(str(value).strip()))

    @property
    def altitude_ft(self) -> float:
        return self._get_float(14, ALTITUDE_UNKNOWN)

    @altitude_ft.setter
    def altitude_ft(self, value: float):
        self._set(14, format_number(value))

    @property
    def altitude_m(self) -> float:
        feet = self.altitude_ft
        return (ALTITUDE_UNKNOWN if feet == ALTITUDE_UNKNOWN
                else feet * METERS_IN_FOOT)

    @altitude_m.setter
    def altitude_m(self, value: float):
        self.altitude_ft = (ALTITUDE_UNKNOWN if value == ALTITUDE_UNKNOWN
                            else float(value) / METERS_IN_FOOT)


@dataclass
class WptFile:
    """Файл точек целиком: заголовок, записи и параметры записи."""

    header: List[str] = field(default_factory=list)
    records: List[WptRecord] = field(default_factory=list)
    trailing: List[str] = field(default_factory=list)
    encoding: str = 'cp1251'
    newline: str = '\r\n'
    trailing_newline: bool = True

    def new_record(self, **kwargs) -> WptRecord:
        """Новая точка в стиле последней записи файла."""
        template = self.records[-1] if self.records else None
        return WptRecord.new(template, **kwargs)

    def to_text(self) -> str:
        lines = list(self.header)
        for record in self.records:
            lines.append(record.raw if record.raw is not None
                         else record.to_line())
        lines.extend(self.trailing)
        text = self.newline.join(lines)
        if self.trailing_newline and lines:
            text += self.newline
        return text

    def encode(self) -> bytes:
        text = self.to_text()
        try:
            return text.encode(self.encoding)
        except UnicodeEncodeError:
            # ANSI не вмещает некоторые символы (эмодзи и т. п.).
            # UTF-8 OziExplorer тоже читает — не теряем данные.
            self.encoding = 'utf-8'
            return text.encode('utf-8')

    def save(self, path) -> None:
        Path(path).write_bytes(self.encode())


def read_wpt_file(path) -> WptFile:
    """Читает ``.wpt`` с сохранением заголовка, кодировки и переводов строк.

    Строки, которые не удалось разобрать как точку, сохраняются в
    ``trailing`` и пишутся обратно — редактор не должен терять данные.

    Текст читается как есть: замена кириллической «с» на латинскую «c»
    выполняется в редакторе — при вводе и по кнопке «Заменить «с» на
    «c»», — чтобы нельзя было изменить файл, просто открыв его.
    """
    raw = Path(path).read_bytes()
    encoding = detect_wpt_encoding(raw)
    text = raw.decode(encoding, errors='replace')
    newline = '\r\n' if '\r\n' in text else '\n'
    trailing_newline = text.endswith(('\n', '\r'))
    lines = text.splitlines()

    header: List[str] = []
    if (lines and lines[0].lstrip().lower()
            .startswith(_HEADER_START)):
        header = lines[:_HEADER_LINES]
        lines = lines[_HEADER_LINES:]
    else:
        header = list(DEFAULT_WPT_HEADER)

    records: List[WptRecord] = []
    trailing: List[str] = []
    for line in lines:
        if not line.strip():
            continue
        if parse_line(line) is not None:
            records.append(WptRecord.from_line(line))
        else:
            trailing.append(line)

    return WptFile(header=header, records=records, trailing=trailing,
                   encoding=encoding, newline=newline,
                   trailing_newline=trailing_newline)
