"""Тесты общей валидации имён файлов (ok_helper.analyzer).

Функциональность: analyze_file(), sanitize_callsign(),
normalize_callsign(), extract_date_from_gpx(), is_m_gpx(),
validate_new_folder_name(), human_size().

Группировка в Allure:
  epic    = ok-helper-tui
  feature = Валидация имён файлов
  story   = задаётся маркером @pytest.mark.story на каждом классе.
"""

import allure
import pytest
from pathlib import Path

from ok_helper.analyzer import (
    analyze_file,
    extract_date_from_gpx,
    human_size,
    is_m_gpx,
    normalize_callsign,
    sanitize_callsign,
    validate_new_folder_name,
)


pytestmark = [
    pytest.mark.analyzer,
    pytest.mark.epic('ok-helper-tui'),
    pytest.mark.feature('Валидация имён файлов'),
]


def touch(p: Path) -> Path:
    p.write_text('')
    return p


def make_gpx(p: Path, last_time: str = '2026-09-17T11:55:03Z') -> Path:
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="test">\n'
        '  <trk>\n'
        '    <trkseg>\n'
        '      <trkpt lat="55.0" lon="37.0">\n'
        f'        <time>{last_time}</time>\n'
        '      </trkpt>\n'
        '    </trkseg>\n'
        '  </trk>\n'
        '</gpx>\n'
    )
    p.write_text(content, encoding='utf-8')
    return p


def make_gpx_multipoint(p: Path, times: list) -> Path:
    pts = ''.join(
        f'      <trkpt lat="55.0" lon="37.0">\n'
        f'        <time>{t}</time>\n'
        f'      </trkpt>\n'
        for t in times
    )
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="test">\n'
        '  <trk>\n'
        '    <trkseg>\n'
        f'{pts}'
        '    </trkseg>\n'
        '  </trk>\n'
        '</gpx>\n'
    )
    p.write_text(content, encoding='utf-8')
    return p


# ─────────────────────────────────────────────────────────────────
# 0. normalize_callsign — чистая функция
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Нормализация позывного')
class TestNormalizeCallsign:
    @pytest.mark.smoke
    @pytest.mark.parametrize('inp,expected', [
        # первая буква — заглавная
        ('lisa',       'Lisa'),
        ('Lisa',       'Lisa'),
        ('x',          'X'),

        # хвостовой номер — 2 разряда, разделитель убран
        ('lisa_1',     'Lisa01'),
        ('lisa1',      'Lisa01'),
        ('lisa_01',    'Lisa01'),
        ('lisa-1',     'Lisa01'),
        ('lisa_12',    'Lisa12'),
        ('lisa12',     'Lisa12'),
        ('lisa_001',   'Lisa001'),   # 3+ цифр — как есть
        ('lisa_123',   'Lisa123'),

        # идемпотентность
        ('Lisa01',     'Lisa01'),

        # номер в СЕРЕДИНЕ позывного (с суффиксом после)
        ('Lisa_03_Test',  'Lisa03_Test'),
        ('Lisa_04_1',     'Lisa04_1'),
        ('lisa_03_test',  'Lisa03_test'),
        ('lisa-05-test',  'Lisa05-test'),

        # если база оканчивается цифрой — хвостовая группа _N/-N
        # НЕ трогается
        ('Lisa01_1',   'Lisa01_1'),
        ('Lisa01_12',  'Lisa01_12'),
        ('Lisa01-1',   'Lisa01-1'),
        ('lisa01_1',   'Lisa01_1'),
        ('Lisa99_1',   'Lisa99_1'),

        # если база не чисто буквенная и разделителя нет —
        # цифры считаются частью позывного
        ('A1B2',       'A1B2'),
        ('abc123',     'Abc123'),

        # underscore между словами
        ('ivan_petr',  'Ivan_petr'),
        ('ivan_petr_2','Ivan_petr02'),

        # цифры в начале
        ('123',        '123'),

        # пустая строка
        ('',           ''),
    ])
    def test_normalize(self, inp, expected):
        assert normalize_callsign(inp) == expected

    def test_idempotent(self):
        for x in ('lisa', 'lisa_1', 'lisa1', 'Lisa01',
                  'ivan_petr_2', 'Lisa01_1', 'lisa01_1',
                  'Lisa_03_Test', 'Lisa_04_1', 'Lisa03_Test'):
            once = normalize_callsign(x)
            assert normalize_callsign(once) == once, x


# ─────────────────────────────────────────────────────────────────
# 1. Уже валидный формат ГГГГММДД_Позывной для .gpx / .plt
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Формат ГГГГММДД_Позывной')
class TestCanonicalFormat:
    @pytest.mark.smoke
    @pytest.mark.parametrize('name', [
        '20260923_Ivanov.gpx',
        '20260923_Ivanov.plt',
        '20260923_Ivan01.gpx',
        '20260923_Ivan01.plt',
        '20260923_A1B2.gpx',
        '20260923_X.plt',
        '20260923_Lisa01_1.gpx',
        '20260923_Lisa01_12.plt',
        # номер в середине — уже валидные имена
        '20261004_Lisa03_Test.gpx',
        '20261004_Lisa04_1.gpx',
    ])
    def test_already_valid(self, tmp_path, name):
        info = analyze_file(touch(tmp_path / name))
        assert info['has_error'] is False
        assert info['new_name'] is None

    @pytest.mark.parametrize('name', [
        '20260923_Ivanov.GPX',
        '20260923_Ivanov.Plt',
    ])
    def test_extension_case_insensitive(self, tmp_path, name):
        info = analyze_file(touch(tmp_path / name))
        assert info['has_error'] is False


# ─────────────────────────────────────────────────────────────────
# 2. Транслит и нормализация позывного в .gpx / .plt
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Транслит и нормализация позывного')
class TestTranslitCallsign:
    @pytest.mark.translit
    @pytest.mark.smoke
    def test_gpx_cyrillic(self, tmp_path):
        info = analyze_file(touch(tmp_path / '20260923_Иванов.gpx'))
        assert info['has_error'] is True
        assert info['new_name'] == '20260923_Ivanov.gpx'
        assert info['confidence'] == 'high'

    @pytest.mark.translit
    @pytest.mark.smoke
    def test_plt_with_dash_number(self, tmp_path):
        info = analyze_file(touch(tmp_path / '20260923_Радио-1.plt'))
        assert info['has_error'] is True
        assert info['new_name'] == '20260923_Radio01.plt'
        assert info['confidence'] == 'high'

    @pytest.mark.smoke
    def test_capitalize_first_letter(self, tmp_path):
        info = analyze_file(touch(tmp_path / '20260923_lisa.gpx'))
        assert info['has_error'] is True
        assert info['new_name'] == '20260923_Lisa.gpx'

    @pytest.mark.smoke
    def test_number_underscore(self, tmp_path):
        info = analyze_file(touch(tmp_path / '20260923_lisa_1.gpx'))
        assert info['new_name'] == '20260923_Lisa01.gpx'

    @pytest.mark.smoke
    def test_number_glued(self, tmp_path):
        info = analyze_file(touch(tmp_path / '20260923_lisa1.gpx'))
        assert info['new_name'] == '20260923_Lisa01.gpx'

    def test_number_already_two_digits(self, tmp_path):
        info = analyze_file(touch(tmp_path / '20260923_lisa_12.gpx'))
        assert info['new_name'] == '20260923_Lisa12.gpx'

    def test_number_hyphen(self, tmp_path):
        info = analyze_file(touch(tmp_path / '20260923_lisa-1.plt'))
        assert info['new_name'] == '20260923_Lisa01.plt'

    def test_existing_number_with_extra_suffix_kept(self, tmp_path):
        info = analyze_file(touch(tmp_path / '20260923_lisa01_1.gpx'))
        assert info['has_error'] is True
        assert info['new_name'] == '20260923_Lisa01_1.gpx'
        assert info['confidence'] == 'high'

    # Новые кейсы из реальной ситуации
    def test_number_in_middle_with_sep(self, tmp_path):
        info = analyze_file(touch(tmp_path / '20261004_Lisa_03_Test.gpx'))
        assert info['has_error'] is True
        assert info['new_name'] == '20261004_Lisa03_Test.gpx'
        assert info['confidence'] == 'high'

    def test_number_in_middle_with_suffix_number(self, tmp_path):
        info = analyze_file(touch(tmp_path / '20261004_Lisa_04_1.gpx'))
        assert info['has_error'] is True
        assert info['new_name'] == '20261004_Lisa04_1.gpx'
        assert info['confidence'] == 'high'

    def test_number_in_middle_hyphen(self, tmp_path):
        info = analyze_file(touch(tmp_path / '20261004_Lisa-05-Test.gpx'))
        assert info['has_error'] is True
        assert info['new_name'] == '20261004_Lisa05-Test.gpx'

    @pytest.mark.translit
    def test_callsign_unfixable(self, tmp_path):
        info = analyze_file(touch(tmp_path / '20260923_!!!.gpx'))
        assert info['has_error'] is True
        assert info['new_name'] is None


# ─────────────────────────────────────────────────────────────────
# 3. «Скачанные» имена .gpx / .plt
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Скачанные имена')
class TestDownloadedNames:
    @pytest.mark.smoke
    @pytest.mark.parametrize('name,expected', [
        ('2026-09-17_11-55_бабушка.gpx',       '20260917_Babushka.gpx'),
        ('2026-09-17-11-55-бабушка.gpx',       '20260917_Babushka.gpx'),
        ('2026-09-17_бабушка.gpx',             '20260917_Babushka.gpx'),
        ('2026.09.17_бабушка.gpx',             '20260917_Babushka.gpx'),
        ('20260917_бабушка.gpx',               '20260917_Babushka.gpx'),
        ('2026-09-17_11-55_babushka.gpx',      '20260917_Babushka.gpx'),
        ('2026-09-17_11-55_babushka (Копия 2).gpx',
         '20260917_Babushka_Kopiya02.gpx'),
        ('2026-09-17_11-55_бабушка (Копия 2).plt',
         '20260917_Babushka_Kopiya02.plt'),
        ('2026-09-17_11.55_бабушка.gpx',       '20260917_Babushka.gpx'),
        ('2026-09-17-113045-бабушка.gpx',      '20260917_Babushka.gpx'),
        ('17-09-2026_11-55_бабушка.gpx',       '20260917_Babushka.gpx'),
        ('2026-09-17_11-55_babushka_2.gpx',    '20260917_Babushka02.gpx'),
        ('2026-09-17_11-55_babushka2.gpx',     '20260917_Babushka02.gpx'),
    ])
    def test_to_canonical(self, tmp_path, name, expected):
        info = analyze_file(touch(tmp_path / name))
        assert info['has_error'] is True
        assert info['new_name'] == expected
        assert info['confidence'] in ('high', 'medium')


# ─────────────────────────────────────────────────────────────────
# 4. Заглушки (низкая уверенность)
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Заглушки')
class TestPlaceholders:
    @pytest.mark.parametrize('name', [
        '2026-09-23 Безымянный трек.gpx',
        '2026-09-23-1803 Безымянный трек.gpx',
        '2026-09-23_untitled.gpx',
        '2026-09-23_track.gpx',
    ])
    def test_is_low_confidence(self, tmp_path, name):
        info = analyze_file(touch(tmp_path / name))
        assert info['has_error'] is True
        assert info['confidence'] == 'low'
        assert 'заглушк' in info['reason']


# ─────────────────────────────────────────────────────────────────
# 5. Нет даты в имени
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Нет даты в имени')
class TestNoDate:
    def test_plt_no_suggestion(self, tmp_path):
        info = analyze_file(touch(tmp_path / 'some_random_file.plt'))
        assert info['has_error'] is True
        assert info['new_name'] is None
        assert 'дата' in info['reason']

    def test_gpx_no_date_in_file(self, tmp_path):
        p = tmp_path / 'track.gpx'
        p.write_text('<?xml version="1.0"?><gpx></gpx>', encoding='utf-8')
        info = analyze_file(p)
        assert info['has_error'] is True
        assert info['new_name'] is None
        assert 'ни в имени, ни в файле' in info['reason']

    def test_gpx_empty_file(self, tmp_path):
        p = tmp_path / 'babushka.gpx'
        p.write_text('', encoding='utf-8')
        info = analyze_file(p)
        assert info['has_error'] is True
        assert info['new_name'] is None


# ─────────────────────────────────────────────────────────────────
# 6. Дата из содержимого .gpx
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Дата из содержимого .gpx')
class TestGpxContentDate:
    @pytest.mark.smoke
    def test_date_from_single_point(self, tmp_path):
        p = make_gpx(tmp_path / 'babushka.gpx',
                     last_time='2026-09-17T11:55:03Z')
        info = analyze_file(p)
        assert info['has_error'] is True
        assert info['new_name'] == '20260917_Babushka.gpx'
        assert info['confidence'] == 'medium'

    def test_date_from_last_of_many_points(self, tmp_path):
        p = make_gpx_multipoint(tmp_path / 'track.gpx', [
            '2025-01-01T00:00:00Z',
            '2026-09-17T11:50:00Z',
            '2026-09-17T11:55:03Z',
        ])
        info = analyze_file(p)
        assert info['new_name'] == '20260917_Track.gpx'
        assert info['confidence'] == 'low'

    def test_cyrillic_name_with_content_date(self, tmp_path):
        p = make_gpx(tmp_path / 'бабушка.gpx')
        info = analyze_file(p)
        assert info['new_name'] == '20260917_Babushka.gpx'

    def test_name_with_spaces_and_copy(self, tmp_path):
        p = make_gpx(tmp_path / 'Мой трек (Копия).gpx')
        info = analyze_file(p)
        assert info['new_name'] == '20260917_Moy_trek_Kopiya.gpx'

    def test_name_with_copy_and_number(self, tmp_path):
        p = make_gpx(tmp_path / 'Мой трек (Копия 2).gpx')
        info = analyze_file(p)
        assert info['new_name'] == '20260917_Moy_trek_Kopiya02.gpx'

    def test_m_gpx_exception_still_wins(self, tmp_path):
        p = make_gpx(tmp_path / '100m.gpx',
                     last_time='2026-09-17T11:55:03Z')
        info = analyze_file(p)
        assert info['has_error'] is False

    def test_invalid_dates_in_content_ignored(self, tmp_path):
        p = tmp_path / 'babushka.gpx'
        p.write_text(
            '<?xml version="1.0"?>\n'
            '<gpx>\n'
            '  <trkpt><time>2026-13-45T99:99:99Z</time></trkpt>\n'
            '  <trkpt><time>2026-09-17T11:55:03Z</time></trkpt>\n'
            '</gpx>\n',
            encoding='utf-8',
        )
        info = analyze_file(p)
        assert info['new_name'] == '20260917_Babushka.gpx'

    def test_gpx_with_only_invalid_dates(self, tmp_path):
        p = tmp_path / 'babushka.gpx'
        p.write_text(
            '<?xml version="1.0"?>\n'
            '<gpx>\n'
            '  <time>2026-99-99T00:00:00Z</time>\n'
            '</gpx>\n',
            encoding='utf-8',
        )
        info = analyze_file(p)
        assert info['has_error'] is True
        assert info['new_name'] is None

    def test_bom_and_utf8_ok(self, tmp_path):
        p = tmp_path / 'бабушка.gpx'
        content = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<gpx><trk><trkseg><trkpt>'
            '<time>2026-09-17T11:55:03Z</time>'
            '</trkpt></trkseg></trk></gpx>\n'
        )
        p.write_bytes(content.encode('utf-8-sig'))
        info = analyze_file(p)
        assert info['new_name'] == '20260917_Babushka.gpx'

    def test_date_at_very_end_of_large_file(self, tmp_path):
        p = tmp_path / 'big.gpx'
        filler = '<trkpt lat="0" lon="0"/>' * 20000
        content = (
            '<?xml version="1.0"?>\n'
            f'<gpx>{filler}'
            '<trkpt><time>2026-09-17T11:55:03Z</time></trkpt>'
            '</gpx>\n'
        )
        p.write_text(content, encoding='utf-8')
        assert p.stat().st_size > 400_000
        info = analyze_file(p)
        assert info['new_name'] == '20260917_Big.gpx'


# ─────────────────────────────────────────────────────────────────
# 7. extract_date_from_gpx — прямая функция
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('extract_date_from_gpx')
class TestExtractDateFunction:
    def test_returns_none_for_missing_file(self, tmp_path):
        assert extract_date_from_gpx(tmp_path / 'nonexistent.gpx') is None

    def test_returns_none_for_empty_file(self, tmp_path):
        p = tmp_path / 'empty.gpx'
        p.write_text('', encoding='utf-8')
        assert extract_date_from_gpx(p) is None

    def test_returns_none_without_dates(self, tmp_path):
        p = tmp_path / 'nodates.gpx'
        p.write_text('<gpx></gpx>', encoding='utf-8')
        assert extract_date_from_gpx(p) is None

    def test_returns_last_date(self, tmp_path):
        p = make_gpx_multipoint(tmp_path / 'multi.gpx', [
            '2025-01-01T00:00:00Z',
            '2026-09-17T11:55:03Z',
        ])
        assert extract_date_from_gpx(p) == '20260917'

    def test_returns_str_in_yyyymmdd_format(self, tmp_path):
        p = make_gpx(tmp_path / 'x.gpx', '2026-09-17T11:55:03Z')
        result = extract_date_from_gpx(p)
        assert isinstance(result, str)
        assert result == '20260917'
        assert len(result) == 8


# ─────────────────────────────────────────────────────────────────
# 8. Исключение: <1-4 цифры>m.gpx — валидация не проводится
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Исключение <1-4>m.gpx')
class TestMGpxException:
    @pytest.mark.smoke
    @pytest.mark.parametrize('name', [
        '1m.gpx',
        '12m.gpx',
        '999m.gpx',
        '9999m.gpx',
        '20260923_100m.gpx',
        'track_5M.gpx',
        '1m.GPX',
        '_100m.gpx',
    ])
    def test_skips_validation(self, tmp_path, name):
        info = analyze_file(touch(tmp_path / name))
        assert info['has_error'] is False
        assert info['new_name'] is None

    @pytest.mark.parametrize('name', [
        'm.gpx',
        '12345m.gpx',
        '123456m.gpx',
        'track.gpx',
        'track_100ms.gpx',
        'track_m100.gpx',
    ])
    def test_falls_through(self, tmp_path, name):
        info = analyze_file(touch(tmp_path / name))
        assert info['has_error'] is True


# ─────────────────────────────────────────────────────────────────
# 9. .wpt — только Waypoints_ГГГГММДД
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Waypoints (.wpt)')
class TestWpt:
    @pytest.mark.smoke
    @pytest.mark.parametrize('name', [
        'Waypoints_20260923.wpt',
        'waypoints_20260923.wpt',
        'WAYPOINTS_20260923.WPT',
    ])
    def test_valid(self, tmp_path, name):
        info = analyze_file(touch(tmp_path / name))
        assert info['has_error'] is False

    def test_from_downloaded_name(self, tmp_path):
        info = analyze_file(touch(tmp_path / '2026-09-23_waypoints.wpt'))
        assert info['has_error'] is True
        assert info['new_name'] == 'Waypoints_20260923.wpt'
        assert info['confidence'] == 'medium'

    def test_no_date(self, tmp_path):
        info = analyze_file(touch(tmp_path / 'waypoints.wpt'))
        assert info['has_error'] is True
        assert info['new_name'] is None
        assert 'Waypoints' in info['reason']

    def test_wrong_suffix_not_canonical(self, tmp_path):
        info = analyze_file(touch(tmp_path / 'random.wpt'))
        assert info['has_error'] is True


# ─────────────────────────────────────────────────────────────────
# 10. Прочие расширения — не проверяются
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Прочие расширения')
class TestOtherExtensions:
    @pytest.mark.parametrize('name', [
        'notes.txt',
        'track.mp3',
        'picture.jpg',
        'archive.zip',
        'no_extension_at_all',
        'data.json',
        'script.py',
    ])
    def test_not_validated(self, tmp_path, name):
        info = analyze_file(touch(tmp_path / name))
        assert info['has_error'] is False
        assert info['new_name'] is None


# ─────────────────────────────────────────────────────────────────
# 11. Не файл (директория, несуществующий путь)
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Не файлы')
class TestNonFiles:
    def test_directory_skipped(self, tmp_path):
        d = tmp_path / 'somedir'
        d.mkdir()
        info = analyze_file(d)
        assert info['has_error'] is False

    def test_missing_file_skipped(self, tmp_path):
        info = analyze_file(tmp_path / 'does_not_exist.gpx')
        assert info['has_error'] is False


# ─────────────────────────────────────────────────────────────────
# 12. sanitize_callsign — вспомогательная функция
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('sanitize_callsign')
class TestSanitizeCallsign:
    @pytest.mark.translit
    @pytest.mark.parametrize('inp,expected', [
        ('Ivanov',    'Ivanov'),
        ('ivan-1',    'ivan-1'),
        ('Ivan_1',    'Ivan_1'),
        ('Иванов',    'Ivanov'),
        ('Радио-1',   'Radio-1'),
        ('тест!@#',   'test'),
        ('Ёжик',      'Yozhik'),
        ('',          ''),
    ])
    def test_sanitize(self, inp, expected):
        assert sanitize_callsign(inp) == expected


# ─────────────────────────────────────────────────────────────────
# 13. validate_new_folder_name — имена локальных папок (Ctrl+N)
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Имя новой локальной папки')
class TestValidateNewFolderName:
    @pytest.mark.smoke
    @pytest.mark.parametrize('name', [
        '2026-09-23_Moscow',
        '10-Tracks',
        'Москва',
        'папка с пробелами',
        'a',
        '123',
        'точка.внутри',
    ])
    def test_valid(self, name):
        ok, reason = validate_new_folder_name(name)

        assert ok is True, f'{name}: {reason}'
        assert reason == ''

    @pytest.mark.smoke
    @pytest.mark.parametrize('name', ['', '   ', '\t\n'])
    def test_empty_rejected(self, name):
        ok, reason = validate_new_folder_name(name)

        assert ok is False
        assert reason == 'пустое имя'

    @pytest.mark.parametrize('name', ['.', '..', ' . '])
    def test_dot_names_rejected(self, name):
        ok, reason = validate_new_folder_name(name)

        assert ok is False
        assert reason == 'некорректное имя'

    @pytest.mark.parametrize('char', list('/\\:*?"<>|') + ['\x00'])
    def test_forbidden_characters_rejected(self, char):
        ok, reason = validate_new_folder_name(f'a{char}b')

        assert ok is False
        assert 'недопустимые символы' in reason
        # В сообщении перечислены все запрещённые символы.
        assert '/' in reason and '|' in reason

    @pytest.mark.parametrize('name', ['имя.', 'a.', 'Moscow.'])
    def test_trailing_dot_rejected(self, name):
        ok, reason = validate_new_folder_name(name)

        assert ok is False
        assert 'оканчиваться точкой' in reason

    def test_whitespace_is_trimmed(self):
        ok, _ = validate_new_folder_name('  2026-09-23_Moscow  ')

        assert ok is True

    def test_dot_inside_is_allowed(self):
        ok, _ = validate_new_folder_name('2026.09.23_Moscow')

        assert ok is True

    def test_message_lists_symbols(self):
        _, reason = validate_new_folder_name('a/b')

        for char in '\\/:*?"<>|':
            assert char in reason


# ─────────────────────────────────────────────────────────────────
# 14. is_m_gpx — исключение <1-4 цифры>m.gpx
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('is_m_gpx')
class TestIsMGpx:
    @pytest.mark.parametrize('name', [
        '1m.gpx', '12m.gpx', '100m.gpx', '9999m.gpx',
        '20260923_100m.gpx', 'track_100m.gpx',
        '100M.GPX', '100m.Gpx',
    ])
    def test_true(self, name):
        assert is_m_gpx(Path(name)) is True

    @pytest.mark.parametrize('name', [
        'm.gpx', '12345m.gpx', 'track.gpx', 'track_100ms.gpx',
        'track_m100.gpx', '100m.plt', '100m.wpt', '100m', '100',
    ])
    def test_false(self, name):
        assert is_m_gpx(Path(name)) is False

    def test_filename_inside_100m_folder(self):
        """Важен только суффикс имени файла, а не путь."""
        assert is_m_gpx(Path('/tmp/10-Tracks/100m.gpx')) is True

    def test_digit_immediately_before_m_matters(self):
        assert is_m_gpx(Path('1000m.gpx')) is True
        assert is_m_gpx(Path('10000m.gpx')) is False


# ─────────────────────────────────────────────────────────────────
# 15. human_size — размер для колонок дерева и FTP
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('human_size')
class TestHumanSize:
    @pytest.mark.smoke
    @pytest.mark.parametrize('size,expected', [
        (0, '0 Б'),
        (1, '1 Б'),
        (512, '512 Б'),
        (1023, '1023 Б'),
        (1024, '1.0 КБ'),
        (1536, '1.5 КБ'),
        (1024 ** 2, '1.0 МБ'),
        (int(1.5 * 1024 ** 2), '1.5 МБ'),
        (1024 ** 3, '1.0 ГБ'),
        (1024 ** 4, '1.0 ТБ'),
    ])
    def test_units(self, size, expected):
        assert human_size(size) == expected

    def test_bytes_are_integers(self):
        assert human_size(1000) == '1000 Б'

    def test_larger_units_have_one_decimal(self):
        assert human_size(1024 ** 5) == '1024.0 ТБ'

    def test_result_ends_with_unit(self):
        for size in (0, 1024, 1024 ** 3):
            assert human_size(size).split()[-1] in \
                ('Б', 'КБ', 'МБ', 'ГБ', 'ТБ')


# ─────────────────────────────────────────────────────────────────
# 16. Ветви причин в analyze_file
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Причины отказа')
class TestReasonBranches:
    def test_date_without_callsign(self, tmp_path):
        """Дата есть, а позывного в имени не осталось."""
        info = analyze_file(touch(tmp_path / '2026-09-17_.gpx'))

        assert info['has_error'] is True
        assert info['new_name'] is None
        assert info['confidence'] == 'low'
        assert 'позывной пуст' in info['reason']

    def test_content_date_without_callsign(self, tmp_path):
        """Дата взята из содержимого, но имя не даёт позывного."""
        info = analyze_file(make_gpx(tmp_path / '_.gpx'))

        assert info['has_error'] is True
        assert info['new_name'] is None
        assert info['confidence'] == 'low'
        assert 'из файла' in info['reason']
        assert '20260917' in info['reason']

    def test_content_date_with_placeholder_name(self, tmp_path):
        """Имя-заглушка + дата из содержимого файла."""
        info = analyze_file(make_gpx(tmp_path / 'безымянный.gpx'))

        assert info['has_error'] is True
        assert info['new_name'] == '20260917_Bezymyannyy.gpx'
        assert info['confidence'] == 'low'
        assert 'из содержимого файла' in info['reason']
        assert 'заглушк' in info['reason']

    def test_dirty_date_ignored(self, tmp_path):
        """Несуществующая дата в имени не принимается."""
        info = analyze_file(touch(tmp_path / '2026-13-45_бабушка.gpx'))

        assert info['has_error'] is True
        assert info['reason'] == 'дата не найдена ни в имени, ни в файле'

    def test_normalization_reason(self, tmp_path):
        info = analyze_file(touch(tmp_path / '2026-09-17_11-55_бабушка.gpx'))

        assert info['reason'] == 'дата + позывной из имени'
        assert info['confidence'] == 'medium'

    def test_high_confidence_reason(self, tmp_path):
        info = analyze_file(touch(tmp_path / '20260923_иванов.gpx'))

        assert info['reason'] == 'нормализация позывного'
        assert info['confidence'] == 'high'


# ─────────────────────────────────────────────────────────────────
# 17. Страховочные ветви normalize_callsign
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('normalize_callsign: страховки')
class TestNormalizeFallbacks:
    @pytest.mark.parametrize('inp,expected', [
        # база состоит только из разделителей — возврат как есть
        ('_-1', '_-1'),
        ('-_5', '-_5'),
        # база не из чистых букв и уже оканчивается цифрой
        ('1_2', '1_2'),
        # база не из чистых букв, номер добивается нулём
        ('ab1c_5', 'Ab1c05'),
        ('ab1c5', 'Ab1c5'),
    ])
    def test_fallback_paths(self, inp, expected):
        assert normalize_callsign(inp) == expected

    def test_idempotent_for_fallbacks(self):
        for x in ('_-1', '1_2', 'ab1_2', 'ab1c_5'):
            once = normalize_callsign(x)
            assert normalize_callsign(once) == once, x

    @pytest.mark.parametrize('inp', ['_', '-', '__', '---'])
    def test_only_separators(self, inp):
        assert normalize_callsign(inp) == inp

