"""Тесты распаковки KMZ в KML (ok_helper.kmz).

Функциональность: kmz_to_kml() — делает копию .kmz с расширением
.zip, извлекает из архива .kml под именем исходного файла и удаляет
промежуточный .zip (остаются только .kmz и .kml).

Группировка в Allure:
  epic    = ok-filemanager
  feature = Распаковка KMZ в KML
  story   = задаётся маркером @pytest.mark.story на каждом классе.
"""

import zipfile
from pathlib import Path

import allure
import pytest

from ok_helper import kmz
from ok_helper.kmz import KmzError, kmz_to_kml


pytestmark = [
    pytest.mark.kmz,
    pytest.mark.epic('ok-filemanager'),
    pytest.mark.feature('Распаковка KMZ в KML'),
]


def make_kmz(path: Path, entries) -> Path:
    """Создаёт .kmz (обычный zip) с указанными файлами."""
    with zipfile.ZipFile(path, 'w') as zf:
        for name, data in entries:
            if isinstance(data, str):
                data = data.encode('utf-8')
            zf.writestr(name, data)
    return path


# ─────────────────────────────────────────────────────────────────
# 1. Успешная распаковка
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Успешная распаковка')
class TestSuccess:
    @pytest.mark.smoke
    def test_creates_kml_next_to_kmz(self, tmp_path):
        src = make_kmz(tmp_path / 'track.kmz',
                       [('doc.kml', '<kml>данные</kml>')])

        result = kmz_to_kml(src)

        assert result == tmp_path / 'track.kml'
        assert result.read_text(encoding='utf-8') == '<kml>данные</kml>'

    @pytest.mark.smoke
    def test_removes_intermediate_zip(self, tmp_path):
        src = make_kmz(tmp_path / 'track.kmz', [('doc.kml', 'x')])

        kmz_to_kml(src)

        assert not (tmp_path / 'track.zip').exists()
        assert sorted(p.name for p in tmp_path.iterdir()) == \
            ['track.kml', 'track.kmz']

    def test_keeps_original_kmz(self, tmp_path):
        src = make_kmz(tmp_path / 'track.kmz', [('doc.kml', 'x')])
        before = src.read_bytes()

        kmz_to_kml(src)

        assert src.read_bytes() == before

    def test_name_follows_kmz_base(self, tmp_path):
        src = make_kmz(tmp_path / 'Мой трек (Копия).kmz',
                       [('doc.kml', 'x')])

        result = kmz_to_kml(src)

        assert result.name == 'Мой трек (Копия).kml'

    def test_content_is_read_from_archive(self, tmp_path):
        src = make_kmz(tmp_path / 'route.kmz',
                       [('files/inner.kml', '<kml>внутри</kml>')])

        result = kmz_to_kml(src)

        assert result.read_text(encoding='utf-8') == '<kml>внутри</kml>'

    def test_overwrites_existing_kml(self, tmp_path):
        (tmp_path / 'track.kml').write_text('старое', encoding='utf-8')
        src = make_kmz(tmp_path / 'track.kmz', [('doc.kml', 'новое')])

        kmz_to_kml(src)

        assert (tmp_path / 'track.kml').read_text(
            encoding='utf-8') == 'новое'


# ─────────────────────────────────────────────────────────────────
# 2. Выбор .kml внутри архива
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Выбор .kml')
class TestEntrySelection:
    def test_prefers_doc_kml(self, tmp_path):
        src = make_kmz(tmp_path / 'track.kmz', [
            ('other.kml', 'другой'),
            ('doc.kml', 'главный'),
        ])

        result = kmz_to_kml(src)

        assert result.read_text(encoding='utf-8') == 'главный'

    def test_falls_back_to_first_kml(self, tmp_path):
        src = make_kmz(tmp_path / 'track.kmz', [
            ('note.txt', 'текст'),
            ('first.kml', 'первый'),
            ('second.kml', 'второй'),
        ])

        result = kmz_to_kml(src)

        assert result.read_text(encoding='utf-8') == 'первый'

    def test_ignores_uppercase_extension(self, tmp_path):
        src = make_kmz(tmp_path / 'track.kmz', [('DOC.KML', 'верхний')])

        result = kmz_to_kml(src)

        assert result.read_text(encoding='utf-8') == 'верхний'


# ─────────────────────────────────────────────────────────────────
# 3. Ошибки
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Ошибки')
class TestErrors:
    def test_not_kmz_raises(self, tmp_path):
        other = tmp_path / 'track.gpx'
        other.write_text('x', encoding='utf-8')

        with pytest.raises(KmzError):
            kmz_to_kml(other)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(KmzError):
            kmz_to_kml(tmp_path / 'нет.kmz')

    def test_bad_zip_raises(self, tmp_path):
        src = tmp_path / 'track.kmz'
        src.write_text('это не архив', encoding='utf-8')

        with pytest.raises(KmzError):
            kmz_to_kml(src)

    def test_no_kml_raises(self, tmp_path):
        src = make_kmz(tmp_path / 'track.kmz', [('readme.txt', 'нет kml')])

        with pytest.raises(KmzError):
            kmz_to_kml(src)

    def test_zip_removed_even_on_error(self, tmp_path):
        src = make_kmz(tmp_path / 'track.kmz', [('readme.txt', 'нет kml')])

        with pytest.raises(KmzError):
            kmz_to_kml(src)

        assert not (tmp_path / 'track.zip').exists()
        assert not (tmp_path / 'track.kml').exists()
        assert sorted(p.name for p in tmp_path.iterdir()) == ['track.kmz']


# ─────────────────────────────────────────────────────────────────
# 4. Вспомогательный выбор записи
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('_select_kml_entry')
class TestSelectEntry:
    def test_doc_kml_wins(self):
        assert kmz._select_kml_entry(['a.kml', 'doc.kml', 'b.kml']) == \
            'doc.kml'

    def test_doc_kml_in_subfolder(self):
        assert kmz._select_kml_entry(
            ['data/doc.kml', 'data/other.kml']) == 'data/doc.kml'

    def test_first_without_doc(self):
        assert kmz._select_kml_entry(['a.kml', 'b.kml']) == 'a.kml'

    def test_no_kml_raises(self):
        with pytest.raises(KmzError):
            kmz._select_kml_entry(['a.txt', 'b.kmz'])
