"""Тесты моделей дерева файлов и FTP-листинга (ok_helper.gui).

Функциональность: FileTreeModel (ленивая подгрузка, цветные пометки,
«✓ на FTP», «можно на FTP») и FtpListModel.

Требуется QApplication в offscreen-режиме (фикстура qapp).

Группировка в Allure:
  epic    = ok-filemanager
  feature = Модели данных GUI
  story   = задаётся маркером @pytest.mark.story на каждом классе.
"""

from pathlib import Path

import allure
import pytest

from ok_helper import gui
from ok_helper.gui import (
    ROLE_FTP_NAME,
    ROLE_PATH,
    STUB_TEXT,
    FileTreeModel,
    FtpListModel,
)


pytestmark = [
    pytest.mark.gui,
    pytest.mark.epic('ok-filemanager'),
    pytest.mark.feature('Модели данных GUI'),
]


@pytest.fixture
def tree(tmp_path):
    """Дерево: две папки (одна пустая), проблемный .gpx, notes.txt."""
    root = tmp_path / 'root'
    root.mkdir()
    moscow = root / '2026-09-23_Moscow'
    moscow.mkdir()
    (moscow / '100m.gpx').write_text('')
    (moscow / 'nested').mkdir()
    (moscow / 'nested' / 'deep.gpx').write_text('')
    (root / 'empty').mkdir()
    (root / '2026-09-17_11-55_бабушка.gpx').write_text('x' * 2048)
    (root / 'notes.txt').write_text('')
    return root


def child_names(item):
    return [item.child(row).text() for row in range(item.rowCount())]


# ─────────────────────────────────────────────────────────────────
# 1. FileTreeModel — построение дерева
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Дерево файлов')
class TestFileTreeModel:
    def test_headers(self, qapp):
        model = FileTreeModel()

        assert [model.headerData(i, gui.Qt.Horizontal)
                for i in range(3)] == ['Имя', 'Размер', 'Статус']

    def test_refresh_without_root_is_noop(self, qapp):
        model = FileTreeModel()
        model.refresh()

        assert model.rowCount() == 0

    @pytest.mark.smoke
    def test_set_root_creates_single_root_row(self, qapp, tree):
        model = FileTreeModel()
        model.set_root(tree)

        assert model.rowCount() == 1
        root_item = model.item(0)
        assert root_item.text() == 'root'
        assert Path(root_item.data(ROLE_PATH)) == tree.resolve()
        assert root_item.data(ROLE_PATH) is not None

    def test_root_row_is_not_editable(self, qapp, tree):
        model = FileTreeModel()
        model.set_root(tree)

        assert model.item(0).isEditable() is False

    def test_root_expanded_paths_seeded(self, qapp, tree):
        model = FileTreeModel()
        model.set_root(tree)

        assert model.expanded_paths == {tree.resolve()}

    def test_status_column_shows_absolute_path(self, qapp, tree):
        model = FileTreeModel()
        model.set_root(tree)

        assert model.item(0, FileTreeModel.COL_STATUS).text() == \
            str(tree.resolve())

    def test_size_column_of_root_is_empty(self, qapp, tree):
        model = FileTreeModel()
        model.set_root(tree)

        assert model.item(0, FileTreeModel.COL_SIZE).text() == ''

    @pytest.mark.smoke
    def test_directories_before_files_alphabetically(self, qapp, tree):
        model = FileTreeModel()
        model.set_root(tree)

        assert child_names(model.item(0)) == [
            '2026-09-23_Moscow',
            'empty',
            '2026-09-17_11-55_бабушка.gpx',
            'notes.txt',
        ]

    def test_directory_row_status_and_colour(self, qapp, tree):
        model = FileTreeModel()
        model.set_theme('light')
        model.set_root(tree)

        moscow = model.item(0).child(0)
        assert moscow.child(0).text() == STUB_TEXT
        assert model.item(0).child(0, FileTreeModel.COL_STATUS).text() == \
            'папка'
        assert moscow.foreground().color().name() == \
            gui.THEMES['light']['tree_dir']

    def test_directory_row_is_bold(self, qapp, tree):
        model = FileTreeModel()
        model.set_root(tree)

        assert model.item(0).child(0).font().bold() is True

    def test_empty_directory_has_no_stub(self, qapp, tree):
        model = FileTreeModel()
        model.set_root(tree)

        empty = model.item(0).child(1)
        assert empty.text() == 'empty'
        assert empty.rowCount() == 0

    def test_nonempty_directory_has_lazy_stub(self, qapp, tree):
        model = FileTreeModel()
        model.set_root(tree)

        moscow = model.item(0).child(0)
        assert moscow.rowCount() == 1
        assert moscow.child(0).text() == STUB_TEXT

    def test_stub_is_not_editable(self, qapp, tree):
        model = FileTreeModel()
        model.set_root(tree)

        assert model.item(0).child(0).child(0).isEditable() is False


# ─────────────────────────────────────────────────────────────────
# 2. FileTreeModel — колонка «Статус»
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Статусы в дереве')
class TestFileTreeModelStatus:
    @pytest.mark.smoke
    def test_error_file_shows_suggested_name(self, qapp, tree):
        """README: ошибки — красные, с предлагаемой правкой."""
        model = FileTreeModel()
        model.set_theme('light')
        model.set_root(tree)

        item = model.item(0).child(2)
        assert item.text() == '2026-09-17_11-55_бабушка.gpx'
        assert item.data(ROLE_PATH) == str(tree / item.text())
        assert model.item(0).child(2, FileTreeModel.COL_STATUS).text() == \
            'правка → 20260917_Babushka.gpx'
        assert item.foreground().color().name() == \
            gui.THEMES['light']['tree_err']

    def test_error_file_is_bold(self, qapp, tree):
        model = FileTreeModel()
        model.set_root(tree)

        assert model.item(0).child(2).font().bold() is True

    def test_size_uses_human_size(self, qapp, tree):
        model = FileTreeModel()
        model.set_root(tree)

        assert model.item(0).child(2, FileTreeModel.COL_SIZE).text() == \
            '2.0 КБ'

    def test_size_is_right_aligned(self, qapp, tree):
        model = FileTreeModel()
        model.set_root(tree)

        alignment = model.item(0).child(2, FileTreeModel.COL_SIZE) \
            .textAlignment()
        assert alignment & gui.Qt.AlignRight

    def test_uploadable_file_gets_mark(self, qapp, tree):
        model = FileTreeModel()
        model.set_root(tree)
        item = model.find_item(tree / '2026-09-23_Moscow' / '100m.gpx')

        assert item.text() == '100m.gpx'
        assert item.parent().child(item.row(),
                                   FileTreeModel.COL_STATUS).text() == \
            'можно на FTP'

    def test_untracked_extension_has_empty_status(self, qapp, tree):
        model = FileTreeModel()
        model.set_root(tree)

        notes = model.item(0).child(3)
        assert notes.text() == 'notes.txt'
        assert model.item(0).child(3, FileTreeModel.COL_STATUS).text() == ''

    def test_untracked_extension_uses_default_colour(self, qapp, tree):
        model = FileTreeModel()
        model.set_theme('light')
        model.set_root(tree)

        assert model.item(0).child(3).foreground().color().name() == \
            gui.THEMES['light']['tree_file']


# ─────────────────────────────────────────────────────────────────
# 3. FileTreeModel — ленивая подгрузка
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Ленивая подгрузка')
class TestLazyLoading:
    @pytest.mark.smoke
    def test_load_children_replaces_stub(self, qapp, tree):
        model = FileTreeModel()
        model.set_root(tree)

        moscow = model.item(0).child(0)
        model.load_children_if_stub(moscow)

        assert child_names(moscow) == ['nested', '100m.gpx']
        assert STUB_TEXT not in child_names(moscow)

    def test_load_children_is_idempotent(self, qapp, tree):
        model = FileTreeModel()
        model.set_root(tree)

        moscow = model.item(0).child(0)
        model.load_children_if_stub(moscow)
        model.load_children_if_stub(moscow)

        assert moscow.rowCount() == 2

    def test_load_children_on_loaded_dir_is_noop(self, qapp, tree):
        """Папка без заглушки (пустая) ничего не догружает."""
        model = FileTreeModel()
        model.set_root(tree)

        empty = model.item(0).child(1)
        model.load_children_if_stub(empty)

        assert empty.rowCount() == 0

    def test_load_children_on_file_is_noop(self, qapp, tree):
        model = FileTreeModel()
        model.set_root(tree)

        notes = model.item(0).child(3)
        model.load_children_if_stub(notes)

        assert notes.rowCount() == 0

    def test_nested_dirs_keep_lazy_stubs(self, qapp, tree):
        """После загрузки папки вложенная папка тоже получает заглушку."""
        model = FileTreeModel()
        model.set_root(tree)

        model.load_children_if_stub(model.item(0).child(0))
        nested = model.item(0).child(0).child(0)

        assert nested.text() == 'nested'
        assert nested.rowCount() == 1
        assert nested.child(0).text() == STUB_TEXT

    def test_find_item_loads_stubs_on_the_way(self, qapp, tree):
        model = FileTreeModel()
        model.set_root(tree)

        item = model.find_item(
            tree / '2026-09-23_Moscow' / 'nested' / 'deep.gpx')

        assert item is not None
        assert item.text() == 'deep.gpx'

    def test_iterdir_error_leaves_no_children(self, qapp, tmp_path,
                                              monkeypatch):
        root = tmp_path / 'x'
        root.mkdir()
        model = FileTreeModel()

        def boom(self, *a, **kw):
            raise OSError('нет доступа')

        monkeypatch.setattr(Path, 'iterdir', boom)
        model.set_root(root)

        assert model.rowCount() == 1
        assert model.item(0).rowCount() == 0


# ─────────────────────────────────────────────────────────────────
# 4. FileTreeModel — find_item
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Поиск элемента')
class TestFindItem:
    def test_root_returns_first_item(self, qapp, tree):
        model = FileTreeModel()
        model.set_root(tree)

        assert model.find_item(tree) is model.item(0)

    def test_direct_child(self, qapp, tree):
        model = FileTreeModel()
        model.set_root(tree)

        item = model.find_item(tree / 'notes.txt')
        assert item is not None
        assert item.text() == 'notes.txt'

    def test_nested_file(self, qapp, tree):
        model = FileTreeModel()
        model.set_root(tree)

        assert model.find_item(tree / '2026-09-23_Moscow' / '100m.gpx') \
            is not None

    def test_nested_directory(self, qapp, tree):
        model = FileTreeModel()
        model.set_root(tree)

        item = model.find_item(tree / '2026-09-23_Moscow' / 'nested')
        assert item is not None
        assert item.text() == 'nested'

    def test_missing_child_returns_none(self, qapp, tree):
        model = FileTreeModel()
        model.set_root(tree)

        assert model.find_item(tree / 'нет_такого.gpx') is None

    def test_path_outside_root_returns_none(self, qapp, tree, tmp_path):
        model = FileTreeModel()
        model.set_root(tree)

        assert model.find_item(tmp_path / 'снаружи.gpx') is None

    def test_without_root_returns_none(self, qapp, tree):
        model = FileTreeModel()

        assert model.find_item(tree / 'notes.txt') is None

    def test_accepts_relative_like_path(self, qapp, tree):
        """Путь нормализуется через resolve()."""
        model = FileTreeModel()
        model.set_root(tree)

        weird = tree / '.' / 'notes.txt'
        assert model.find_item(weird) is not None


# ─────────────────────────────────────────────────────────────────
# 5. FileTreeModel — пометка «✓ на FTP»
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Пометка «на FTP»')
class TestFtpSyncMark:
    @pytest.mark.smoke
    def test_matching_file_is_marked(self, qapp, tree):
        model = FileTreeModel()
        model.set_theme('light')
        model.set_root(tree)
        model.set_ftp_sync('2026-09-23_Moscow', {'100m.gpx'})
        item = model.find_item(tree / '2026-09-23_Moscow' / '100m.gpx')

        status = item.parent().child(item.row(), FileTreeModel.COL_STATUS)
        assert status.text() == '✓ на FTP; можно на FTP'
        assert item.foreground().color().name() == \
            gui.THEMES['light']['tree_up']

    def test_file_in_other_folder_not_marked(self, qapp, tree):
        model = FileTreeModel()
        model.set_root(tree)
        model.set_ftp_sync('2026-09-23_Moscow',
                           {'2026-09-17_11-55_бабушка.gpx'})

        assert '✓' not in model.item(0).child(
            2, FileTreeModel.COL_STATUS).text()

    def test_unknown_name_not_marked(self, qapp, tree):
        model = FileTreeModel()
        model.set_root(tree)
        model.set_ftp_sync('2026-09-23_Moscow', {'other.gpx'})
        item = model.find_item(tree / '2026-09-23_Moscow' / '100m.gpx')

        assert '✓' not in item.parent().child(
            item.row(), FileTreeModel.COL_STATUS).text()

    def test_nested_ftp_folder_disables_marking(self, qapp, tree):
        """Известное ограничение: путь с '/' пометки не ставит."""
        model = FileTreeModel()
        model.set_root(tree)
        model.set_ftp_sync('upload/2026-09-23_Moscow', {'100m.gpx'})
        item = model.find_item(tree / '2026-09-23_Moscow' / '100m.gpx')

        assert '✓' not in item.parent().child(
            item.row(), FileTreeModel.COL_STATUS).text()

    def test_clear_ftp_sync_removes_marks(self, qapp, tree):
        model = FileTreeModel()
        model.set_root(tree)
        model.set_ftp_sync('2026-09-23_Moscow', {'100m.gpx'})
        model.clear_ftp_sync()
        item = model.find_item(tree / '2026-09-23_Moscow' / '100m.gpx')

        assert model.ftp_sync_folder == ''
        assert model.ftp_sync_names == set()
        assert '✓' not in item.parent().child(
            item.row(), FileTreeModel.COL_STATUS).text()

    def test_is_uploaded_predicate(self, qapp, tree):
        model = FileTreeModel()

        assert model._is_uploaded(tree / 'x' / '100m.gpx') is False

        model.set_ftp_sync('x', {'100m.gpx'})
        assert model._is_uploaded(tree / 'x' / '100m.gpx') is True
        assert model._is_uploaded(tree / 'y' / '100m.gpx') is False

    def test_set_ftp_sync_copies_names(self, qapp):
        model = FileTreeModel()
        names = {'a.gpx'}
        model.set_ftp_sync('d', names)
        names.add('b.gpx')

        assert model.ftp_sync_names == {'a.gpx'}


# ─────────────────────────────────────────────────────────────────
# 6. FileTreeModel — смена темы
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Смена темы дерева')
class TestTreeTheme:
    @pytest.mark.parametrize('theme', ['light', 'dark'])
    def test_directory_colour_follows_theme(self, qapp, tree, theme):
        model = FileTreeModel()
        model.set_theme(theme)
        model.set_root(tree)

        assert model.item(0).child(0).foreground().color().name() == \
            gui.THEMES[theme]['tree_dir']

    def test_default_theme_is_light(self, qapp):
        assert FileTreeModel()._theme_name == 'light'

    def test_set_theme_refreshes(self, qapp, tree):
        model = FileTreeModel()
        model.set_root(tree)
        model.set_theme('dark')

        assert model.rowCount() == 1
        assert model.item(0).child(0).foreground().color().name() == \
            gui.THEMES['dark']['tree_dir']


# ─────────────────────────────────────────────────────────────────
# 7. FtpListModel
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Модель FTP-листинга')
class TestFtpListModel:
    ENTRIES = [
        ('2026-09-23_Moscow', True, 0),
        ('100m.gpx', False, 2048),
        ('empty.plt', False, 0),
    ]

    def test_headers(self, qapp):
        model = FtpListModel()

        assert [model.headerData(i, gui.Qt.Horizontal)
                for i in range(2)] == ['Имя', 'Размер']

    def test_empty_by_default(self, qapp):
        assert FtpListModel().rowCount() == 0

    @pytest.mark.smoke
    def test_set_entries_creates_rows(self, qapp):
        model = FtpListModel()
        model.set_entries(self.ENTRIES)

        assert model.rowCount() == 3
        assert [model.item(r).text() for r in range(3)] == \
            ['2026-09-23_Moscow', '100m.gpx', 'empty.plt']

    def test_directory_row_formatting(self, qapp):
        model = FtpListModel()
        model.set_theme('light')
        model.set_entries(self.ENTRIES)

        row = model.item(0)
        assert row.font().bold() is True
        assert row.foreground().color().name() == \
            gui.THEMES['light']['tree_dir']
        assert model.item(0, 1).text() == ''

    def test_file_row_formatting(self, qapp):
        model = FtpListModel()
        model.set_theme('light')
        model.set_entries(self.ENTRIES)

        assert model.item(1, 1).text() == '2.0 КБ'
        assert model.item(1).foreground().color().name() == \
            gui.THEMES['light']['tree_file']

    def test_zero_size_shows_empty(self, qapp):
        model = FtpListModel()
        model.set_entries(self.ENTRIES)

        assert model.item(2, 1).text() == ''

    def test_size_is_right_aligned(self, qapp):
        model = FtpListModel()
        model.set_entries(self.ENTRIES)

        assert model.item(1, 1).textAlignment() & gui.Qt.AlignRight

    @pytest.mark.smoke
    def test_ftp_name_role_is_set(self, qapp):
        model = FtpListModel()
        model.set_entries(self.ENTRIES)

        assert model.item(0).data(ROLE_FTP_NAME) == '2026-09-23_Moscow'
        assert model.item(1).data(ROLE_FTP_NAME) == '100m.gpx'

    def test_items_are_not_editable(self, qapp):
        model = FtpListModel()
        model.set_entries(self.ENTRIES)

        for row in range(model.rowCount()):
            assert model.item(row).isEditable() is False
            assert model.item(row, 1).isEditable() is False

    @pytest.mark.parametrize('name,expected', [
        ('100m.gpx', ('100m.gpx', False, 2048)),
        ('2026-09-23_Moscow', ('2026-09-23_Moscow', True, 0)),
        ('нет.gpx', None),
    ])
    def test_entry_for_name(self, qapp, name, expected):
        model = FtpListModel()
        model.set_entries(self.ENTRIES)

        assert model.entry_for_name(name) == expected

    def test_set_entries_replaces_previous(self, qapp):
        model = FtpListModel()
        model.set_entries(self.ENTRIES)
        model.set_entries([('only.gpx', False, 1)])

        assert model.rowCount() == 1
        assert model.entry_for_name('100m.gpx') is None

    def test_empty_entries_clear_list(self, qapp):
        model = FtpListModel()
        model.set_entries(self.ENTRIES)
        model.set_entries([])

        assert model.rowCount() == 0

    def test_set_theme_recolours(self, qapp):
        model = FtpListModel()
        model.set_entries(self.ENTRIES)
        model.set_theme('dark')

        assert model.item(0).foreground().color().name() == \
            gui.THEMES['dark']['tree_dir']
