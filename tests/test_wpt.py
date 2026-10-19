"""Тесты разбора файлов точек OziExplorer (ok_helper.wpt).

Функциональность: parse_line(), parse_wpt(), decode_wpt(), read_wpt() —
точка из строки .wpt в терминах oziCreateWPex. Разбор нужен, чтобы
**добавить** файл точек к уже загруженным в OziExplorer: API умеет
грузить файл точек только целиком (oziLoadWPfile заменяет набор).

Особенности формата, которые проверяются:
  * запятая внутри текста записана символом 209 (0xD1);
  * высота в файле — в футах, API ждёт метры, -777 — «неизвестна»;
  * дата — число Delphi TDateTime, а не текст;
  * кодировка — ANSI (cp1251), но встречается и UTF-8.

Группировка в Allure:
  epic    = ok-filemanager
  feature = Разбор файлов точек OziExplorer
  story   = задаётся маркером @pytest.mark.story на каждом классе.
"""

from pathlib import Path

import allure
import pytest

from ok_helper import wpt
from ok_helper.wpt import (
    ALTITUDE_UNKNOWN,
    DEFAULT,
    Waypoint,
    decode_wpt,
    parse_line,
    parse_wpt,
    read_wpt,
)


pytestmark = [
    pytest.mark.wpt,
    pytest.mark.epic('ok-filemanager'),
    pytest.mark.feature('Разбор файлов точек OziExplorer'),
]


# Заголовок реального файла OziExplorer (4 строки).
HEADER = ('OziExplorer Waypoint File Version 1.1\r\n'
          'WGS 84\r\n'
          'Reserved 2\r\n'
          'garmin\r\n')

# Строка из реального файла: текст с запятыми-символом-209, высота -777,
# подряд идущие пробелы и завершающие пустые поля. Байты, а не текст:
# так строка выглядит в самом файле (cp1251).
REAL_LINE = (
    b'1,BVP,  56.423073,  61.877189,46105.7564356,  0, 1, 3,         0,'
    b'       255,\xd1\xe8\xed\xe0\xf0\xf1\xea\xe8\xe9 \xf0\xe0\xe9\xee\xed'
    b'\xd1 \xe3\xee\xf0\xee\xe4\xf1\xea\xee\xe9 \xee\xea\xf0\xf3\xe3,'
    b' 0, 0,    0,   -777, 6, 0,17,0,10.0,2,,,,300\r\n'
).decode('cp1251')


# ─────────────────────────────────────────────────────────────────
# 1. Разбор одной строки
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Разбор строки')
class TestParseLine:
    @pytest.mark.smoke
    def test_real_line(self):
        point = parse_line(REAL_LINE)

        assert point is not None
        assert point.name == 'BVP'
        assert point.lat == pytest.approx(56.423073)
        assert point.lon == pytest.approx(61.877189)
        assert point.symbol == 0

    def test_positional_fields(self):
        line = ('3,Точка,55.5,37.5,44927.25,7,1,5,0,2,10,1,0,328,'
                '12,13,14,15,16,16.5,17')

        point = parse_line(line)

        assert point.name == 'Точка'
        assert point.date == pytest.approx(44927.25)
        assert point.symbol == 7
        assert point.map_display_format == 5
        assert point.fore_color == 0
        assert point.back_color == 2
        assert point.description == '10'
        assert point.pointer_direction == 1
        assert point.garmin_display_format == 0
        assert point.proximity_distance == 328
        assert point.altitude_m == pytest.approx(12 * 0.3048)
        assert point.font_size == 13
        assert point.font_style == 14
        assert point.symbol_size == 15
        assert point.proximity_symbol_pos == 16
        assert point.proximity_time == pytest.approx(16.5)
        assert point.proximity_route == 17

    def test_altitude_feet_to_meters(self):
        """В файле футы, API ждёт метры (иначе точка уедет по высоте)."""
        point = parse_line('1,A,55.0,37.0,,1,1,1,0,0,,0,1,0,3280')

        assert point.altitude_m == pytest.approx(3280 * 0.3048)

    def test_altitude_unknown_is_passed_through(self):
        point = parse_line('1,A,55.0,37.0,,1,1,1,0,0,,0,1,0,-777')

        assert point.altitude_m == ALTITUDE_UNKNOWN

    def test_altitude_missing_is_unknown(self):
        point = parse_line('1,A,55.0,37.0')

        assert point.altitude_m == ALTITUDE_UNKNOWN

    def test_comma_substitute_is_decoded(self):
        """Западная кодировка: символ 209 — это записанная запятая."""
        point = parse_line('1,A,55.0,37.0,,1,1,1,0,0,'
                           + wpt.COMMA_SUBSTITUTE.join(['ул. Мира', 'д. 5']))

        assert point.description == 'ул. Мира,д. 5'

    def test_cp1251_text_keeps_letter_es(self):
        """В cp1251 тот же байт — буква «С», поэтому текст не трогаем.

        Иначе «Синарский» превратился бы в «,инарский»: служебный байт и
        буква в русском файле неразличимы, и OziExplorer, читая файл сам,
        видит ту же букву.
        """
        point = parse_line('1,A,55.0,37.0,,1,1,1,0,0,Синарский район')

        assert point.description == 'Синарский район'

    def test_date_is_a_number(self):
        """Дата — TDateTime, а не текст: API ждёт число."""
        point = parse_line('1,A,55.0,37.0,46105.7564356')

        assert isinstance(point.date, float)
        assert point.date == pytest.approx(46105.7564356)

    def test_missing_optional_fields_use_api_defaults(self):
        """Чего нет в файле — -1, «взять умолчание OziExplorer»."""
        point = parse_line('5,A,55.0,37.0')

        assert point.date == DEFAULT
        assert point.symbol == DEFAULT
        assert point.font_size == DEFAULT
        assert point.proximity_route == DEFAULT

    def test_empty_line_is_none(self):
        assert parse_line('') is None
        assert parse_line('   \r\n') is None

    def test_comment_or_garbage_is_none(self):
        assert parse_line('Reserved 2') is None
        assert parse_line('это не точка') is None

    def test_broken_coordinates_are_none(self):
        """Строка с нечисловыми координатами не должна ломать разбор."""
        assert parse_line('1,A,широта,долгота') is None
        assert parse_line('1,A,,37.0') is None

    def test_spaces_are_stripped(self):
        point = parse_line('  4 , A B , 55.5 , 37.5 ')

        assert point.name == 'A B'
        assert point.lat == pytest.approx(55.5)

    def test_field_order_matches_ozihelp(self):
        """Порядок полей — как в справке OziExplorer (1-based)."""
        assert Waypoint.__dataclass_fields__['symbol'] is not None
        point = parse_line('1,N,1.0,2.0,3.0,4,5,6,7,8,9,10,11,328,'
                           '12,13,14,15,16,17,18')

        assert point.symbol == 4            # поле 6
        assert point.map_display_format == 6  # поле 8 (поле 7 — статус)
        assert point.description == '9'     # поле 11
        assert point.proximity_route == 18  # поле 21


# ─────────────────────────────────────────────────────────────────
# 2. Разбор файла целиком
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Разбор файла')
class TestParseWpt:
    def test_header_is_skipped(self):
        text = HEADER + '1,A,55.0,37.0\r\n2,B,56.0,38.0\r\n'

        points = parse_wpt(text)

        assert [p.name for p in points] == ['A', 'B']

    def test_without_header(self):
        text = '1,A,55.0,37.0\r\n2,B,56.0,38.0\r\n'

        points = parse_wpt(text)

        assert [p.name for p in points] == ['A', 'B']

    def test_empty_file(self):
        assert parse_wpt('') == []
        assert parse_wpt(HEADER) == []

    def test_trailing_blank_lines_ignored(self):
        text = HEADER + '1,A,55.0,37.0\r\n\r\n\r\n'

        assert len(parse_wpt(text)) == 1

    def test_broken_line_does_not_stop_parsing(self):
        text = HEADER + '1,A,55.0,37.0\r\nмусор\r\n2,B,56.0,38.0\r\n'

        assert [p.name for p in points_of(text)] == ['A', 'B']

    def test_real_file(self, tmp_path):
        """Файл, записанный самим OziExplorer: cp1251 и все поля."""
        src = tmp_path / 'Waypoints.wpt'
        src.write_bytes(HEADER.encode('cp1251') + REAL_LINE.encode('cp1251'))

        points = read_wpt(src)

        assert len(points) == 1
        assert points[0].name == 'BVP'
        assert points[0].altitude_m == ALTITUDE_UNKNOWN
        assert points[0].description.startswith('Синарский район')
        # байт 209 остался буквой «С»: так же его прочитает и сам
        # OziExplorer (в cp1251 это один и тот же байт)
        assert wpt.COMMA_SUBSTITUTE not in points[0].description


def points_of(text):
    return parse_wpt(text)


# ─────────────────────────────────────────────────────────────────
# 3. Кодировки
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Кодировки')
class TestDecode:
    def test_cp1251(self):
        raw = '1,Ёлка,55.0,37.0'.encode('cp1251')

        assert decode_wpt(raw).startswith('1,Ёлка')

    def test_utf8_with_bom(self):
        """OziExplorerCE/Android пишут UTF-8, иногда с BOM."""
        raw = '1,Ёлка,55.0,37.0'.encode('utf-8-sig')

        text = decode_wpt(raw)

        assert text.startswith('1,Ёлка')
        assert not text.startswith('\ufeff')

    def test_utf8_without_bom(self):
        raw = '1,Ёлка,55.0,37.0'.encode('utf-8')

        assert decode_wpt(raw).startswith('1,Ёлка')

    def test_broken_bytes_do_not_raise(self):
        raw = b'1,A,55.0,37.0,1,\xff\xfe\xfd'

        assert decode_wpt(raw).startswith('1,A')


# ─────────────────────────────────────────────────────────────────
# 4. read_wpt и сериализация для моста
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('read_wpt')
class TestReadWpt:
    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(OSError):
            read_wpt(tmp_path / 'nope.wpt')

    def test_reads_utf8(self, tmp_path):
        src = tmp_path / 'points.wpt'
        src.write_text(HEADER + '1,Ёлка,55.0,37.0\r\n', encoding='utf-8')

        assert read_wpt(src)[0].name == 'Ёлка'

    def test_reads_cp1251(self, tmp_path):
        src = tmp_path / 'points.wpt'
        src.write_bytes((HEADER + '1,Ёлка,55.0,37.0\r\n').encode('cp1251'))

        assert read_wpt(src)[0].name == 'Ёлка'

    def test_accepts_str_path(self, tmp_path):
        src = tmp_path / 'points.wpt'
        src.write_text('1,A,55.0,37.0\r\n', encoding='utf-8')

        assert read_wpt(str(src))[0].name == 'A'


@pytest.mark.story('Поля для oziCreateWPex')
class TestAsApi:
    @pytest.mark.smoke
    def test_keys_match_bridge_protocol(self):
        """Мост читает именно эти имена (PowerShell ConvertFrom-Json)."""
        data = Waypoint(name='A', lat=1.0, lon=2.0).as_api()

        assert set(data) == {
            'name', 'symbol', 'lat', 'lon', 'altitude', 'date',
            'mapDisplayFormat', 'pointerDirection', 'garminDisplayFormat',
            'foreColor', 'backColor', 'proximityDistance', 'description',
            'fontSize', 'fontStyle', 'symbolSize', 'proximitySymbolPos',
            'proximityTime', 'proximityRoute',
        }

    def test_types_are_json_friendly(self):
        data = parse_line('1,A,55,37,44927,7,1,5,0,2,,1,0,328,10').as_api()

        for key in ('symbol', 'mapDisplayFormat', 'foreColor', 'fontSize'):
            assert isinstance(data[key], int)
        for key in ('lat', 'lon', 'altitude', 'date', 'proximityTime'):
            assert isinstance(data[key], float)
        assert isinstance(data['name'], str)

    def test_altitude_in_meters_is_ready_for_api(self):
        """API ждёт метры, поэтому в мост уходит уже переведённое число."""
        data = parse_line('1,A,55,37,,1,1,1,0,0,,0,1,0,100').as_api()

        assert data['altitude'] == pytest.approx(30.48)

    def test_date_is_sent_as_number_not_text(self):
        data = parse_line('1,A,55,37,46105.756').as_api()

        assert data['date'] == pytest.approx(46105.756)


# ─────────────────────────────────────────────────────────────────
# 5. Реальные файлы пользователя (если есть)
# ─────────────────────────────────────────────────────────────────

REAL_FILES = sorted(Path(r'D:\LA\Треки').rglob('Waypoints_*.wpt')) \
    if Path(r'D:\LA\Треки').is_dir() else []


@pytest.mark.slow
@pytest.mark.story('Реальные файлы OziExplorer')
@pytest.mark.skipif(not REAL_FILES, reason='нет файлов в D:\\LA')
class TestRealFiles:
    def test_every_file_parses(self):
        """Ни один из настоящих .wpt не должен уронить разбор."""
        for src in REAL_FILES:
            points = read_wpt(src)
            assert points, f'{src} — не разобралось ни одной точки'
            for point in points:
                assert -90 <= point.lat <= 90, src
                assert -180 <= point.lon <= 180, src
                assert point.name, src

    def test_altitudes_are_meters_or_unknown(self):
        """Футы переведены: значения не могут остаться «футовыми»."""
        for src in REAL_FILES[:5]:
            for point in read_wpt(src):
                if point.altitude_m != ALTITUDE_UNKNOWN:
                    # Эверест — 8848 м; 29 000 «футов» так не выглядят.
                    assert -1000 < point.altitude_m < 10000, (src, point)
