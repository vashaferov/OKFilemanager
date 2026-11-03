"""Заметки к папкам: хранение, поиск и путь файла заметок."""

import json
from pathlib import Path

import pytest

from ok_helper.notes import NotesStore, notes_path_for


@pytest.fixture
def store(tmp_path):
    return NotesStore(tmp_path / 'notes.json')


def test_empty_store_has_no_notes(store):
    assert len(store) == 0
    assert store.get('/tmp/no-such-folder') == ''
    assert store.all_notes() == []


def test_set_and_get_round_trip(store, tmp_path):
    folder = tmp_path / '2026-09-23_Moscow'
    folder.mkdir()

    assert store.set(folder, 'Проверить трек') is True

    assert store.get(folder) == 'Проверить трек'
    assert len(store) == 1
    assert store.note(folder).path == folder.resolve()


def test_set_same_text_is_not_reported_as_change(store, tmp_path):
    folder = tmp_path / 'folder'
    folder.mkdir()
    store.set(folder, 'текст')

    assert store.set(folder, 'текст') is False


def test_empty_text_removes_note(store, tmp_path):
    folder = tmp_path / 'folder'
    folder.mkdir()
    store.set(folder, 'текст')

    assert store.set(folder, '   \n  ') is True
    assert store.get(folder) == ''
    assert len(store) == 0


def test_remove_reports_whether_note_existed(store, tmp_path):
    folder = tmp_path / 'folder'
    folder.mkdir()
    store.set(folder, 'текст')

    assert store.remove(folder) is True
    assert store.remove(folder) is False


def test_changes_survive_reload(tmp_path):
    path = tmp_path / 'notes.json'
    folder = tmp_path / 'folder'
    folder.mkdir()
    NotesStore(path).set(folder, 'строка 1\nстрока 2')

    again = NotesStore(path)

    assert again.get(folder) == 'строка 1\nстрока 2'
    assert again.note(folder).updated


def test_file_is_utf8_json_with_metadata(tmp_path):
    path = tmp_path / 'notes.json'
    folder = tmp_path / 'Папка'
    folder.mkdir()
    NotesStore(path).set(folder, 'Заметка')

    data = json.loads(path.read_text(encoding='utf-8'))

    assert data['version'] == 1
    entry = data['notes'][str(folder.resolve())]
    assert entry['text'] == 'Заметка'
    assert entry['updated']


def test_legacy_flat_file_is_read(tmp_path):
    path = tmp_path / 'notes.json'
    folder = tmp_path / 'folder'
    folder.mkdir()
    path.write_text(json.dumps({str(folder): 'старый формат'}),
                    encoding='utf-8')

    store = NotesStore(path)

    assert store.get(folder) == 'старый формат'


def test_broken_file_does_not_crash(tmp_path):
    path = tmp_path / 'notes.json'
    path.write_text('{не json', encoding='utf-8')

    store = NotesStore(path)

    assert len(store) == 0
    assert store.load_error


def test_search_by_text_and_path(store, tmp_path):
    first = tmp_path / '2026-09-23_Moscow'
    second = tmp_path / '2026-09-17_Tver'
    first.mkdir()
    second.mkdir()
    store.set(first, 'Проверить трек перед заливкой')
    store.set(second, 'Созвониться с координатором')

    assert [m.path for m in store.search('трек')] == [first.resolve()]
    assert [m.path for m in store.search('moscow')] == [first.resolve()]
    assert [m.path for m in store.search('2026')] == [
        second.resolve(), first.resolve()]
    assert store.search('нет такого') == []


def test_search_requires_all_words(store, tmp_path):
    folder = tmp_path / 'folder'
    folder.mkdir()
    store.set(folder, 'Проверить трек')

    assert len(store.search('проверить трек')) == 1
    assert store.search('проверить заливка') == []


def test_empty_query_returns_all_sorted(store, tmp_path):
    b = tmp_path / 'b'
    a = tmp_path / 'a'
    a.mkdir()
    b.mkdir()
    store.set(b, 'b')
    store.set(a, 'a')

    assert [m.path.name for m in store.search('')] == ['a', 'b']


def test_snippet_contains_match(store, tmp_path):
    folder = tmp_path / 'folder'
    folder.mkdir()
    store.set(folder, 'слово ' * 40 + 'ИСКОМОЕ' + ' хвост' * 40)

    match = store.search('искомое')[0]

    assert 'ИСКОМОЕ' in match.snippet


def test_notes_path_uses_config_path(tmp_path):
    cfg = {'_path': tmp_path / 'config.toml'}

    assert notes_path_for(cfg) == tmp_path / 'notes.json'


def test_notes_path_falls_back_to_default(tmp_path, monkeypatch):
    import ok_helper.notes as notes

    monkeypatch.setattr(notes, 'config_path',
                        lambda: tmp_path / 'app' / 'config.toml')

    assert notes_path_for({}) == tmp_path / 'app' / 'notes.json'
