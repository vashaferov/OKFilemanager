"""Тесты разбора .gpx/.kml и записи файлов OziExplorer (ok_helper.gpxkml).

Функциональность: разбор GPX (треки, сегменты, точки, точки маршрутов) и
KML (Placemark с линией, точкой, gx:Track; битый KML — мягкий разбор),
преобразование времени в TDateTime и метры в футы, запись обычных
файлов OziExplorer .plt/.wpt для загрузки через OziAPI.

Реальные файлы треков не читаются: образцы собираются в тесте. Формат
файлов OziExplorer сверен вручную с настоящими файлами из папок треков.

Группировка в Allure:
  epic    = ok-filemanager
  feature = Загрузка .gpx/.kml в OziExplorer
  story   = задаётся маркером @pytest.mark.story на каждом классе.
"""

from pathlib import Path

import allure
import pytest

from ok_helper.gpxkml import (
    ALTITUDE_UNKNOWN,
    GPX_KML_SUFFIXES,
    NO_DATE,
    GpxKmlError,
    Track,
    TrackPoint,
    coordinates,
    read_gpx_kml,
    tdatetime,
    write_plt,
    write_wpt,
)
from ok_helper.wpt import Waypoint, read_wpt


pytestmark = [
    pytest.mark.gpxkml,
    pytest.mark.epic('ok-filemanager'),
    pytest.mark.feature('Загрузка .gpx/.kml в OziExplorer'),
]


def make(path: Path, text: str, encoding: str = 'utf-8') -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode(encoding))
    return path


GPX_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test">
  <wpt lat="57.483576" lon="60.990078">
    <ele>200.5</ele>
    <time>2026-08-25T20:22:23Z</time>
    <name>A1</name>
    <desc>точка с запятой, и запятая</desc>
  </wpt>
  <rte>
    <rtept lat="57.5" lon="60.9"><name>R1</name></rtept>
  </rte>
  <trk>
    <name>Октябрьский</name>
    <trkseg>
      <trkpt lat="60.581368" lon="60.154179">
        <ele>188.09</ele><time>2025-10-16T05:53:54.200Z</time>
      </trkpt>
      <trkpt lat="60.581400" lon="60.154200">
        <ele>189.0</ele><time>2025-10-16T05:53:55Z</time>
      </trkpt>
    </trkseg>
    <trkseg>
      <trkpt lat="60.590000" lon="60.160000"/>
    </trkseg>
  </trk>
</gpx>
"""

KML_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns:gx="http://www.google.com/kml/ext/2.2"><Document>
  <Placemark>
    <name>Зеленый пер</name>
    <LineString>
      <coordinates>60.576345,56.901661,274.0 60.576400,56.901700,275.5</coordinates>
    </LineString>
  </Placemark>
  <Placemark>
    <name>A1</name>
    <description>подпись</description>
    <Point><coordinates>60.990078,57.483576</coordinates></Point>
  </Placemark>
  <Placemark>
    <name>GT</name>
    <gx:Track>
      <when>2026-08-25T20:00:00Z</when>
      <gx:coord>60.1 57.1 100</gx:coord>
      <when>2026-08-25T20:00:10Z</when>
      <gx:coord>60.2 57.2 101</gx:coord>
    </gx:Track>
  </Placemark>
</Document></kml>
"""

# Так OziExplorer и пишет: <Folder> без закрывающего тега и свои <end>.
BROKEN_KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml><Document><Folder><name>Data</name>
  <Placemark>
   <name>A1</name>
   <description></description>
    <Point><coordinates>60.990078,57.483576</coordinates></Point>
    <end>
    <TimeInstant><timePosition><time>2026-08-25T20:22:23Z</time>
    </timePosition></TimeInstant>
   </end>
  </Placemark>
  <Placemark>
   <name>B1</name>
    <Point><coordinates>60.998412,57.483709</coordinates></Point>
  </Placemark>
  <Placemark>
    <name>Grid_Line</name>
    <LineString><coordinates>60.99,57.48 61.00,57.49</coordinates></LineString>
  </Placemark>
</Document></kml>
"""


# ─────────────────────────────────────────────────────────────────
# 1. Время и координаты
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Время и координаты')
class TestValues:
    @pytest.mark.smoke
    def test_time_is_taken_as_written(self):
        """Часовой пояс отбрасывается: 05:53:54 — это 5:53:54."""
        assert tdatetime('2025-10-16T05:53:54Z') == pytest.approx(
            45946.24576388889, abs=1e-6)

    def test_time_keeps_fractional_seconds(self):
        """В реальных треках время бывает с десятыми: 05:53:54.2Z."""
        assert tdatetime('2025-10-16T05:53:54.200Z') == pytest.approx(
            45946.2457662, abs=1e-6)

    def test_time_with_offset_drops_zone(self):
        assert tdatetime('2025-10-16T05:53:54+05:00') == pytest.approx(
            tdatetime('2025-10-16T05:53:54Z'), abs=1e-9)

    def test_time_with_fraction(self):
        assert tdatetime('2025-10-16T05:53:54.500Z') == pytest.approx(
            45946.24576388889 + 0.5 / 86400, abs=1e-9)

    def test_empty_time_is_no_date(self):
        assert tdatetime('') == NO_DATE
        assert tdatetime(None) == NO_DATE

    def test_garbage_time_is_no_date(self):
        assert tdatetime('не дата') == NO_DATE

    def test_coordinates_are_lon_lat(self):
        """В KML «долгота,широта[,высота]», а нужны широта и долгота."""
        assert coordinates('60.990078,57.483576,300') == [
            (57.483576, 60.990078, 300.0)]

    def test_coordinates_without_altitude(self):
        assert coordinates('60.99,57.48') == [(57.48, 60.99, None)]

    def test_coordinates_skip_garbage(self):
        assert coordinates('мусор 60.99,57.48') == [(57.48, 60.99, None)]


# ─────────────────────────────────────────────────────────────────
# 2. Разбор GPX
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Разбор GPX')
class TestParseGpx:
    @pytest.fixture
    def content(self, tmp_path):
        return read_gpx_kml(make(tmp_path / 'track.gpx', GPX_SAMPLE))

    @pytest.mark.smoke
    def test_track_with_segments(self, content):
        assert len(content.tracks) == 1
        track = content.tracks[0]
        assert track.name == 'Октябрьский'
        assert track.count() == 3
        assert [len(seg) for seg in track.segments] == [2, 1]

    def test_first_point_values(self, content):
        point = content.tracks[0].segments[0][0]
        assert point.lat == pytest.approx(60.581368)
        assert point.lon == pytest.approx(60.154179)
        assert point.altitude_m == pytest.approx(188.09)
        assert point.date == pytest.approx(45946.2457662, abs=1e-6)

    def test_waypoints(self, content):
        assert len(content.waypoints) == 2
        first = content.waypoints[0]
        assert first.name == 'A1'
        assert first.lat == pytest.approx(57.483576)
        assert first.lon == pytest.approx(60.990078)
        assert first.altitude_m == pytest.approx(200.5)
        assert first.date == pytest.approx(46259.8488773, abs=1e-6)

    def test_description_falls_back_to_name(self, content):
        assert content.waypoints[1].name == 'R1'
        assert content.waypoints[1].description == 'R1'

    def test_point_without_ele_and_time(self, content):
        point = content.tracks[0].segments[1][0]
        assert point.altitude_m == ALTITUDE_UNKNOWN
        assert point.date == NO_DATE

    def test_segment_breaks_are_marked(self, content):
        codes = [point['code'] for point in content.tracks[0].as_api()]
        assert codes == [1, 0, 1]

    def test_has_flags(self, content):
        assert content.has_tracks
        assert content.has_waypoints


# ─────────────────────────────────────────────────────────────────
# 3. Разбор KML
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Разбор KML')
class TestParseKml:
    @pytest.fixture
    def content(self, tmp_path):
        return read_gpx_kml(make(tmp_path / 'track.kml', KML_SAMPLE))

    @pytest.mark.smoke
    def test_line_becomes_track(self, content):
        assert [track.name for track in content.tracks] == [
            'Зеленый пер', 'GT']
        track = content.tracks[0]
        assert track.name == 'Зеленый пер'
        assert track.count() == 2
        first = track.segments[0][0]
        assert first.lat == pytest.approx(56.901661)
        assert first.lon == pytest.approx(60.576345)
        assert first.altitude_m == pytest.approx(274.0)

    def test_point_becomes_waypoint(self, content):
        assert len(content.waypoints) == 1
        point = content.waypoints[0]
        assert point.name == 'A1'
        assert point.description == 'подпись'
        assert point.lat == pytest.approx(57.483576)
        assert point.lon == pytest.approx(60.990078)
        assert point.altitude_m == ALTITUDE_UNKNOWN

    def test_gx_track_with_times(self, content):
        track = content.tracks[1]
        assert track.name == 'GT'
        assert track.count() == 2
        first = track.segments[0][0]
        assert (first.lat, first.lon) == (57.1, 60.1)
        assert first.altitude_m == pytest.approx(100.0)
        assert first.date == pytest.approx(46259.8333333, abs=1e-6)


# ─────────────────────────────────────────────────────────────────
# 4. Битый KML (так пишет сам OziExplorer)
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Битый KML')
class TestBrokenKml:
    @pytest.mark.smoke
    def test_strict_parse_fails(self, tmp_path):
        """Строгий XML такие файлы не принимает — нужен мягкий разбор."""
        import xml.etree.ElementTree as ET
        src = make(tmp_path / 'broken.kml', BROKEN_KML)
        with pytest.raises(ET.ParseError):
            ET.fromstring(src.read_bytes())

    def test_fallback_finds_points_and_line(self, tmp_path):
        content = read_gpx_kml(make(tmp_path / 'broken.kml', BROKEN_KML))

        assert [p.name for p in content.waypoints] == ['A1', 'B1']
        assert content.waypoints[0].lat == pytest.approx(57.483576)
        assert content.waypoints[0].lon == pytest.approx(60.990078)
        assert len(content.tracks) == 1
        assert content.tracks[0].name == 'Grid_Line'
        assert content.tracks[0].count() == 2

    def test_fallback_ignores_garbage(self, tmp_path):
        src = make(tmp_path / 'broken.kml',
                   '<kml><Document><Folder><координаты>')
        with pytest.raises(GpxKmlError):
            read_gpx_kml(src)


# ─────────────────────────────────────────────────────────────────
# 5. Ошибки разбора
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Ошибки разбора')
class TestParseErrors:
    def test_unknown_suffix(self, tmp_path):
        with pytest.raises(GpxKmlError):
            read_gpx_kml(make(tmp_path / 'track.txt', '<gpx/>'))

    def test_missing_file(self, tmp_path):
        with pytest.raises(GpxKmlError):
            read_gpx_kml(tmp_path / 'нет.gpx')

    def test_gpx_without_data(self, tmp_path):
        with pytest.raises(GpxKmlError):
            read_gpx_kml(make(tmp_path / 'пусто.gpx', '<gpx></gpx>'))

    def test_kml_in_cp1251_with_declaration(self, tmp_path):
        """В объявлении utf-8, а файл в cp1251: пробуем прочитать и так."""
        text = KML_SAMPLE.replace('Зеленый пер', 'Зеленый пер')
        src = make(tmp_path / 'ansi.kml', text, encoding='cp1251')

        content = read_gpx_kml(src)

        assert content.tracks[0].name == 'Зеленый пер'

    def test_suffixes_constant(self):
        assert GPX_KML_SUFFIXES == ('.gpx', '.kml')


# ─────────────────────────────────────────────────────────────────
# 6. Запись .plt
# ─────────────────────────────────────────────────────────────────

def plt_track() -> Track:
    return Track(segments=[[
        TrackPoint(lat=60.581368, lon=60.154179, altitude_m=188.09,
                   date=45946.2457662),
        TrackPoint(lat=60.581400, lon=60.154200, altitude_m=189.0),
    ], [
        TrackPoint(lat=60.59, lon=60.16, altitude_m=ALTITUDE_UNKNOWN),
    ]])


@pytest.mark.story('Запись .plt')
class TestWritePlt:
    @pytest.fixture
    def lines(self, tmp_path):
        path = write_plt(plt_track(), tmp_path / 'track.plt',
                         '20251016_Copter01')
        text = path.read_bytes().decode('cp1251')
        assert text.endswith('\r\n')
        return text.splitlines()

    @pytest.mark.smoke
    def test_header_is_oziexplorer_header(self, lines):
        assert lines[0] == 'OziExplorer Track Point File Version 2.1'
        assert lines[1] == 'WGS 84'
        assert lines[2] == 'Altitude is in Feet'
        assert lines[3] == 'Reserved 3'

    def test_settings_line_keeps_name(self, lines):
        assert lines[4] == ('0,2,255,20251016_Copter01                  ,'
                            '0,0,2,8421376,-1,0')

    def test_count_line(self, lines):
        assert lines[5] == '3'

    @pytest.mark.smoke
    def test_point_line_looks_like_oziexplorer(self, lines):
        """Высота переводится в футы, время берётся из TDateTime."""
        assert lines[6] == ('  60.581368,  60.154179,1,  617.1,'
                            '45946.2457662, 16-окт-25, 5:53:54')

    def test_segment_break_is_marked(self, lines):
        assert lines[7].split(',')[2] == '0'
        assert lines[8].split(',')[2] == '1'

    def test_unknown_altitude_stays_unknown(self, lines):
        assert lines[8].split(',')[3].strip() == '-777.0'

    def test_unknown_time_leaves_empty_fields(self, lines):
        assert lines[8].count(',') == 6
        assert lines[8].split(',')[4] == ''

    def test_long_name_is_cut(self, tmp_path):
        path = write_plt(plt_track(), tmp_path / 'long.plt', 'Я' * 50)

        line = path.read_bytes().decode('cp1251').splitlines()[4]

        assert line == f'0,2,255,{"Я" * 35},0,0,2,8421376,-1,0'

    def test_comma_in_name_is_replaced(self, tmp_path):
        """Запятая в текстовом поле сломала бы разбор строки по запятым."""
        path = write_plt(plt_track(), tmp_path / 'comma.plt', 'трек, копия')

        line = path.read_bytes().decode('cp1251').splitlines()[4]

        assert 'трек  копия' in line


# ─────────────────────────────────────────────────────────────────
# 7. Запись .wpt
# ─────────────────────────────────────────────────────────────────

def wpt_points():
    return [
        Waypoint(name='A1', lat=57.483576, lon=60.990078,
                 date=46259.8488773, altitude_m=200.5,
                 description='точка, с запятой'),
        Waypoint(name='B1', lat=57.483709, lon=60.998412),
    ]


@pytest.mark.story('Запись .wpt')
class TestWriteWpt:
    @pytest.fixture
    def lines(self, tmp_path):
        path = write_wpt(wpt_points(), tmp_path / 'points.wpt')
        text = path.read_bytes().decode('cp1251')
        assert text.endswith('\r\n')
        return text.splitlines()

    @pytest.mark.smoke
    def test_header(self, lines):
        assert lines[0] == 'OziExplorer Waypoint File Version 1.1'
        assert lines[1] == 'WGS 84'
        assert lines[2] == 'Reserved 2'
        assert lines[3] == 'garmin'

    def test_fields_count(self, lines):
        assert len(lines) == 6
        assert len(lines[4].split(',')) == 24

    @pytest.mark.smoke
    def test_first_point(self, lines):
        fields = lines[4].split(',')
        assert fields[0] == '1'
        assert fields[1] == 'A1'
        assert fields[2] == '57.483576'
        assert fields[3] == '60.990078'
        assert fields[4] == '46259.8488773'
        assert fields[6] == '1'                  # статус
        assert fields[14] == '657.8'             # высота в футах

    def test_defaults_are_left_empty(self, lines):
        """Пустое поле — это «взять умолчание OziExplorer»."""
        fields = lines[5].split(',')
        assert fields[4] == ''                   # дата
        assert fields[5] == ''                   # символ
        assert fields[6] == '1'                  # статус пишем всегда
        assert fields[14] == '-777.0'

    def test_comma_in_description_is_replaced(self, lines):
        assert 'точка  с запятой' in lines[4]

    def test_read_back_by_our_parser(self, tmp_path):
        """Наш же разбор .wpt должен вернуть те же точки."""
        path = write_wpt(wpt_points(), tmp_path / 'points.wpt')

        back = read_wpt(path)

        assert [p.name for p in back] == ['A1', 'B1']
        assert back[0].lat == pytest.approx(57.483576)
        assert back[0].lon == pytest.approx(60.990078)
        assert back[0].date == pytest.approx(46259.8488773)
        assert back[0].altitude_m == pytest.approx(200.5, abs=0.01)
        # Запятая в тексте заменена пробелом: она сломала бы разбор
        # строки по запятым, а символ 209 в cp1251 — это буква «С».
        assert back[0].description == 'точка  с запятой'
        assert back[1].altitude_m == ALTITUDE_UNKNOWN

    def test_empty_list_writes_header_only(self, tmp_path):
        path = write_wpt([], tmp_path / 'empty.wpt')

        assert path.read_bytes().decode('cp1251').splitlines() == [
            'OziExplorer Waypoint File Version 1.1',
            'WGS 84',
            'Reserved 2',
            'garmin',
        ]
