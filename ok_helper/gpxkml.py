"""Разбор ``.gpx`` и ``.kml`` для загрузки в уже открытое окно OziExplorer.

OziAPI (1.09) не умеет импортировать эти форматы: файловых загрузчиков у
него пять — ``.plt``, ``.wpt``, ``.pnt``, ``.rte``, ``.map``, и попытка
скормить ``oziLoadTrackFile`` файл ``.gpx`` вызывает в OziExplorer
модальное «Invalid numeric input.» (проверено вживую). Свои импорты
GPX/KML в OziExplorer есть, но это пункты меню, которых API не касается.

Поэтому файл разбираем сами и **пишем обычные файлы OziExplorer**:
трек — во временный ``.plt`` и грузим его ``oziLoadTrackFile`` в выбранный
слот (как обычный ``.plt``), точки — во временный ``.wpt`` и грузим
``oziLoadWPfile`` либо (когда выбрано добавление) создаём по одной через
``oziCreateWPex``. Поточечные вызовы для целого файла не годятся: в
реальных треках по 7-8 тысяч точек, а каждый вызов — это отдельный
диалог моста с OziExplorer.

Что переносим и в каких единицах (сверено по реальным парам
«.gpx → .plt» из папок треков):

* координаты — как в файлах OziExplorer: сначала широта, потом долгота
  (в GPX они в атрибутах ``lat``/``lon``, в KML — ``долгота,широта``);
* высота — в ``<ele>`` и в координатах KML в метрах; в файлах
  OziExplorer высота всегда в футах, поэтому при записи переводим;
* время — Delphi TDateTime (дни с 30.12.1899), берётся из ``<time>``
  как есть, без пересчёта часового пояса: в паре из треков время
  ``05:53:54Z`` попало в ``.plt`` как ``5:53:54``;
* сегменты трека (``<trkseg>``, отдельные ``<LineString>``) — разрыв
  линии, то есть ``code=1`` у первой точки сегмента.

Чего не переносим: символы точек (``<sym>`` — это имена из набора
Garmin, а API и файлы OziExplorer ждут номер символа), скорости и прочие
расширения, точки маршрутов (``<rtept>`` — маршрут API не создаёт, но
чтобы не терять данные, грузим их как обычные точки).
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from ok_helper.wpt import (ALTITUDE_UNKNOWN, DEFAULT, METERS_IN_FOOT,
                           Waypoint)

# Форматы, которые разбирает этот модуль.
GPX_KML_SUFFIXES = ('.gpx', '.kml')

# Начало отсчёта TDateTime (дни) в Delphi и OziExplorer.
_TDATETIME_EPOCH = datetime(1899, 12, 30)

# Времени в файле нет — «взять умолчание» из документации API.
NO_DATE = -1.0


class GpxKmlError(Exception):
    """Файл не разобрать или данных в нём нет."""


@dataclass
class TrackPoint:
    """Точка трека в терминах ``oziCreateTrackPoint``."""

    lat: float
    lon: float
    altitude_m: float = ALTITUDE_UNKNOWN
    date: float = NO_DATE
    code: int = 0          # 1 — новая секция, 0 — линия от прошлой точки

    def as_api(self) -> dict:
        return {
            'lat': float(self.lat),
            'lon': float(self.lon),
            'altitude': float(self.altitude_m),
            'date': float(self.date),
            'code': int(self.code),
        }


@dataclass
class Track:
    """Трек: имя и секции (разрыв между секциями — это разрыв линии)."""

    name: str = ''
    segments: List[List[TrackPoint]] = field(default_factory=list)

    def has_points(self) -> bool:
        return any(seg for seg in self.segments)

    def count(self) -> int:
        return sum(len(seg) for seg in self.segments)

    def as_api(self) -> List[dict]:
        """Точки для моста, с метками начала секций."""
        result = []
        for segment in self.segments:
            for index, point in enumerate(segment):
                item = point.as_api()
                item['code'] = 1 if index == 0 else 0
                result.append(item)
        return result


@dataclass
class Content:
    """Что нашлось в файле."""

    tracks: List[Track] = field(default_factory=list)
    waypoints: List[Waypoint] = field(default_factory=list)

    @property
    def has_tracks(self) -> bool:
        return any(track.has_points() for track in self.tracks)

    @property
    def has_waypoints(self) -> bool:
        return bool(self.waypoints)


def _tag(elem) -> str:
    """Имя тега без пространства имён, в нижнем регистре."""
    return elem.tag.rsplit('}', 1)[-1].lower()


def _text(elem) -> str:
    return (elem.text or '').strip()


def _walk(elem) -> Iterator:
    """Все элементы поддерева, включая сам elem."""
    yield elem
    for child in elem:
        yield from _walk(child)


def _float(value: str, default: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def tdatetime(value: str, default: float = NO_DATE) -> float:
    """Время из ISO 8601 в TDateTime (дни с 30.12.1899).

    Часовой пояс отбрасывается: время берётся как записано. Так же
    поступает конвертер, чьи ``.plt`` лежат рядом с ``.gpx`` (там
    ``05:53:54Z`` стало ``5:53:54``).
    """
    text = str(value or '').strip()
    if not text:
        return default
    if text.endswith(('Z', 'z')):
        text = text[:-1]
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return default
    if moment.tzinfo is not None:
        moment = moment.replace(tzinfo=None)
    return (moment - _TDATETIME_EPOCH).total_seconds() / 86400.0


def _lat_lon(elem) -> Optional[Tuple[float, float]]:
    """Широта и долгота из атрибутов элемента (GPX)."""
    lat = str(elem.get('lat', '')).strip()
    lon = str(elem.get('lon', '')).strip()
    if not lat or not lon:
        return None
    try:
        return float(lat), float(lon)
    except ValueError:
        return None


def _child_value(elem, names) -> str:
    """Текст первого дочернего элемента с одним из имён."""
    for child in elem:
        if _tag(child) in names:
            return _text(child)
    return ''


def _waypoint(elem, name: str = '',
              description: str = '') -> Optional[Waypoint]:
    """Точка из элемента GPX ``<wpt>``/``<rtept>``."""
    coords = _lat_lon(elem)
    if coords is None:
        return None
    lat, lon = coords
    return Waypoint(
        name=name or _child_value(elem, ('name',)),
        lat=lat,
        lon=lon,
        date=tdatetime(_child_value(elem, ('time',))),
        description=(description
                     or _child_value(elem, ('desc', 'cmt'))
                     or _child_value(elem, ('name',))),
        altitude_m=_float(_child_value(elem, ('ele',)), ALTITUDE_UNKNOWN),
    )


def _track_point(elem) -> Optional[TrackPoint]:
    """Точка из элемента GPX ``<trkpt>``."""
    coords = _lat_lon(elem)
    if coords is None:
        return None
    lat, lon = coords
    return TrackPoint(
        lat=lat,
        lon=lon,
        altitude_m=_float(_child_value(elem, ('ele',)), ALTITUDE_UNKNOWN),
        date=tdatetime(_child_value(elem, ('time',))),
    )


def _gpx_track(elem) -> Track:
    """Трек из элемента GPX ``<trk>`` (сегменты — ``<trkseg>``)."""
    track = Track(name=_child_value(elem, ('name',)))
    for child in elem:
        if _tag(child) != 'trkseg':
            continue
        points = []
        for point in child:
            if _tag(point) != 'trkpt':
                continue
            track_point = _track_point(point)
            if track_point is not None:
                points.append(track_point)
        if points:
            track.segments.append(points)
    return track


def parse_gpx(root) -> Content:
    """Разбирает дерево GPX: ``<wpt>``, ``<trk>`` и точки маршрутов."""
    content = Content()
    for elem in _walk(root):
        tag = _tag(elem)
        if tag == 'wpt':
            point = _waypoint(elem)
            if point is not None:
                content.waypoints.append(point)
        elif tag == 'trk':
            track = _gpx_track(elem)
            if track.has_points():
                content.tracks.append(track)
        elif tag == 'rtept':
            # Маршрут API не создаёт (oziLoadRTEfile грузит только .rte),
            # поэтому точки маршрута кладём как обычные точки.
            point = _waypoint(elem)
            if point is not None:
                content.waypoints.append(point)
    return content


def coordinates(text: str) -> List[Tuple[float, float, float]]:
    """Точки из ``<coordinates>`` KML: «долгота,широта[,высота]»."""
    result = []
    for chunk in str(text or '').split():
        parts = chunk.split(',')
        if len(parts) < 2:
            continue
        try:
            lon = float(parts[0])
            lat = float(parts[1])
        except ValueError:
            continue
        altitude = (float(parts[2])
                    if len(parts) > 2 and parts[2].strip() else None)
        result.append((lat, lon, altitude))
    return result


def _kml_point(lat, lon, altitude, name: str, description: str,
               date: float = NO_DATE) -> Waypoint:
    return Waypoint(
        name=name,
        lat=lat,
        lon=lon,
        date=date,
        description=description,
        altitude_m=(ALTITUDE_UNKNOWN if altitude is None else altitude),
    )


def _kml_track_point(lat, lon, altitude, date: float = NO_DATE) -> TrackPoint:
    return TrackPoint(
        lat=lat,
        lon=lon,
        altitude_m=(ALTITUDE_UNKNOWN if altitude is None else altitude),
        date=date,
    )


def _kml_gx_track(elem) -> Track:
    """Трек из Google-овского ``<gx:Track>`` (``<gx:coord>`` + ``<when>``)."""
    coords: List[Tuple[float, float, float]] = []
    times: List[float] = []
    for child in elem:
        tag = _tag(child)
        if tag == 'coord':
            parts = _text(child).split()
            if len(parts) < 2:
                continue
            lat = _float(parts[1], ALTITUDE_UNKNOWN)
            lon = _float(parts[0], ALTITUDE_UNKNOWN)
            altitude = _float(parts[2], ALTITUDE_UNKNOWN) \
                if len(parts) > 2 else ALTITUDE_UNKNOWN
            coords.append((lat, lon, altitude))
        elif tag == 'when':
            times.append(tdatetime(_text(child)))
    points = []
    for index, (lat, lon, altitude) in enumerate(coords):
        date = times[index] if index < len(times) else NO_DATE
        points.append(_kml_track_point(lat, lon, altitude, date))
    track = Track()
    if points:
        track.segments.append(points)
    return track


def _deep_text(elem, names) -> str:
    """Текст первого элемента с одним из имён на любой глубине."""
    for child in _walk(elem):
        if child is not elem and _tag(child) in names:
            return _text(child)
    return ''


def _kml_placemark(elem) -> Tuple[List[Track], List[Waypoint]]:
    """Треки и точки одного ``<Placemark>``."""
    name = _child_value(elem, ('name',))
    description = _child_value(elem, ('description',))
    # Время точки лежит неглубоко, но у OziExplorer — внутри
    # <TimeInstant><timePosition>, поэтому ищем по всему поддереву.
    date = tdatetime(_deep_text(elem, ('when', 'time')))
    segments: List[List[TrackPoint]] = []
    points: List[Waypoint] = []

    def walk(node, parent_tag: str = ''):
        for child in node:
            tag = _tag(child)
            if tag == 'coordinates':
                coords = coordinates(_text(child))
                if parent_tag in ('linestring', 'linearring'):
                    if coords:
                        segments.append([_kml_track_point(lat, lon, altitude)
                                         for lat, lon, altitude in coords])
                else:
                    # <Point> (и всё остальное с координатами) — точки.
                    for lat, lon, altitude in coords:
                        points.append(_kml_point(lat, lon, altitude, name,
                                                 description, date))
            elif tag == 'track':
                track = _kml_gx_track(child)
                if track.has_points():
                    segments.extend(track.segments)
            elif tag == 'placemark':
                continue        # вложенный Placemark разберём отдельно
            else:
                walk(child, tag)

    walk(elem)
    track = Track(name=name, segments=segments)
    tracks = [track] if track.has_points() else []
    return tracks, points


def parse_kml(root) -> Content:
    """Разбирает дерево KML: ``<Placemark>`` с точками и линиями."""
    content = Content()
    for elem in _walk(root):
        if _tag(elem) != 'placemark':
            continue
        tracks, points = _kml_placemark(elem)
        content.tracks.extend(tracks)
        content.waypoints.extend(points)
    return content


def _parse_xml(raw: bytes):
    """XML из байтов; при неудаче — как текст в cp1251 (без объявления)."""
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        text = raw.decode('cp1251', errors='replace')
        text = re.sub(r'^\s*<\?xml[^>]*\?>', '', text, count=1)
        return ET.fromstring(text)


# Имя ближайшего <name> и геометрия для мягкого разбора KML.
_NAME_RE = re.compile(r'<name>(.*?)</name>', re.S | re.I)
_GEOMETRY_RE = re.compile(
    r'<(Point|LineString|LinearRing)\b[^>]*>(.*?)</\1>', re.S | re.I)
_COORDS_RE = re.compile(r'<coordinates>(.*?)</coordinates>', re.S | re.I)
_TAGS_RE = re.compile(r'<[^>]+>')


def _lenient_kml(text: str) -> Content:
    """Разбор KML «на глазок», когда строгий XML не проходит.

    OziExplorer пишет KML с незакрытыми тегами (в реальном файле
    «2026-08-25_Lipovskoe_500m.kml» открыт ``<Folder>`` и нет
    ``</Folder>``, из-за чего весь остальной файл — вложенность с
    ошибкой), а также добавляет свои теги вроде ``<end>``. Строгий
    разбор такие файлы отвергает целиком, поэтому координаты достаём
    регулярным выражением: имя берём из ближайшего ``<name>`` перед
    геометрией, точки — из ``<Point>``, линии — из ``<LineString>`` и
    ``<LinearRing>``.
    """
    content = Content()
    for match in _GEOMETRY_RE.finditer(text):
        kind = match.group(1).lower()
        inside = _COORDS_RE.search(match.group(2))
        if inside is None:
            continue
        coords = coordinates(_TAGS_RE.sub('', inside.group(1)))
        if not coords:
            continue
        names = _NAME_RE.findall(text[:match.start()])
        name = _TAGS_RE.sub('', names[-1]).strip() if names else ''
        if kind == 'point':
            for lat, lon, altitude in coords:
                content.waypoints.append(_kml_point(lat, lon, altitude,
                                                    name, ''))
        else:
            content.tracks.append(Track(
                name=name,
                segments=[[_kml_track_point(lat, lon, altitude)
                           for lat, lon, altitude in coords]]))
    return content


def read_gpx_kml(path) -> Content:
    """Разбирает ``.gpx`` или ``.kml``. Ошибка — :class:`GpxKmlError`."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in GPX_KML_SUFFIXES:
        raise GpxKmlError(f'формат {suffix or path.name} не поддержан')
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise GpxKmlError(f'не прочитать {path.name}: {e}')
    try:
        root = _parse_xml(raw)
    except (ET.ParseError, ValueError) as e:
        content = _lenient_content(raw, suffix)
        if content is not None:
            return content
        raise GpxKmlError(f'не разобрать {path.name}: {e}')
    content = parse_kml(root) if suffix == '.kml' else parse_gpx(root)
    if not content.has_tracks and not content.has_waypoints:
        raise GpxKmlError(f'в {path.name} нет ни треков, ни точек')
    return content


def _lenient_content(raw: bytes, suffix: str) -> Optional[Content]:
    """Мягкий разбор битого файла; None — если и он ничего не нашёл."""
    if suffix != '.kml':
        return None
    text = raw.decode('utf-8', errors='replace')
    content = _lenient_kml(text)
    if not content.has_tracks and not content.has_waypoints:
        return None
    return content


# ------- запись файлов OziExplorer (грузим их одним вызовом API) -------

# Заголовки — ровно такие, какие пишет сам OziExplorer.
_PLT_HEADER = ('OziExplorer Track Point File Version 2.1\r\n'
               'WGS 84\r\n'
               'Altitude is in Feet\r\n'
               'Reserved 3\r\n')
_WPT_HEADER = ('OziExplorer Waypoint File Version 1.1\r\n'
               'WGS 84\r\n'
               'Reserved 2\r\n'
               'garmin\r\n')

# Строка настроек трека (.plt, строка 5) из файлов OziExplorer:
# толщина, цвет, описание, пропуск, тип, заливка, цвет заливки, флаги.
# Значения взяты из того, что сохраняет сам OziExplorer для пустого трека.
_PLT_SETTINGS = '0,2,255,{name},0,0,2,8421376,-1,0'

# Ширина поля описания трека в строке настроек .plt (так пишет OziExplorer).
_PLT_NAME_WIDTH = 35

# Сокращения месяцев, как их пишет OziExplorer в русской Windows. Поля
# 6-7 в .plt OziExplorer при чтении игнорирует (берёт поле 5), но пишет
# сам, поэтому пишем похоже.
_MONTHS = ('янв', 'фев', 'мар', 'апр', 'май', 'июн',
           'июл', 'авг', 'сен', 'окт', 'ноя', 'дек')


def _file_text(value: str) -> str:
    """Текст для файла OziExplorer.

    Запятая в текстовом поле файла сломала бы разбор строки по запятым.
    Справка предлагает писать вместо неё символ 209, но в cp1251 этот
    байт — буква «С», и живой русский текст от служебного символа не
    отличить (об этом же написано в ``ok_helper/wpt.py``), поэтому
    заменяем запятую пробелом: поле остаётся читаемым.
    """
    return str(value or '').replace(',', ' ')


def _to_feet(altitude_m: float) -> float:
    """Метры в футы; «высоты нет» остаётся -777."""
    if altitude_m == ALTITUDE_UNKNOWN:
        return ALTITUDE_UNKNOWN
    return altitude_m / METERS_IN_FOOT


def _plt_datetime(date: float) -> Tuple[str, str]:
    """Поля 6-7 точки .plt: дата и время строкой («16-окт-25», «5:53:54»)."""
    moment = _TDATETIME_EPOCH + timedelta(days=date)
    text_date = (f'{moment.day:02d}-{_MONTHS[moment.month - 1]}-'
                 f'{moment.year % 100:02d}')
    text_time = f'{moment.hour}:{moment.minute:02d}:{moment.second:02d}'
    return text_date, text_time


def write_plt(track: Track, path, name: str = '') -> Path:
    """Пишет ``.plt`` с одним треком — для ``oziLoadTrackFile``.

    Формат описан в справке OziExplorer («OziExplorer File Formats»):
    поля точки — широта, долгота, признак начала секции, высота **в
    футах**, дата TDateTime, а также дата и время строкой (их OziExplorer
    при чтении игнорирует).
    """
    path = Path(path)
    points = track.as_api()
    name_field = f'{_file_text(name):<{_PLT_NAME_WIDTH}}'[:_PLT_NAME_WIDTH]
    lines = [_PLT_SETTINGS.format(name=name_field), str(len(points))]
    for item in points:
        date = float(item['date'])
        if date == NO_DATE:
            tail = ',,'
        else:
            text_date, text_time = _plt_datetime(date)
            tail = f'{date:.7f}, {text_date}, {text_time}'
        lines.append(
            f'{float(item["lat"]):11.6f},{float(item["lon"]):11.6f},'
            f'{int(item["code"])},{_to_feet(float(item["altitude"])):7.1f},'
            f'{tail}')
    path.write_bytes((_PLT_HEADER + '\r\n'.join(lines) + '\r\n')
                     .encode('cp1251', errors='replace'))
    return path


def _wpt_number(value, digits: int = 0) -> str:
    """Число поля .wpt; -1 — это «умолчание», такое поле оставляем пустым."""
    if int(value) == DEFAULT:
        return ''
    return f'{float(value):.{digits}f}' if digits else str(int(value))


def write_wpt(points, path) -> Path:
    """Пишет ``.wpt`` с точками — для ``oziLoadWPfile``.

    Поля, которых в GPX/KML нет, остаются пустыми: по справке пустое
    поле означает «взять умолчание OziExplorer».
    """
    path = Path(path)
    lines = []
    for index, point in enumerate(points, 1):
        fields = [
            str(index),
            _file_text(point.name),
            f'{point.lat:.6f}',
            f'{point.lon:.6f}',
            '' if point.date == DEFAULT else f'{point.date:.7f}',
            _wpt_number(point.symbol),
            '1',                            # статус: «точка включена»
            _wpt_number(point.map_display_format),
            _wpt_number(point.fore_color),
            _wpt_number(point.back_color),
            _file_text(point.description),
            _wpt_number(point.pointer_direction),
            _wpt_number(point.garmin_display_format),
            _wpt_number(point.proximity_distance),
            f'{_to_feet(float(point.altitude_m)):.1f}',
            _wpt_number(point.font_size),
            _wpt_number(point.font_style),
            _wpt_number(point.symbol_size),
            _wpt_number(point.proximity_symbol_pos),
            _wpt_number(point.proximity_time, digits=1),
            _wpt_number(point.proximity_route),
            '', '', '',                     # вложения и имена файлов
        ]
        lines.append(','.join(fields))
    text = _WPT_HEADER
    if lines:
        text += '\r\n'.join(lines) + '\r\n'
    path.write_bytes(text.encode('cp1251', errors='replace'))
    return path
