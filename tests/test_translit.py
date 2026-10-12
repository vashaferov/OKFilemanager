"""Тесты транслитерации кириллицы (ok_helper.translit).

Функциональность: transliterate() — ГОСТ 7.79-2000, система B.

Группировка в Allure:
  epic    = ok-helper-tui
  feature = Транслитерация
  story   = задаётся маркером @pytest.mark.story на каждом классе.
"""

import allure
import pytest

from ok_helper.translit import transliterate


pytestmark = [
    pytest.mark.translit,
    pytest.mark.epic('ok-helper-tui'),
    pytest.mark.feature('Транслитерация'),
]


# ─────────────────────────────────────────────────────────────────
# 1. Таблица соответствия — все буквы алфавита
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Таблица соответствия')
class TestTransliterateTable:
    """Явная спецификация ГОСТ 7.79-2000 (система B).

    Пары выписаны руками, а не взяты из _TRANSLIT_MAP, чтобы тест
    проверял таблицу, а не повторял её.
    """

    @pytest.mark.smoke
    @pytest.mark.parametrize('inp,expected', [
        ('а', 'a'),   ('б', 'b'),   ('в', 'v'),   ('г', 'g'),
        ('д', 'd'),   ('е', 'e'),   ('ё', 'yo'),  ('ж', 'zh'),
        ('з', 'z'),   ('и', 'i'),   ('й', 'y'),   ('к', 'k'),
        ('л', 'l'),   ('м', 'm'),   ('н', 'n'),   ('о', 'o'),
        ('п', 'p'),   ('р', 'r'),   ('с', 's'),   ('т', 't'),
        ('у', 'u'),   ('ф', 'f'),   ('х', 'kh'),  ('ц', 'ts'),
        ('ч', 'ch'),  ('ш', 'sh'),  ('щ', 'shch'),
        ('ъ', ''),    ('ы', 'y'),   ('ь', ''),
        ('э', 'e'),   ('ю', 'yu'),  ('я', 'ya'),
    ])
    def test_lowercase_letters(self, inp, expected):
        assert transliterate(inp) == expected

    @pytest.mark.smoke
    @pytest.mark.parametrize('inp,expected', [
        ('А', 'A'),   ('Б', 'B'),   ('В', 'V'),   ('Г', 'G'),
        ('Д', 'D'),   ('Е', 'E'),   ('Ё', 'Yo'),  ('Ж', 'Zh'),
        ('З', 'Z'),   ('И', 'I'),   ('Й', 'Y'),   ('К', 'K'),
        ('Л', 'L'),   ('М', 'M'),   ('Н', 'N'),   ('О', 'O'),
        ('П', 'P'),   ('Р', 'R'),   ('С', 'S'),   ('Т', 'T'),
        ('У', 'U'),   ('Ф', 'F'),   ('Х', 'Kh'),  ('Ц', 'Ts'),
        ('Ч', 'Ch'),  ('Ш', 'Sh'),  ('Щ', 'Shch'),
        ('Ъ', ''),    ('Ы', 'Y'),   ('Ь', ''),
        ('Э', 'E'),   ('Ю', 'Yu'),  ('Я', 'Ya'),
    ])
    def test_uppercase_letters(self, inp, expected):
        assert transliterate(inp) == expected

    @pytest.mark.parametrize('inp,expected', [
        ('Иванов',      'Ivanov'),
        ('бабушка',     'babushka'),
        ('Ёжик',        'Yozhik'),
        ('Щука',        'Shchuka'),
        ('Москва',      'Moskva'),
        ('Радио-1',     'Radio-1'),
        ('подъезд',     'podezd'),
        ('объект',      'obekt'),
        ('Новый год',   'Novyy god'),
    ])
    def test_words(self, inp, expected):
        assert transliterate(inp) == expected


# ─────────────────────────────────────────────────────────────────
# 2. Символы вне таблицы переносятся как есть
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Нетранслитерируемые символы')
class TestPassthrough:
    @pytest.mark.parametrize('text', [
        'Ivanov',
        'lisa_01',
        '2026-09-23',
        'file name with spaces',
        '',
    ])
    def test_latin_and_punctuation_untouched(self, text):
        assert transliterate(text) == text

    @pytest.mark.parametrize('text', ['ü', '中文', 'emoji 🙂', 'naïve'])
    def test_non_cyrillic_unicode_untouched(self, text):
        assert transliterate(text) == text

    def test_latin_passthrough_is_identity(self):
        assert transliterate('Ivanov_01.A3B2') == 'Ivanov_01.A3B2'

    def test_empty_string(self):
        assert transliterate('') == ''

    def test_digits_and_underscore(self):
        assert transliterate('123_456') == '123_456'

    def test_mixed_script(self):
        assert transliterate('Трек-2026_ok') == 'Trek-2026_ok'


# ─────────────────────────────────────────────────────────────────
# 3. Идемпотентность
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Идемпотентность')
class TestIdempotence:
    @pytest.mark.parametrize('text', [
        'бабушка',
        'Ёжик',
        'Щука',
        '2026-09-17_11-55_бабушка (Копия 2)',
        'Радио-1',
        'Трек с пробелами',
    ])
    def test_double_translit_is_stable(self, text):
        once = transliterate(text)
        assert transliterate(once) == once

    def test_case_is_preserved(self):
        assert transliterate('иВаНоВ') == 'iVaNoV'


# ─────────────────────────────────────────────────────────────────
# 4. Совместимость с именами файлов из README
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Транслитерация в именах файлов')
class TestFileNames:
    @pytest.mark.smoke
    @pytest.mark.parametrize('inp,expected', [
        ('2026-09-17_11-55_бабушка.gpx', '2026-09-17_11-55_babushka.gpx'),
        ('20260923_Иванов.gpx',          '20260923_Ivanov.gpx'),
        ('20260923_Радио-1.plt',         '20260923_Radio-1.plt'),
        ('Мой трек (Копия 2).gpx',       'Moy trek (Kopiya 2).gpx'),
    ])
    def test_downloaded_names(self, inp, expected):
        assert transliterate(inp) == expected

    def test_result_contains_no_cyrillic(self):
        result = transliterate('2026-09-23_Безымянный трек.gpx')
        assert all(ord(ch) < 128 for ch in result)
