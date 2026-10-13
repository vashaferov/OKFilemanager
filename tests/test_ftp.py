"""Тесты FTP-специфичной валидации (ok_helper.ftp)."""

import allure
import pytest
from pathlib import Path

from ok_helper.ftp import (
    ALLOWED_UPLOAD_EXTENSIONS,
    find_target_folder,
    is_in_10_tracks,
    is_uploadable,
    upload_extensions_hint,
    upload_reason_for,
    validate_folder_name,
    validate_upload_target,
)


pytestmark = [
    pytest.mark.ftp,
    pytest.mark.epic('ok-filemanager'),
    pytest.mark.feature('FTP'),
]


def touch(p: Path) -> Path:
    p.write_text('')
    return p


# ─────────────────────────────────────────────────────────────────
# 1. validate_folder_name
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Валидация имён папок')
class TestFolderName:
    @pytest.mark.folder
    @pytest.mark.smoke
    @pytest.mark.parametrize('name', [
        '2026-09-23_Moscow',
        '2026-09-23_moscow',
        '2026-09-23_Moscow-1',
        '2026-09-23_ivan_1',
        '2026-09-23_a',
        '2026-09-23_123',
        '2026-12-31_NewYear',
        '2026-01-01_Start',
    ])
    def test_valid(self, name):
        ok, reason = validate_folder_name(name)
        assert ok is True, f'{name}: {reason}'

    @pytest.mark.folder
    @pytest.mark.parametrize('name,reason_part', [
        ('',                  'пустое'),
        ('   ',               'пустое'),
        ('2026-09-23_Москва', 'формат'),
        ('2026-09-23_',       'формат'),
        ('2026-09-23',        'формат'),
        ('23-09-2026_Moscow', 'формат'),
        ('2026/09/23_Moscow', 'формат'),
        ('2026-09-23 Moscow', 'формат'),
        ('2026-09-23_Moscow/', 'формат'),
        ('Moscow_2026-09-23', 'формат'),
        ('2026-13-01_Moscow', 'дата'),
        ('2026-00-15_Moscow', 'дата'),
        ('2026-09-32_Moscow', 'дата'),
        ('2026-09-00_Moscow', 'дата'),
    ])
    def test_invalid(self, name, reason_part):
        ok, reason = validate_folder_name(name)
        assert ok is False
        assert reason_part in reason

    @pytest.mark.folder
    def test_whitespace_trimmed(self):
        ok, _ = validate_folder_name('  2026-09-23_Moscow  ')
        assert ok is True


# ─────────────────────────────────────────────────────────────────
# 2. validate_upload_target — запрет заливки в корень
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Запрет заливки в корень')
class TestValidateUploadTarget:
    @pytest.mark.upload
    @pytest.mark.smoke
    @pytest.mark.parametrize('folder', [
        '2026-09-23_Moscow',
        '2026-09-23_moscow',
        '2026-09-23_Moscow-1',
    ])
    def test_normal_folder_ok(self, folder):
        ok, reason = validate_upload_target('/', folder)
        assert ok is True, reason

    @pytest.mark.upload
    @pytest.mark.smoke
    @pytest.mark.parametrize('folder', [
        '', '   ', '/', '\\', '.', '..',
    ])
    def test_empty_or_root_rejected(self, folder):
        ok, reason = validate_upload_target('/', folder)
        assert ok is False
        assert reason

    @pytest.mark.upload
    @pytest.mark.parametrize('folder', [
        '/abs/path',
        '\\abs\\path',
    ])
    def test_absolute_path_rejected(self, folder):
        ok, reason = validate_upload_target('/', folder)
        assert ok is False

    @pytest.mark.upload
    @pytest.mark.parametrize('folder', [
        '../parent',
        'sub/../../escape',
        '..\\windows',
    ])
    def test_parent_traversal_rejected(self, folder):
        ok, reason = validate_upload_target('/', folder)
        assert ok is False

    @pytest.mark.upload
    def test_with_nonempty_base_ok(self):
        ok, _ = validate_upload_target('/upload', '2026-09-23_Moscow')
        assert ok is True

    @pytest.mark.upload
    def test_with_empty_base_ok(self):
        ok, _ = validate_upload_target('', '2026-09-23_Moscow')
        assert ok is True


# ─────────────────────────────────────────────────────────────────
# 3. find_target_folder
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Поиск целевой папки')
class TestFindTargetFolder:
    @pytest.mark.path_walk
    def test_direct(self, tmp_path):
        folder = tmp_path / '2026-09-23_Moscow'
        folder.mkdir()
        name, found = find_target_folder(folder)
        assert found is True
        assert name == '2026-09-23_Moscow'

    @pytest.mark.path_walk
    @pytest.mark.smoke
    def test_walks_up(self, tmp_path):
        folder = tmp_path / '2026-09-23_Moscow'
        folder.mkdir()
        sub = folder / '10-Tracks'
        sub.mkdir()
        name, found = find_target_folder(sub)
        assert found is True
        assert name == '2026-09-23_Moscow'

    @pytest.mark.path_walk
    def test_deep_walk_up(self, tmp_path):
        folder = tmp_path / '2026-09-23_Moscow'
        folder.mkdir()
        deep = folder / 'a' / 'b' / 'c'
        deep.mkdir(parents=True)
        name, found = find_target_folder(deep)
        assert found is True
        assert name == '2026-09-23_Moscow'

    @pytest.mark.path_walk
    def test_not_found(self, tmp_path):
        sub = tmp_path / 'foo' / 'bar'
        sub.mkdir(parents=True)
        name, found = find_target_folder(sub)
        assert found is False
        assert name == 'bar'

    @pytest.mark.path_walk
    def test_respects_max_levels(self, tmp_path):
        deep = tmp_path
        for i in range(10):
            deep = deep / f'lvl{i}'
        deep.mkdir(parents=True)
        name, found = find_target_folder(deep, max_levels=3)
        assert found is False


# ─────────────────────────────────────────────────────────────────
# 4. is_in_10_tracks
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Запрет отправки из 10-Tracks')
class TestIsInTenTracks:
    @pytest.mark.upload
    @pytest.mark.smoke
    def test_direct_parent(self, tmp_path):
        d = tmp_path / '10-Tracks'
        d.mkdir()
        p = d / 'foo.gpx'
        assert is_in_10_tracks(p) is True

    @pytest.mark.upload
    def test_case_insensitive(self, tmp_path):
        d = tmp_path / '10-tracks'
        d.mkdir()
        p = d / 'foo.gpx'
        assert is_in_10_tracks(p) is True

    @pytest.mark.upload
    def test_uppercase(self, tmp_path):
        d = tmp_path / '10-TRACKS'
        d.mkdir()
        p = d / 'foo.gpx'
        assert is_in_10_tracks(p) is True

    @pytest.mark.upload
    @pytest.mark.smoke
    def test_nested_subfolder(self, tmp_path):
        d = tmp_path / '10-Tracks' / 'subfolder'
        d.mkdir(parents=True)
        p = d / 'foo.gpx'
        assert is_in_10_tracks(p) is True

    @pytest.mark.upload
    def test_deep_structure(self, tmp_path):
        folder = tmp_path / '2026-09-23_Moscow'
        folder.mkdir()
        sub = folder / '10-Tracks'
        sub.mkdir()
        p = sub / 'foo.gpx'
        assert is_in_10_tracks(p) is True

    @pytest.mark.upload
    @pytest.mark.smoke
    def test_regular_folder_not_ten_tracks(self, tmp_path):
        folder = tmp_path / '2026-09-23_Moscow'
        folder.mkdir()
        p = folder / 'foo.gpx'
        assert is_in_10_tracks(p) is False

    @pytest.mark.upload
    def test_similar_name_not_matched(self, tmp_path):
        for name in ('10-Track', '10_Tracks', 'Tracks-10', 'tracks'):
            d = tmp_path / name
            d.mkdir()
            p = d / 'foo.gpx'
            assert is_in_10_tracks(p) is False, name


# ─────────────────────────────────────────────────────────────────
# 5. is_uploadable
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Политика отправки файлов')
class TestIsUploadable:
    @pytest.mark.upload
    def test_allowed_extensions_constant(self):
        assert ALLOWED_UPLOAD_EXTENSIONS == {'.gpx', '.plt', '.wpt'}

    @pytest.mark.upload
    @pytest.mark.parametrize('ext', [
        '.txt', '.mp3', '.jpg', '.zip', '.kml', '.csv', '.pdf',
    ])
    def test_rejects_other_extensions(self, tmp_path, ext):
        p = tmp_path / f'20260923_Ivanov{ext}'
        assert is_uploadable(p, {'has_error': False}) is False

    @pytest.mark.upload
    def test_no_extension(self, tmp_path):
        p = tmp_path / '20260923_Ivanov'
        assert is_uploadable(p, {'has_error': False}) is False

    @pytest.mark.upload
    @pytest.mark.smoke
    @pytest.mark.parametrize('name', [
        '1m.gpx',
        '12m.gpx',
        '100m.gpx',
        '999m.gpx',
        '9999m.gpx',
        '20260917_100m.gpx',
        '20260917_9999m.gpx',
        'track_5M.gpx',
        '_100m.gpx',
    ])
    def test_gpx_with_m_suffix(self, tmp_path, name):
        p = touch(tmp_path / name)
        assert is_uploadable(p) is True

    @pytest.mark.upload
    @pytest.mark.smoke
    @pytest.mark.parametrize('name', [
        '20260917_lisa_1.gpx',
        '20260917_Ivanov.gpx',
        '20260923_A1B2.gpx',
        '2026-09-17_11-55_бабушка.gpx',
        'track.gpx',
        'm.gpx',
        '12345m.gpx',
        'track_100ms.gpx',
        'track_m100.gpx',
        '2026-09-23 Безымянный трек.gpx',
    ])
    def test_gpx_without_m_suffix_rejected(self, tmp_path, name):
        p = touch(tmp_path / name)
        assert is_uploadable(p) is False

    @pytest.mark.upload
    def test_gpx_case_insensitive_m(self, tmp_path):
        p = touch(tmp_path / '100M.GPX')
        assert is_uploadable(p) is True

    @pytest.mark.upload
    def test_plt_valid(self, tmp_path):
        p = touch(tmp_path / '20260923_Ivanov.plt')
        assert is_uploadable(p) is True

    @pytest.mark.upload
    def test_plt_needs_normalization(self, tmp_path):
        """Позывной со строчной буквы — сначала нужно поправить имя."""
        p = touch(tmp_path / '20260923_ivanov.plt')
        assert is_uploadable(p) is False

    @pytest.mark.upload
    def test_plt_invalid_name(self, tmp_path):
        p = touch(tmp_path / 'track.plt')
        assert is_uploadable(p) is False

    @pytest.mark.upload
    def test_wpt_valid(self, tmp_path):
        p = touch(tmp_path / 'Waypoints_20260923.wpt')
        assert is_uploadable(p) is True

    @pytest.mark.upload
    def test_wpt_wrong_name(self, tmp_path):
        p = touch(tmp_path / 'random.wpt')
        assert is_uploadable(p) is False

    @pytest.mark.upload
    def test_uses_precomputed_info(self, tmp_path):
        p = touch(tmp_path / '20260923_Ivanov.plt')
        assert is_uploadable(p, {'has_error': True}) is False

    @pytest.mark.upload
    @pytest.mark.smoke
    def test_ten_tracks_blocks_valid_gpx(self, tmp_path):
        d = tmp_path / '10-Tracks'
        d.mkdir()
        p = touch(d / '100m.gpx')
        assert is_uploadable(p) is False

    @pytest.mark.upload
    def test_ten_tracks_blocks_plt(self, tmp_path):
        d = tmp_path / '10-Tracks'
        d.mkdir()
        p = touch(d / '20260923_Ivanov.plt')
        assert is_uploadable(p) is False

    @pytest.mark.upload
    def test_ten_tracks_blocks_wpt(self, tmp_path):
        d = tmp_path / '10-Tracks'
        d.mkdir()
        p = touch(d / 'Waypoints_20260923.wpt')
        assert is_uploadable(p) is False

    @pytest.mark.upload
    def test_ten_tracks_nested_subfolder(self, tmp_path):
        d = tmp_path / '10-Tracks' / 'sub'
        d.mkdir(parents=True)
        p = touch(d / '100m.gpx')
        assert is_uploadable(p) is False


# ─────────────────────────────────────────────────────────────────
# 6. upload_extensions_hint и upload_reason_for
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Подсказки и причины отказа')
class TestUploadHints:
    @pytest.mark.upload
    def test_extensions_hint_mentions_all(self):
        hint = upload_extensions_hint()
        assert '.gpx' in hint
        assert '.plt' in hint
        assert '.wpt' in hint
        assert 'цифр' in hint or 'm.gpx' in hint

    @pytest.mark.upload
    def test_reason_gpx_non_m(self, tmp_path):
        p = tmp_path / '20260917_lisa_1.gpx'
        reason = upload_reason_for(p)
        assert 'цифр' in reason or 'm.gpx' in reason

    @pytest.mark.upload
    def test_reason_other_extension(self, tmp_path):
        p = tmp_path / 'notes.txt'
        reason = upload_reason_for(p)
        assert 'расширение' in reason

    @pytest.mark.upload
    @pytest.mark.smoke
    def test_reason_ten_tracks(self, tmp_path):
        d = tmp_path / '10-Tracks'
        d.mkdir()
        p = d / '100m.gpx'
        reason = upload_reason_for(p)
        assert '10-Tracks' in reason


# ─────────────────────────────────────────────────────────────────
# 7. Сквозные сценарии
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Сквозные сценарии')
class TestUploadMatrix:
    @pytest.mark.upload
    @pytest.mark.smoke
    @pytest.mark.parametrize('name,expected', [
        ('1m.gpx',                      True),
        ('100m.gpx',                    True),
        ('20260917_9999m.gpx',          True),
        ('20260917_lisa_1.gpx',         False),
        ('20260917_Ivanov.gpx',         False),  # .gpx — только *m.gpx
        ('track.gpx',                   False),
        ('12345m.gpx',                  False),
        ('20260923_Ivanov.plt',         True),
        ('20260923_ivanov.plt',         False),  # нужен F5/F7
        ('20260923_Иванов.plt',         False),  # нужен F5/F7
        ('Waypoints_20260923.wpt',      True),
        ('random.wpt',                  False),
        ('notes.txt',                   False),
        ('20260923_ivanov.mp3',         False),
    ])
    def test_matrix(self, tmp_path, name, expected):
        p = touch(tmp_path / name)
        assert is_uploadable(p) is expected

    @pytest.mark.upload
    @pytest.mark.parametrize('name,expected', [
        ('100m.gpx',                    False),
        ('20260923_Ivanov.plt',         False),
        ('Waypoints_20260923.wpt',      False),
    ])
    def test_matrix_inside_ten_tracks(self, tmp_path, name, expected):
        d = tmp_path / '10-Tracks'
        d.mkdir()
        p = touch(d / name)
        assert is_uploadable(p) is expected
