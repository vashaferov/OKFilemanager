"""Тесты диалогов GUI (ok_helper.gui).

Функциональность: RenameDialog (F2), NewFolderDialog (Ctrl+N),
MoveDialog (Ctrl+X), SettingsDialog (F10), HelpDialog (F1),
OziLoadDialog (куда грузить файл в открытое окно OziExplorer).

Диалоги не показываются: модальные окна и exec() не вызываются —
проверяются поля, правила предзаполнения и результат _apply/_save.

Группировка в Allure:
  epic    = ok-filemanager
  feature = Диалоги GUI
  story   = задаётся маркером @pytest.mark.story на каждом классе.
"""

from datetime import datetime
from pathlib import Path

import allure
import pytest
from PySide6.QtCore import QEvent
from PySide6.QtGui import QColor

from ok_helper import gui
from ok_helper.gpxkml import Content, Track, TrackPoint
from ok_helper.gui import (
    HelpDialog,
    MoveDialog,
    NewFolderDialog,
    OziLoadDialog,
    RenameDialog,
    SettingsDialog,
    WaypointEditDialog,
    WptEditorDialog,
)
from ok_helper.wpt import (
    ALTITUDE_UNKNOWN,
    Waypoint,
    WptRecord,
    read_wpt,
    read_wpt_file,
)


pytestmark = [
    pytest.mark.gui,
    pytest.mark.epic('ok-filemanager'),
    pytest.mark.feature('Диалоги GUI'),
]


@pytest.fixture(autouse=True)
def destroy_dialogs(qapp):
    """Удаляет диалоги после каждого теста.

    Диалоги подключают сигналы к своим же слотам через лямбды: C++ держит
    ссылку на Python-замыкание, поэтому циклический сборщик такие объекты не
    освобождает. Без явного deleteLater накопленные окна заставляют
    QApplication.setStyleSheet() переполировывать их при каждом создании
    MainWindow в соседнем модуле (общий прогон растягивается в разы).
    """
    yield
    for widget in qapp.topLevelWidgets():
        # Всплывающие списки QComboBox — окна, но у них есть родитель:
        # они удалятся вместе с диалогом, трогать их отдельно нельзя.
        if widget.parent() is not None:
            continue
        widget.close()
        widget.setParent(None)
        widget.deleteLater()
    qapp.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()


def ctrl_t_actions(dlg):
    return [a for a in dlg.actions()
            if a.shortcut().toString() == 'Ctrl+T']


# ─────────────────────────────────────────────────────────────────
# 1. RenameDialog (F2)
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Диалог переименования')
class TestRenameDialog:
    @pytest.mark.smoke
    def test_suggestion_for_downloaded_name(self, qapp, tmp_path):
        p = tmp_path / '2026-09-17_11-55_бабушка.gpx'
        p.write_text('')

        dlg = RenameDialog(None, p)

        assert dlg.suggestion == '20260917_Babushka.gpx'
        assert dlg.date_edit.text() == '20260917'
        assert dlg.group_combo.currentText() == 'Babushka'
        assert dlg.number_edit.text() == ''
        assert dlg.callsign_combo.currentText() == ''
        assert dlg.preview.text() == '20260917_Babushka.gpx'
        assert dlg.result is None

    def test_suggestion_falls_back_to_current_name(self, qapp, tmp_path):
        p = tmp_path / '20260923_Ivanov.gpx'
        p.write_text('')

        dlg = RenameDialog(None, p)

        assert dlg.suggestion == '20260923_Ivanov.gpx'
        assert dlg.date_edit.text() == '20260923'
        assert dlg.group_combo.currentText() == 'Ivanov'

    def test_path_is_kept(self, qapp, tmp_path):
        p = tmp_path / 'notes.txt'
        p.write_text('')

        assert RenameDialog(None, p).path == p

    def test_has_ctrl_t_action(self, qapp, tmp_path):
        p = tmp_path / 'x.gpx'
        p.write_text('')

        assert len(ctrl_t_actions(RenameDialog(None, p))) == 1

    def test_plain_file_keeps_single_name_field(self, qapp, tmp_path):
        p = tmp_path / 'notes.txt'
        p.write_text('')

        dlg = RenameDialog(None, p)

        assert dlg.structured is False
        assert dlg.edit.text() == 'notes.txt'

    def test_wpt_keeps_single_name_field(self, qapp, tmp_path):
        """Формат .wpt — Waypoints_ГГГГММДД, поля частей ему не подходят."""
        p = tmp_path / '20260917.wpt'
        p.write_text('')

        dlg = RenameDialog(None, p)

        assert dlg.structured is False
        assert dlg.edit.text() == 'Waypoints_20260917.wpt'

    def test_m_gpx_keeps_single_name_field(self, qapp, tmp_path):
        """<1-4 цифры>m.gpx не проверяются — оставляем единое поле."""
        p = tmp_path / '100m.gpx'
        p.write_text('')

        dlg = RenameDialog(None, p)

        assert dlg.structured is False
        assert dlg.edit.text() == '100m.gpx'

    # --- структурированные поля .gpx/.plt ---

    def test_parts_are_split_from_name(self, qapp, tmp_path):
        p = tmp_path / '20260917_Lisa_03_Test.gpx'
        p.write_text('')

        dlg = RenameDialog(None, p)

        assert dlg.date_edit.text() == '20260917'
        assert dlg.group_combo.currentText() == 'Lisa'
        assert dlg.number_edit.text() == '03'
        assert dlg.callsign_combo.currentText() == 'Test'

    def test_dictionary_group_with_underscore_is_kept_whole(self, qapp,
                                                           tmp_path):
        p = tmp_path / '20260917_North_Team_03_Test.gpx'
        p.write_text('')

        dlg = RenameDialog(None, p, {'group_names': ['North_Team']})

        assert dlg.group_combo.currentText() == 'North_Team'
        assert dlg.number_edit.text() == '03'
        assert dlg.callsign_combo.currentText() == 'Test'
        assert dlg.preview.text() == '20260917_North_Team03_Test.gpx'

    def test_apply_builds_name_from_parts(self, qapp, tmp_path):
        p = tmp_path / 'x.gpx'
        p.write_text('')
        dlg = RenameDialog(None, p)
        dlg.date_edit.setText('20260923')
        dlg.group_combo.setCurrentText('Ivanov')

        dlg._apply()

        assert dlg.result == '20260923_Ivanov.gpx'

    def test_apply_pads_one_digit_group_number(self, qapp, tmp_path):
        p = tmp_path / 'x.gpx'
        p.write_text('')
        dlg = RenameDialog(None, p)
        dlg.date_edit.setText('20260923')
        dlg.group_combo.setCurrentText('lisa')
        dlg.number_edit.setText('3')
        dlg.callsign_combo.setCurrentText('иванов')

        dlg._apply()

        assert dlg.result == '20260923_Lisa03_Ivanov.gpx'

    def test_apply_without_callsign(self, qapp, tmp_path):
        p = tmp_path / 'x.gpx'
        p.write_text('')
        dlg = RenameDialog(None, p)
        dlg.date_edit.setText('20260923')
        dlg.group_combo.setCurrentText('Lisa')
        dlg.number_edit.setText('03')

        dlg._apply()

        assert dlg.result == '20260923_Lisa03.gpx'

    def test_apply_requires_date(self, qapp, tmp_path, msgbox):
        p = tmp_path / 'x.gpx'
        p.write_text('')
        dlg = RenameDialog(None, p)
        dlg.date_edit.setText('')
        dlg.group_combo.setCurrentText('Lisa')

        dlg._apply()

        assert dlg.result is None
        assert msgbox.kinds == ['critical']
        assert 'ГГГГММДД' in msgbox.texts()[0]

    def test_apply_requires_group(self, qapp, tmp_path, msgbox):
        p = tmp_path / 'x.gpx'
        p.write_text('')
        dlg = RenameDialog(None, p)
        dlg.date_edit.setText('20260923')
        dlg.group_combo.setCurrentText('')

        dlg._apply()

        assert dlg.result is None
        assert msgbox.kinds == ['critical']
        assert 'группы' in msgbox.texts()[0]

    def test_manual_result_is_used(self, qapp, tmp_path):
        """«Итог» можно править вручную — он и станет новым именем."""
        p = tmp_path / 'x.gpx'
        p.write_text('')
        dlg = RenameDialog(None, p)
        dlg.date_edit.setText('20260923')
        dlg.group_combo.setCurrentText('Lisa')
        dlg.preview.setText('20260923_Custom_Test.gpx')

        dlg._apply()

        assert dlg.result == '20260923_Custom_Test.gpx'

    def test_manual_result_skips_part_validation(self, qapp, tmp_path):
        p = tmp_path / 'x.gpx'
        p.write_text('')
        dlg = RenameDialog(None, p)
        dlg.date_edit.setText('')
        dlg.group_combo.setCurrentText('')
        dlg.preview.setText('Track_01.gpx')

        dlg._apply()

        assert dlg.result == 'Track_01.gpx'

    def test_manual_result_rejects_path_separators(self, qapp, tmp_path,
                                                   msgbox):
        p = tmp_path / 'x.gpx'
        p.write_text('')
        dlg = RenameDialog(None, p)
        dlg.preview.setText('sub/20260923_Lisa.gpx')

        dlg._apply()

        assert dlg.result is None
        assert msgbox.kinds == ['critical']
        assert 'путь' in msgbox.texts()[0]

    def test_empty_result_is_rejected(self, qapp, tmp_path, msgbox):
        p = tmp_path / 'x.gpx'
        p.write_text('')
        dlg = RenameDialog(None, p)
        dlg.preview.setText('   ')

        dlg._apply()

        assert dlg.result is None
        assert msgbox.kinds == ['critical']
        assert 'итоговое имя' in msgbox.texts()[0]

    def test_preview_is_rebuilt_when_parts_change(self, qapp, tmp_path):
        p = tmp_path / 'x.gpx'
        p.write_text('')
        dlg = RenameDialog(None, p)
        dlg.preview.setText('manual.gpx')

        dlg.date_edit.setText('20260923')
        dlg.group_combo.setCurrentText('Lisa')

        assert dlg.preview.text() == '20260923_Lisa.gpx'

    # --- словари ---

    def test_dictionaries_are_offered_in_combos(self, qapp, tmp_path):
        p = tmp_path / '20260923_Lisa.gpx'
        p.write_text('')
        cfg = {'group_names': ['Lisa', 'Moscow'],
               'callsigns': ['Ivanov', 'Petrov']}

        dlg = RenameDialog(None, p, cfg)

        assert [dlg.group_combo.itemText(i)
                for i in range(dlg.group_combo.count())] == ['Lisa', 'Moscow']
        assert [dlg.callsign_combo.itemText(i)
                for i in range(dlg.callsign_combo.count())] == \
            ['Ivanov', 'Petrov']

    def test_typed_values_are_added_to_dictionaries(self, qapp, tmp_path):
        p = tmp_path / 'x.gpx'
        p.write_text('')
        cfg = {'group_names': ['Lisa'], 'callsigns': ['Ivanov']}
        dlg = RenameDialog(None, p, cfg)
        dlg.date_edit.setText('20260923')
        dlg.group_combo.setCurrentText('Moscow')
        dlg.callsign_combo.setCurrentText('Petrov')

        dlg._apply()

        assert cfg['group_names'] == ['Lisa', 'Moscow']
        assert cfg['callsigns'] == ['Ivanov', 'Petrov']

    def test_dictionary_values_are_not_duplicated(self, qapp, tmp_path):
        p = tmp_path / 'x.gpx'
        p.write_text('')
        cfg = {'group_names': ['Lisa'], 'callsigns': ['Ivanov']}
        dlg = RenameDialog(None, p, cfg)
        dlg.date_edit.setText('20260923')
        dlg.group_combo.setCurrentText('Lisa')
        dlg.callsign_combo.setCurrentText('Ivanov')

        dlg._apply()

        assert cfg['group_names'] == ['Lisa']
        assert cfg['callsigns'] == ['Ivanov']

    def test_dictionary_compares_normalized_values(self, qapp, tmp_path):
        """«иванов» и «Ivanov» — одно и то же имя, дубль не нужен."""
        p = tmp_path / 'x.gpx'
        p.write_text('')
        cfg = {'group_names': ['north team'], 'callsigns': ['иванов']}
        dlg = RenameDialog(None, p, cfg)
        dlg.date_edit.setText('20260923')
        dlg.group_combo.setCurrentText('North_Team')
        dlg.callsign_combo.setCurrentText('Ivanov')

        dlg._apply()

        assert cfg['group_names'] == ['north team']
        assert cfg['callsigns'] == ['иванов']

    # --- Ctrl+T ---

    @pytest.mark.smoke
    def test_translit_and_normalize(self, qapp, tmp_path):
        p = tmp_path / 'x.gpx'
        p.write_text('')
        dlg = RenameDialog(None, p)
        dlg.date_edit.setText('20260923')
        dlg.group_combo.setCurrentText('иванов')

        dlg._translit()

        assert dlg.group_combo.currentText() == 'Ivanov'
        assert dlg.preview.text() == '20260923_Ivanov.gpx'

    def test_translit_normalizes_number(self, qapp, tmp_path):
        p = tmp_path / 'x.gpx'
        p.write_text('')
        dlg = RenameDialog(None, p)
        dlg.date_edit.setText('20260923')
        dlg.group_combo.setCurrentText('lisa')
        dlg.number_edit.setText('1')

        dlg._translit()

        assert dlg.preview.text() == '20260923_Lisa01.gpx'

    def test_translit_callsign(self, qapp, tmp_path):
        p = tmp_path / 'x.gpx'
        p.write_text('')
        dlg = RenameDialog(None, p)
        dlg.date_edit.setText('20260923')
        dlg.group_combo.setCurrentText('Lisa')
        dlg.callsign_combo.setCurrentText('петров')

        dlg._translit()

        assert dlg.callsign_combo.currentText() == 'Petrov'

    def test_translit_action_triggers_same_slot(self, qapp, tmp_path):
        p = tmp_path / 'x.gpx'
        p.write_text('')
        dlg = RenameDialog(None, p)
        dlg.date_edit.setText('20260923')
        dlg.group_combo.setCurrentText('бабушка')

        ctrl_t_actions(dlg)[0].trigger()

        assert dlg.group_combo.currentText() == 'Babushka'

    # --- единое поле для прочих файлов ---

    def test_plain_apply_sets_result(self, qapp, tmp_path):
        p = tmp_path / 'notes.txt'
        p.write_text('')
        dlg = RenameDialog(None, p)
        dlg.edit.setText('20260923_Ivanov.txt')

        dlg._apply()

        assert dlg.result == '20260923_Ivanov.txt'

    def test_plain_apply_strips_whitespace(self, qapp, tmp_path):
        p = tmp_path / 'notes.txt'
        p.write_text('')
        dlg = RenameDialog(None, p)
        dlg.edit.setText('  20260923_Ivanov.txt  ')

        dlg._apply()

        assert dlg.result == '20260923_Ivanov.txt'

    def test_plain_apply_ignores_empty_name(self, qapp, tmp_path):
        p = tmp_path / 'notes.txt'
        p.write_text('')
        dlg = RenameDialog(None, p)
        dlg.edit.setText('   ')

        dlg._apply()

        assert dlg.result is None

    def test_plain_translit_and_normalize(self, qapp, tmp_path):
        p = tmp_path / 'notes.txt'
        p.write_text('')
        dlg = RenameDialog(None, p)
        dlg.edit.setText('20260923_lisa_1.txt')

        dlg._translit()

        assert dlg.edit.text() == '20260923_Lisa01.txt'

    def test_plain_translit_without_underscore(self, qapp, tmp_path):
        p = tmp_path / 'notes.txt'
        p.write_text('')
        dlg = RenameDialog(None, p)
        dlg.edit.setText('Ёжик.txt')

        dlg._translit()

        assert dlg.edit.text() == 'Yozhik.txt'

    def test_plain_translit_keeps_dashed_date(self, qapp, tmp_path):
        """Дата не в формате ГГГГММДД — только транслит."""
        p = tmp_path / 'notes.txt'
        p.write_text('')
        dlg = RenameDialog(None, p)
        dlg.edit.setText('2026-09-17_11-55_бабушка.txt')

        dlg._translit()

        assert dlg.edit.text() == '2026-09-17_11-55_babushka.txt'


# ─────────────────────────────────────────────────────────────────
# 2. NewFolderDialog (Ctrl+N)
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Диалог новой папки')
class TestNewFolderDialog:
    @pytest.mark.smoke
    def test_root_prefills_date(self, qapp, gui_config):
        """README: в корне имя предзаполняется датой ГГГГ-ММ-ДД_."""
        dlg = NewFolderDialog(None, gui_config,
                              gui_config['root_dir_resolved'])

        assert dlg.is_root is True
        assert dlg.edit.text() == datetime.now().strftime('%Y-%m-%d_')

    @pytest.mark.smoke
    def test_root_has_ten_tracks_checkbox(self, qapp, gui_config):
        dlg = NewFolderDialog(None, gui_config,
                              gui_config['root_dir_resolved'])

        assert dlg.tracks_cb is not None
        assert dlg.tracks_cb.isChecked() is True
        assert dlg.with_10_tracks is True

    @pytest.mark.smoke
    def test_nested_dir_has_empty_name_and_no_checkbox(self, qapp, gui_config):
        """README: во вложенных папках поле пустое, чек-бокса нет."""
        sub = gui_config['root_dir_resolved'] / 'sub'
        sub.mkdir()

        dlg = NewFolderDialog(None, gui_config, sub)

        assert dlg.is_root is False
        assert dlg.edit.text() == ''
        assert dlg.tracks_cb is None

    def test_is_root_compares_resolved_paths(self, qapp, gui_config):
        weird = gui_config['root_dir_resolved'] / '.' / 'sub' / '..'

        dlg = NewFolderDialog(None, gui_config, weird)

        assert dlg.is_root is True

    def test_missing_root_dir_resolved_is_not_root(self, qapp, tmp_path):
        cfg = {'root_dir_resolved': None}

        assert NewFolderDialog(None, cfg, tmp_path).is_root is False

    def test_has_ctrl_t_action(self, qapp, gui_config):
        dlg = NewFolderDialog(None, gui_config,
                              gui_config['root_dir_resolved'])

        assert len(ctrl_t_actions(dlg)) == 1

    def test_ctrl_t_translits(self, qapp, gui_config):
        dlg = NewFolderDialog(None, gui_config,
                              gui_config['root_dir_resolved'])
        dlg.edit.setText('2026-09-23_Москва')

        ctrl_t_actions(dlg)[0].trigger()

        assert dlg.edit.text() == '2026-09-23_Moskva'

    # --- _apply ---

    @pytest.mark.smoke
    def test_apply_builds_path(self, qapp, gui_config):
        root = gui_config['root_dir_resolved']
        dlg = NewFolderDialog(None, gui_config, root)
        dlg.edit.setText('2026-09-23_Moscow')

        dlg._apply()

        assert dlg.result == root / '2026-09-23_Moscow'

    def test_apply_strips_whitespace(self, qapp, gui_config):
        root = gui_config['root_dir_resolved']
        dlg = NewFolderDialog(None, gui_config, root)
        dlg.edit.setText('  2026-09-23_Moscow  ')

        dlg._apply()

        assert dlg.result == root / '2026-09-23_Moscow'

    def test_apply_ignores_empty_name(self, qapp, gui_config, msgbox):
        dlg = NewFolderDialog(None, gui_config,
                              gui_config['root_dir_resolved'])
        dlg.edit.setText('   ')

        dlg._apply()

        assert dlg.result is None
        assert msgbox.calls == []

    @pytest.mark.parametrize('bad', [
        'a/b', 'a\\b', 'a:b', 'a*b', 'a?b', 'a"b', 'a<b', 'a>b', 'a|b',
        'имя.', '.',
    ])
    def test_apply_rejects_invalid_name(self, qapp, gui_config, msgbox, bad):
        dlg = NewFolderDialog(None, gui_config,
                              gui_config['root_dir_resolved'])
        dlg.edit.setText(bad)

        dlg._apply()

        assert dlg.result is None
        assert msgbox.kinds == ['critical']

    @pytest.mark.smoke
    def test_apply_reads_ten_tracks_checkbox(self, qapp, gui_config):
        root = gui_config['root_dir_resolved']
        dlg = NewFolderDialog(None, gui_config, root)
        dlg.edit.setText('2026-09-23_Moscow')
        dlg.tracks_cb.setChecked(True)

        dlg._apply()

        assert dlg.with_10_tracks is True

    def test_apply_respects_unchecked_ten_tracks(self, qapp, gui_config):
        """Флаг включён по умолчанию, но его можно снять."""
        root = gui_config['root_dir_resolved']
        dlg = NewFolderDialog(None, gui_config, root)
        dlg.edit.setText('2026-09-23_Moscow')
        dlg.tracks_cb.setChecked(False)

        dlg._apply()

        assert dlg.with_10_tracks is False

    def test_ten_tracks_stays_false_without_checkbox(self, qapp, gui_config):
        sub = gui_config['root_dir_resolved'] / 'sub'
        sub.mkdir()
        dlg = NewFolderDialog(None, gui_config, sub)
        dlg.edit.setText('2026-09-23_Moscow')

        dlg._apply()

        assert dlg.with_10_tracks is False
        assert dlg.result == sub / '2026-09-23_Moscow'


# ─────────────────────────────────────────────────────────────────
# 3. MoveDialog (Ctrl+X)
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Диалог перемещения')
class TestMoveDialog:
    @pytest.fixture
    def layout(self, tmp_path):
        base = tmp_path / 'base'
        src_dir = base / 'from'
        dst_dir = base / 'to'
        src_dir.mkdir(parents=True)
        dst_dir.mkdir()
        (dst_dir / 'sub').mkdir()
        (dst_dir / 'other.gpx').write_text('')
        src = src_dir / '100m.gpx'
        src.write_text('')
        return {'base': base, 'src': src, 'dst': dst_dir}

    @pytest.mark.smoke
    def test_lists_directories_before_files(self, qapp, layout):
        dlg = MoveDialog(None, layout['src'], layout['dst'])

        assert dlg.model.rowCount() == 2
        assert dlg.model.item(0).text() == 'sub'
        assert dlg.model.item(0, 1).text() == 'папка'
        assert dlg.model.item(1).text() == 'other.gpx'
        assert dlg.model.item(1, 1).text() == 'файл'

    def test_items_keep_full_path(self, qapp, layout):
        dlg = MoveDialog(None, layout['src'], layout['dst'])

        assert Path(dlg.model.item(0).data(gui.Qt.UserRole + 3)) == \
            layout['dst'] / 'sub'
        assert dlg.model.item(0).isEditable() is False

    def test_path_label_shows_current_dir(self, qapp, layout):
        dlg = MoveDialog(None, layout['src'], layout['dst'])

        assert dlg.path_lbl.text() == str(layout['dst'].resolve())

    def test_start_dir_is_resolved(self, qapp, layout):
        dlg = MoveDialog(None, layout['src'], layout['dst'])

        assert dlg.current_dir == layout['dst'].resolve()

    def test_up_moves_to_parent(self, qapp, layout):
        dlg = MoveDialog(None, layout['src'], layout['dst'] / 'sub')

        dlg._up()

        assert dlg.current_dir == layout['dst'].resolve()
        assert dlg.path_lbl.text() == str(layout['dst'].resolve())
        assert dlg.model.item(0).text() == 'sub'

    def test_up_at_filesystem_root_does_nothing(self, qapp, layout):
        dlg = MoveDialog(None, layout['src'], layout['dst'])
        dlg.current_dir = Path(dlg.current_dir.anchor)

        dlg._up()

        assert dlg.current_dir == Path(dlg.current_dir.anchor)

    @pytest.mark.smoke
    def test_enter_directory(self, qapp, layout):
        dlg = MoveDialog(None, layout['src'], layout['dst'])

        dlg._enter(dlg.model.index(0, 0))

        assert dlg.current_dir == (layout['dst'] / 'sub').resolve()
        assert dlg.model.rowCount() == 0

    def test_enter_file_does_nothing(self, qapp, layout):
        dlg = MoveDialog(None, layout['src'], layout['dst'])

        dlg._enter(dlg.model.index(1, 0))

        assert dlg.current_dir == layout['dst'].resolve()

    def test_enter_invalid_index_does_nothing(self, qapp, layout):
        dlg = MoveDialog(None, layout['src'], layout['dst'])

        dlg._enter(gui.QModelIndex())

        assert dlg.current_dir == layout['dst'].resolve()

    def test_accept_sets_result(self, qapp, layout, msgbox):
        dlg = MoveDialog(None, layout['src'], layout['dst'])
        dlg._enter(dlg.model.index(0, 0))

        dlg._accept()

        assert dlg.result == (layout['dst'] / 'sub').resolve()
        assert msgbox.calls == []

    @pytest.mark.smoke
    def test_accept_same_folder_is_refused(self, qapp, layout, msgbox):
        """README: кнопка «Переместить сюда» в исходной папке — отказ."""
        dlg = MoveDialog(None, layout['src'], layout['src'].parent)
        dlg.current_dir = layout['src'].parent

        dlg._accept()

        assert dlg.result is None
        assert msgbox.kinds == ['information']
        assert 'уже в этой папке' in msgbox.texts()[0]

    def test_populate_survives_iterdir_error(self, qapp, layout,
                                             monkeypatch):
        dlg = MoveDialog(None, layout['src'], layout['dst'])

        def boom(self, *a, **kw):
            raise OSError('нет доступа')

        monkeypatch.setattr(Path, 'iterdir', boom)
        dlg._populate()

        assert dlg.model.rowCount() == 0

    def test_no_ctrl_t_action(self, qapp, layout):
        """Транслит в этом диалоге не нужен."""
        dlg = MoveDialog(None, layout['src'], layout['dst'])

        assert ctrl_t_actions(dlg) == []


# ─────────────────────────────────────────────────────────────────
# 4. SettingsDialog (F10)
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Диалог настроек')
class TestSettingsDialog:
    def test_fields_filled_from_config(self, qapp, gui_config):
        dlg = SettingsDialog(None, gui_config)

        assert dlg.fields['root_dir'].text() == gui_config['root_dir']
        assert dlg.fields['downloads_dir'].text() == \
            gui_config['downloads_dir']
        assert dlg.fields['ftp_host'].text() == 'ftp.example.com'
        assert dlg.fields['ftp_port'].text() == '21'
        assert dlg.fields['ftp_user'].text() == 'user'
        assert dlg.fields['ftp_password'].text() == 'secret'
        assert dlg.fields['ftp_path'].text() == '/upload'
        assert dlg.fields['base_url'].text() == gui_config['base_url']
        assert dlg.fields['ozi_exe'].text() == gui_config.get('ozi_exe', '')
        assert dlg.fields['ozi_api_dll'].text() == \
            gui_config.get('ozi_api_dll', '')

    def test_ozi_download_button_opens_page(self, qapp, gui_config,
                                            monkeypatch):
        opened = []
        monkeypatch.setattr(gui.QDesktopServices, 'openUrl',
                            lambda url: opened.append(url.toString()))
        dlg = SettingsDialog(None, gui_config)

        assert 'OziAPI' in dlg.ozi_download_btn.text()
        dlg.ozi_download_btn.click()

        assert opened == [gui.OZI_API_DOWNLOAD_URL]

    def test_ozi_extract_button_extracts_and_fills_field(
            self, qapp, gui_config, msgbox, monkeypatch, tmp_path):
        dll = tmp_path / 'OziAPI.dll'
        calls = []
        monkeypatch.setattr(gui, 'extract_bundled_api',
                            lambda: calls.append(True) or dll)
        dlg = SettingsDialog(None, gui_config)

        assert 'Распаковать' in dlg.ozi_extract_btn.text()
        dlg.ozi_extract_btn.click()

        assert calls == [True]
        assert dlg.fields['ozi_api_dll'].text() == str(dll)
        assert msgbox.kinds == ['information']
        assert str(dll) in msgbox.texts()[0]

    def test_ozi_extract_button_without_archive_warns(
            self, qapp, gui_config, msgbox, monkeypatch):
        def boom():
            raise OSError('комплектный архив с OziAPI.dll не найден')

        monkeypatch.setattr(gui, 'extract_bundled_api', boom)
        dlg = SettingsDialog(None, gui_config)
        dlg.fields['ozi_api_dll'].setText('')

        dlg.ozi_extract_btn.click()

        assert msgbox.kinds == ['warning']
        assert 'не найден' in msgbox.texts()[0]
        assert dlg.fields['ozi_api_dll'].text() == ''   # поле не меняем

    def test_ozi_hint_says_no_extra_enabling_needed(self, qapp, gui_config):
        """В OziExplorer нет настройки «включить OziAPI».

        Проверено по справке OziExplorer.chm (3.95.6f): слово OziAPI
        встречается там только в истории версий, поэтому подсказка не
        должна отправлять пользователя в Configuration → OziAPI.
        """
        dlg = SettingsDialog(None, gui_config)

        hint = dlg.ozi_hint.text()

        assert 'Configuration' not in hint
        assert 'не нужно' in hint

    def test_password_is_masked(self, qapp, gui_config):
        dlg = SettingsDialog(None, gui_config)

        assert dlg.fields['ftp_password'].echoMode() == \
            gui.QLineEdit.Password
        assert dlg.fields['ftp_user'].echoMode() == gui.QLineEdit.Normal

    def test_checkboxes_reflect_config(self, qapp, gui_config):
        dlg = SettingsDialog(None, gui_config)

        assert dlg.watch_cb.isChecked() is False
        assert dlg.help_cb.isChecked() is False
        assert dlg.ftp_readonly_cb.isChecked() is False
        assert dlg.log_visible_cb.isChecked() is True   # журнал виден
        assert dlg.blocking_errors_cb.isChecked() is False

    def test_checkboxes_reflect_enabled_config(self, qapp, gui_config):
        gui_config.update({'watch_downloads': True,
                           'help_visible_at_start': True,
                           'ftp_readonly': True,
                           'log_visible': False,
                           'blocking_errors': True})

        dlg = SettingsDialog(None, gui_config)

        assert dlg.watch_cb.isChecked() is True
        assert dlg.help_cb.isChecked() is True
        assert dlg.ftp_readonly_cb.isChecked() is True
        assert dlg.log_visible_cb.isChecked() is False
        assert dlg.blocking_errors_cb.isChecked() is True

    def test_theme_combo(self, qapp, gui_config):
        dlg = SettingsDialog(None, gui_config)

        assert [dlg.theme_combo.itemText(i)
                for i in range(dlg.theme_combo.count())] == \
            ['auto', 'light', 'dark']
        assert dlg.theme_combo.currentText() == 'light'

    def test_advanced_theme_key(self, qapp, gui_config):
        gui_config['theme'] = 'dark'

        assert SettingsDialog(None, gui_config).theme_combo.currentText() \
            == 'dark'

    # --- ошибки валидации ---

    def test_bad_root_dir_refused(self, qapp, gui_config, msgbox, tmp_path):
        before = gui_config['root_dir']
        dlg = SettingsDialog(None, gui_config)
        dlg.fields['root_dir'].setText(str(tmp_path / 'нет-такой-папки'))

        dlg._save()

        assert msgbox.kinds == ['critical']
        assert 'root_dir' in msgbox.texts()[0]
        assert gui_config['root_dir'] == before

    def test_bad_downloads_dir_refused(self, qapp, gui_config, msgbox,
                                       tmp_path):
        dlg = SettingsDialog(None, gui_config)
        dlg.fields['downloads_dir'].setText(str(tmp_path / 'нет-такой'))

        dlg._save()

        assert msgbox.kinds == ['critical']
        assert 'downloads_dir' in msgbox.texts()[0]

    @pytest.mark.parametrize('bad', ['abc', '0', '70000', '-1'])
    def test_bad_port_refused(self, qapp, gui_config, msgbox, bad):
        dlg = SettingsDialog(None, gui_config)
        dlg.fields['ftp_port'].setText(bad)

        dlg._save()

        assert msgbox.kinds == ['critical']
        assert 'порт' in msgbox.texts()[0].lower()
        assert gui_config['ftp_port'] == 21

    def test_empty_port_defaults_to_21(self, qapp, gui_config, msgbox):
        dlg = SettingsDialog(None, gui_config)
        dlg.fields['ftp_port'].setText('')

        dlg._save()

        assert msgbox.calls == []
        assert gui_config['ftp_port'] == 21

    # --- успешное сохранение ---

    @pytest.mark.smoke
    def test_save_updates_config(self, qapp, gui_config, msgbox, tmp_path):
        other_root = tmp_path / 'other'
        other_root.mkdir()
        dlg = SettingsDialog(None, gui_config)
        dlg.fields['root_dir'].setText(str(other_root))
        dlg.fields['ftp_host'].setText('  new.example.com  ')
        dlg.fields['ftp_port'].setText('2121')
        dlg.fields['ftp_user'].setText('bob')
        dlg.fields['ftp_password'].setText('p@ss')
        dlg.fields['ftp_path'].setText('/upload/sub')
        dlg.fields['base_url'].setText('  https://new.example.com/ftp  ')
        dlg.fields['ozi_exe'].setText(r'  C:\Ozi\oziexp.exe  ')
        dlg.fields['ozi_api_dll'].setText(r'  C:\Ozi\OziAPI.dll  ')
        dlg.ozi_track_spin.setValue(12)
        dlg.ozi_wpt_combo.setCurrentIndex(
            dlg.ozi_wpt_combo.findData('replace'))
        dlg.ozi_track_ask_cb.setChecked(False)
        dlg.ozi_wpt_ask_cb.setChecked(False)
        dlg.theme_combo.setCurrentText('dark')
        dlg.watch_cb.setChecked(True)
        dlg.help_cb.setChecked(True)
        dlg.ftp_readonly_cb.setChecked(True)
        dlg.log_visible_cb.setChecked(False)
        dlg.blocking_errors_cb.setChecked(True)

        dlg._save()

        assert msgbox.calls == []
        assert gui_config['root_dir'] == str(other_root)
        assert gui_config['root_dir_resolved'] == other_root.resolve()
        assert gui_config['ftp_host'] == 'new.example.com'
        assert gui_config['ftp_port'] == 2121
        assert gui_config['ftp_user'] == 'bob'
        assert gui_config['ftp_password'] == 'p@ss'
        assert gui_config['ftp_path'] == '/upload/sub'
        assert gui_config['base_url'] == 'https://new.example.com/ftp'
        assert gui_config['ozi_exe'] == r'C:\Ozi\oziexp.exe'
        assert gui_config['ozi_api_dll'] == r'C:\Ozi\OziAPI.dll'
        assert gui_config['ozi_track_num'] == 12
        assert gui_config['ozi_wpt_mode'] == 'replace'
        assert gui_config['ozi_track_ask'] is False
        assert gui_config['ozi_wpt_ask'] is False
        assert gui_config['theme'] == 'dark'
        assert gui_config['watch_downloads'] is True
        assert gui_config['help_visible_at_start'] is True
        assert gui_config['ftp_readonly'] is True
        assert gui_config['log_visible'] is False
        assert gui_config['blocking_errors'] is True

    # --- словари ---

    def test_dictionaries_are_loaded_from_config(self, qapp, gui_config):
        gui_config['group_names'] = ['Lisa', 'Moscow']
        gui_config['callsigns'] = ['Ivanov']

        dlg = SettingsDialog(None, gui_config)

        assert dlg.group_names_edit.toPlainText() == 'Lisa\nMoscow'
        assert dlg.callsigns_edit.toPlainText() == 'Ivanov'

    def test_dictionaries_are_trimmed_and_deduplicated(self, qapp, gui_config,
                                                       msgbox):
        dlg = SettingsDialog(None, gui_config)
        dlg.group_names_edit.setPlainText(' Lisa \n\nMoscow\nLisa\n')
        dlg.callsigns_edit.setPlainText('  Petrov  \nIvanov')

        dlg._save()

        assert msgbox.calls == []
        assert gui_config['group_names'] == ['Lisa', 'Moscow']
        assert gui_config['callsigns'] == ['Petrov', 'Ivanov']

    def test_empty_ftp_path_becomes_root(self, qapp, gui_config, msgbox):
        dlg = SettingsDialog(None, gui_config)
        dlg.fields['ftp_path'].setText('   ')

        dlg._save()

        assert gui_config['ftp_path'] == '/'

    def test_password_is_not_stripped(self, qapp, gui_config, msgbox):
        """Пароль может содержать значимые пробелы."""
        dlg = SettingsDialog(None, gui_config)
        dlg.fields['ftp_password'].setText('  secret  ')

        dlg._save()

        assert gui_config['ftp_password'] == '  secret  '

    def test_save_accepts_dialog(self, qapp, gui_config, msgbox):
        dlg = SettingsDialog(None, gui_config)

        dlg._save()

        assert dlg.result() == gui.QDialog.Accepted

    def test_config_untouched_on_error(self, qapp, gui_config, msgbox):
        dlg = SettingsDialog(None, gui_config)
        dlg.fields['ftp_port'].setText('99999')
        dlg.theme_combo.setCurrentText('dark')

        dlg._save()

        assert gui_config['theme'] == 'light'


@pytest.mark.story('Вкладки в настройках')
class TestSettingsTabs:
    """F10 разбито на вкладки, чтобы окно не разрасталось по высоте.

    Все разделы подряд давали окно 851x982; вкладки оставляют ту же
    функциональность, но высоту определяет самая высокая вкладка.
    """

    def test_tab_names(self, qapp, gui_config):
        dlg = SettingsDialog(None, gui_config)

        names = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]

        assert names == ['Папки', 'Интерфейс', 'Словари', 'FTP',
                         'OziExplorer']

    @pytest.mark.parametrize('key,tab', [
        ('root_dir', 'Папки'),
        ('downloads_dir', 'Папки'),
        ('ftp_host', 'FTP'),
        ('base_url', 'FTP'),
        ('ozi_exe', 'OziExplorer'),
        ('ozi_api_dll', 'OziExplorer'),
    ])
    def test_fields_live_on_expected_tabs(self, qapp, gui_config, key, tab):
        dlg = SettingsDialog(None, gui_config)

        assert dlg.tabs.tabText(dlg._tab_of(dlg.fields[key])) == tab

    @pytest.mark.parametrize('attr,tab', [
        ('watch_cb', 'Папки'),
        ('theme_combo', 'Интерфейс'),
        ('help_cb', 'Интерфейс'),
        ('log_visible_cb', 'Интерфейс'),
        ('blocking_errors_cb', 'Интерфейс'),
        ('ftp_readonly_cb', 'FTP'),
        ('ozi_download_btn', 'OziExplorer'),
        ('ozi_extract_btn', 'OziExplorer'),
        ('ozi_track_spin', 'OziExplorer'),
        ('ozi_wpt_combo', 'OziExplorer'),
        ('ozi_track_ask_cb', 'OziExplorer'),
        ('ozi_wpt_ask_cb', 'OziExplorer'),
        ('group_names_edit', 'Словари'),
        ('callsigns_edit', 'Словари'),
    ])
    def test_widgets_live_on_expected_tabs(self, qapp, gui_config, attr, tab):
        dlg = SettingsDialog(None, gui_config)

        assert dlg.tabs.tabText(dlg._tab_of(getattr(dlg, attr))) == tab

    def test_bad_root_dir_switches_to_folders_tab(self, qapp, gui_config,
                                                  msgbox, tmp_path):
        """Поле с ошибкой может быть на скрытой вкладке — показываем её."""
        dlg = SettingsDialog(None, gui_config)
        dlg.tabs.setCurrentIndex(2)                      # уходим на «FTP»
        dlg.fields['root_dir'].setText(str(tmp_path / 'нет-такой-папки'))

        dlg._save()

        assert msgbox.kinds == ['critical']
        assert dlg.tabs.tabText(dlg.tabs.currentIndex()) == 'Папки'

    def test_bad_downloads_dir_switches_to_folders_tab(self, qapp, gui_config,
                                                       msgbox, tmp_path):
        dlg = SettingsDialog(None, gui_config)
        dlg.tabs.setCurrentIndex(2)
        dlg.fields['downloads_dir'].setText(str(tmp_path / 'нет-такой'))

        dlg._save()

        assert msgbox.kinds == ['critical']
        assert dlg.tabs.tabText(dlg.tabs.currentIndex()) == 'Папки'

    def test_bad_port_switches_to_ftp_tab(self, qapp, gui_config, msgbox):
        dlg = SettingsDialog(None, gui_config)
        dlg.tabs.setCurrentIndex(0)
        dlg.fields['ftp_port'].setText('0')

        dlg._save()

        assert msgbox.kinds == ['critical']
        assert dlg.tabs.tabText(dlg.tabs.currentIndex()) == 'FTP'

    def test_dialog_is_compact(self, qapp, gui_config):
        dlg = SettingsDialog(None, gui_config)

        assert dlg.minimumSizeHint().height() < 700
        assert dlg.minimumSizeHint().width() < 700

    def test_no_page_content_is_clipped(self, qapp, gui_config):
        """Содержимое каждой вкладки помещается целиком."""
        dlg = SettingsDialog(None, gui_config)
        dlg.show()
        qapp.processEvents()

        for i in range(dlg.tabs.count()):
            page = dlg.tabs.widget(i)
            dlg.tabs.setCurrentIndex(i)
            qapp.processEvents()
            need = page.minimumSizeHint()
            assert page.width() >= need.width(), dlg.tabs.tabText(i)
            assert page.height() >= need.height(), dlg.tabs.tabText(i)

        dlg.close()

    def test_ozi_buttons_fit_on_their_tab(self, qapp, gui_config):
        """Строку кнопок OziAPI не сжимает — иначе текст обрезается."""
        dlg = SettingsDialog(None, gui_config)
        dlg.show()
        dlg.tabs.setCurrentIndex(dlg.tabs.count() - 1)
        qapp.processEvents()

        row = dlg.ozi_download_btn.parentWidget()
        need = (dlg.ozi_download_btn.sizeHint().width()
                + dlg.ozi_extract_btn.sizeHint().width())

        assert row.width() >= need
        dlg.close()


@pytest.mark.story('Выбор места загрузки в OziExplorer')
class TestOziPositionSettings:
    """Поля «куда грузить» на вкладке OziExplorer (F10)."""

    def test_track_spin_range(self, qapp, gui_config):
        dlg = SettingsDialog(None, gui_config)

        assert dlg.ozi_track_spin.minimum() == 1
        assert dlg.ozi_track_spin.maximum() == gui.OZI_TRACK_MAX

    def test_track_spin_from_config(self, qapp, gui_config):
        gui_config['ozi_track_num'] = 9

        assert SettingsDialog(None, gui_config).ozi_track_spin.value() == 9

    @pytest.mark.parametrize('raw,expected', [
        (0, 1), (-4, 1), (76, 75), (1000, 75),
    ])
    def test_track_spin_clamps_bad_config(self, qapp, gui_config, raw,
                                          expected):
        """Мусор в конфиге не должен ронять окно настроек."""
        gui_config['ozi_track_num'] = raw

        assert SettingsDialog(None, gui_config).ozi_track_spin.value() == \
            expected

    def test_track_spin_survives_text(self, qapp, gui_config):
        gui_config['ozi_track_num'] = 'abc'

        assert SettingsDialog(None, gui_config).ozi_track_spin.value() == 1

    def test_wpt_combo_items(self, qapp, gui_config):
        dlg = SettingsDialog(None, gui_config)

        data = [dlg.ozi_wpt_combo.itemData(i)
                for i in range(dlg.ozi_wpt_combo.count())]

        assert data == list(gui.OZI_WPT_MODES)

    def test_wpt_combo_from_config(self, qapp, gui_config):
        gui_config['ozi_wpt_mode'] = 'replace'

        assert SettingsDialog(None, gui_config).ozi_wpt_combo \
            .currentData() == 'replace'

    def test_wpt_combo_bad_config_falls_back_to_append(self, qapp,
                                                       gui_config):
        gui_config['ozi_wpt_mode'] = 'что-то своё'

        assert SettingsDialog(None, gui_config).ozi_wpt_combo \
            .currentData() == 'append'

    def test_ask_checkboxes_default_true(self, qapp, gui_config):
        dlg = SettingsDialog(None, gui_config)

        assert dlg.ozi_track_ask_cb.isChecked() is True
        assert dlg.ozi_wpt_ask_cb.isChecked() is True

    def test_ask_checkboxes_reflect_config(self, qapp, gui_config):
        gui_config['ozi_track_ask'] = False
        gui_config['ozi_wpt_ask'] = False

        dlg = SettingsDialog(None, gui_config)

        assert dlg.ozi_track_ask_cb.isChecked() is False
        assert dlg.ozi_wpt_ask_cb.isChecked() is False

    def test_ask_checkboxes_explain_themselves(self, qapp, gui_config):
        """Галочки снимаются диалогом загрузки — об этом надо сказать."""
        dlg = SettingsDialog(None, gui_config)

        assert 'Запомнить' in dlg.ozi_track_ask_cb.toolTip()
        assert 'Запомнить' in dlg.ozi_wpt_ask_cb.toolTip()


@pytest.mark.story('Диалог выбора места загрузки')
class TestOziLoadDialog:
    """OziLoadDialog: номер трека для .plt или добавление/замена .wpt."""

    def test_track_mode_shows_spin(self, qapp, tmp_path):
        dlg = OziLoadDialog(None, tmp_path / 'track.plt', 'track',
                            track_num=5)

        assert dlg.spin.value() == 5
        assert dlg.spin.minimum() == 1
        assert dlg.spin.maximum() == gui.OZI_TRACK_MAX

    @pytest.mark.parametrize('raw,expected', [
        (0, 1), (-3, 1), (100, gui.OZI_TRACK_MAX),
    ])
    def test_track_number_is_clamped(self, qapp, tmp_path, raw, expected):
        dlg = OziLoadDialog(None, tmp_path / 'track.plt', 'track',
                            track_num=raw)

        assert dlg.spin.value() == expected

    def test_file_name_is_shown(self, qapp, tmp_path):
        dlg = OziLoadDialog(None, tmp_path / 'трек.plt', 'track')

        assert 'трек.plt' in dlg.findChild(gui.QLabel).text()

    def test_track_accept_reads_spin(self, qapp, tmp_path):
        dlg = OziLoadDialog(None, tmp_path / 'track.plt', 'track')
        dlg.spin.setValue(23)

        dlg._accept()

        assert dlg.track_num == 23
        assert dlg.mode == 'track'

    def test_wpt_mode_defaults_to_append(self, qapp, tmp_path):
        dlg = OziLoadDialog(None, tmp_path / 'points.wpt', 'wp')

        assert dlg.append_rb.isChecked() is True
        assert dlg.replace_rb.isChecked() is False

    def test_wpt_mode_prefilled_from_config(self, qapp, tmp_path):
        dlg = OziLoadDialog(None, tmp_path / 'points.wpt', 'wp',
                            wpt_mode='replace')

        assert dlg.replace_rb.isChecked() is True

    def test_wpt_accept_reads_radio(self, qapp, tmp_path):
        dlg = OziLoadDialog(None, tmp_path / 'points.wpt', 'wp')
        dlg.replace_rb.setChecked(True)

        dlg._accept()

        assert dlg.wpt_mode == 'replace'

    def test_wpt_accept_append(self, qapp, tmp_path):
        dlg = OziLoadDialog(None, tmp_path / 'points.wpt', 'wp',
                            wpt_mode='replace')
        dlg.append_rb.setChecked(True)

        dlg._accept()

        assert dlg.wpt_mode == 'append'

    def test_remember_defaults_to_false(self, qapp, tmp_path):
        dlg = OziLoadDialog(None, tmp_path / 'track.plt', 'track')

        assert dlg.remember_cb.isChecked() is False
        assert dlg.remember is False

    def test_remember_is_read_on_accept(self, qapp, tmp_path):
        dlg = OziLoadDialog(None, tmp_path / 'track.plt', 'track')
        dlg.remember_cb.setChecked(True)

        dlg._accept()

        assert dlg.remember is True

    def test_remember_explains_how_to_turn_it_back(self, qapp, tmp_path):
        dlg = OziLoadDialog(None, tmp_path / 'track.plt', 'track')

        assert 'F10' in dlg.remember_cb.toolTip()

    def test_ok_button_says_load(self, qapp, tmp_path):
        """Кнопки диалога: действие, а не «ОК»."""
        dlg = OziLoadDialog(None, tmp_path / 'track.plt', 'track')
        box = dlg.findChild(gui.QDialogButtonBox)

        assert box.button(gui.QDialogButtonBox.Ok).text() == 'Загрузить'
        assert box.button(gui.QDialogButtonBox.Cancel).text() == 'Отмена'

    def test_reject_keeps_values(self, qapp, tmp_path):
        """Отмена — настройки не меняются, загрузки не будет."""
        dlg = OziLoadDialog(None, tmp_path / 'track.plt', 'track',
                            track_num=4)
        dlg.spin.setValue(30)
        dlg.reject()

        assert dlg.track_num == 4
        assert dlg.remember is False

    def test_has_no_track_widgets_in_wpt_mode(self, qapp, tmp_path):
        dlg = OziLoadDialog(None, tmp_path / 'points.wpt', 'wp')

        assert not hasattr(dlg, 'spin')

    def test_has_no_radio_widgets_in_track_mode(self, qapp, tmp_path):
        dlg = OziLoadDialog(None, tmp_path / 'track.plt', 'track')

        assert not hasattr(dlg, 'append_rb')


def gpx_content(tracks=1, points=0) -> Content:
    """Что «нашлось» в .gpx/.kml для диалога."""
    return Content(
        tracks=[Track(segments=[[TrackPoint(lat=60.5 + i / 1000,
                                            lon=60.1 + i / 1000)
                                 for i in range(3)]])
                for _ in range(tracks)],
        waypoints=[Waypoint(name=f'A{i}', lat=57.4 + i / 100, lon=60.9)
                   for i in range(points)],
    )


@pytest.mark.story('Диалог загрузки .gpx/.kml')
class TestOziLoadDialogGpxKml:
    """OziLoadDialog для .gpx/.kml: и треки, и точки в одном файле.

    Про треки спрашиваем, только если треки в файле есть, про точки —
    только если есть точки: спрашивать про то, чего нет, незачем.
    """

    def test_tracks_and_points_show_both_rows(self, qapp, tmp_path):
        dlg = OziLoadDialog(None, tmp_path / 'track.gpx', 'gpxkml',
                            content=gpx_content(tracks=2, points=3))

        assert dlg.ask_track is True
        assert dlg.ask_wpt is True
        assert hasattr(dlg, 'spin')
        assert hasattr(dlg, 'append_rb')

    def test_only_tracks_hides_point_rows(self, qapp, tmp_path):
        dlg = OziLoadDialog(None, tmp_path / 'track.gpx', 'gpxkml',
                            content=gpx_content(tracks=1))

        assert dlg.ask_track is True
        assert dlg.ask_wpt is False
        assert not hasattr(dlg, 'append_rb')

    def test_only_points_hides_track_row(self, qapp, tmp_path):
        dlg = OziLoadDialog(None, tmp_path / 'points.kml', 'gpxkml',
                            content=gpx_content(tracks=0, points=2))

        assert dlg.ask_track is False
        assert dlg.ask_wpt is True
        assert not hasattr(dlg, 'spin')

    def test_empty_content_hides_everything(self, qapp, tmp_path):
        dlg = OziLoadDialog(None, tmp_path / 'пусто.gpx', 'gpxkml',
                            content=Content())

        assert dlg.ask_track is False
        assert dlg.ask_wpt is False

    def test_summary_tells_what_is_in_file(self, qapp, tmp_path):
        dlg = OziLoadDialog(None, tmp_path / 'track.gpx', 'gpxkml',
                            content=gpx_content(tracks=1, points=4))
        texts = [label.text() for label in dlg.findChildren(gui.QLabel)]

        assert any('треков 1' in text and 'точек 4' in text
                   for text in texts)

    def test_track_count_limits_the_spin(self, qapp, tmp_path):
        """Треки файла должны влезть в 75 слотов — это и ограничивает номер."""
        dlg = OziLoadDialog(None, tmp_path / 'track.gpx', 'gpxkml',
                            content=gpx_content(tracks=3))

        assert dlg.track_count == 3
        assert dlg.spin.maximum() == gui.OZI_TRACK_MAX - 2

    def test_single_track_keeps_full_range(self, qapp, tmp_path):
        dlg = OziLoadDialog(None, tmp_path / 'track.gpx', 'gpxkml',
                            content=gpx_content(tracks=1))

        assert dlg.spin.maximum() == gui.OZI_TRACK_MAX

    def test_ask_flags_can_be_narrowed(self, qapp, tmp_path):
        """Галочка «не спрашивать» снимает только свой вопрос."""
        dlg = OziLoadDialog(None, tmp_path / 'track.gpx', 'gpxkml',
                            content=gpx_content(tracks=1, points=1),
                            ask_track=False, ask_wpt=True)

        assert dlg.ask_track is False
        assert dlg.ask_wpt is True
        assert not hasattr(dlg, 'spin')

    def test_accept_reads_both_choices(self, qapp, tmp_path):
        dlg = OziLoadDialog(None, tmp_path / 'track.gpx', 'gpxkml',
                            content=gpx_content(tracks=1, points=1))
        dlg.spin.setValue(12)
        dlg.replace_rb.setChecked(True)

        dlg._accept()

        assert dlg.track_num == 12
        assert dlg.wpt_mode == 'replace'

    def test_low_number_with_many_tracks(self, qapp, tmp_path):
        """Номер подтягивается: 74 с тремя треками не поместились бы."""
        dlg = OziLoadDialog(None, tmp_path / 'track.gpx', 'gpxkml',
                            track_num=74, content=gpx_content(tracks=3))

        assert dlg.spin.value() == 73


@pytest.mark.story('Выбор пути в настройках')
class TestSettingsBrowse:
    """Кнопки «…» справа от полей-путей (папки и файлы)."""

    def test_buttons_only_on_path_fields(self, qapp, gui_config):
        dlg = SettingsDialog(None, gui_config)

        assert set(dlg.browse_buttons) == {
            'root_dir', 'downloads_dir', 'ozi_exe', 'ozi_api_dll'}

    def test_dir_button_fills_field(self, qapp, gui_config, monkeypatch,
                                    tmp_path):
        target = tmp_path / 'chosen'
        target.mkdir()
        monkeypatch.setattr(gui.QFileDialog, 'getExistingDirectory',
                            lambda *a, **kw: str(target))
        dlg = SettingsDialog(None, gui_config)

        dlg.browse_buttons['root_dir'].click()

        assert dlg.fields['root_dir'].text() == str(target)

    def test_dir_button_uses_current_value(self, qapp, gui_config, monkeypatch,
                                           tmp_path):
        seen = []
        monkeypatch.setattr(gui.QFileDialog, 'getExistingDirectory',
                            lambda parent, caption, start: seen.append(start))
        dlg = SettingsDialog(None, gui_config)
        dlg.fields['downloads_dir'].setText(str(tmp_path))

        dlg.browse_buttons['downloads_dir'].click()

        assert seen == [str(tmp_path)]

    def test_file_button_fills_field_with_filter(self, qapp, gui_config,
                                                 monkeypatch, tmp_path):
        exe = tmp_path / 'oziexp.exe'
        exe.write_text('', encoding='utf-8')
        seen = {}

        def fake(parent, caption, base, flt):
            seen['filter'] = flt
            return str(exe), flt

        monkeypatch.setattr(gui.QFileDialog, 'getOpenFileName', fake)
        dlg = SettingsDialog(None, gui_config)

        dlg.browse_buttons['ozi_exe'].click()

        assert dlg.fields['ozi_exe'].text() == str(exe)
        assert '*.exe' in seen['filter']

    def test_dll_button_filter(self, qapp, gui_config, monkeypatch, tmp_path):
        dll = tmp_path / 'OziAPI.dll'
        dll.write_text('', encoding='utf-8')
        seen = {}

        def fake(parent, caption, base, flt):
            seen['filter'] = flt
            return str(dll), flt

        monkeypatch.setattr(gui.QFileDialog, 'getOpenFileName', fake)
        dlg = SettingsDialog(None, gui_config)

        dlg.browse_buttons['ozi_api_dll'].click()

        assert dlg.fields['ozi_api_dll'].text() == str(dll)
        assert '*.dll' in seen['filter']

    def test_cancel_keeps_text(self, qapp, gui_config, monkeypatch):
        monkeypatch.setattr(gui.QFileDialog, 'getExistingDirectory',
                            lambda *a, **kw: '')
        monkeypatch.setattr(gui.QFileDialog, 'getOpenFileName',
                            lambda *a, **kw: ('', ''))
        dlg = SettingsDialog(None, gui_config)
        dlg.fields['root_dir'].setText(r'C:\keep\me')
        dlg.fields['ozi_exe'].setText(r'C:\keep\oziexp.exe')

        dlg.browse_buttons['root_dir'].click()
        dlg.browse_buttons['ozi_exe'].click()

        assert dlg.fields['root_dir'].text() == r'C:\keep\me'
        assert dlg.fields['ozi_exe'].text() == r'C:\keep\oziexp.exe'


# ─────────────────────────────────────────────────────────────────
# 5. HelpDialog (F1)
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Окно справки')
class TestHelpDialog:
    def test_checkbox_reflects_config(self, qapp, gui_config):
        gui_config['help_visible_at_start'] = True

        assert HelpDialog(None, gui_config).show_cb.isChecked() is True

    def test_toggle_writes_to_config(self, qapp, gui_config):
        dlg = HelpDialog(None, gui_config)

        dlg.show_cb.setChecked(True)

        assert gui_config['help_visible_at_start'] is True

    def test_toggle_off_writes_to_config(self, qapp, gui_config):
        gui_config['help_visible_at_start'] = True
        dlg = HelpDialog(None, gui_config)

        dlg.show_cb.setChecked(False)

        assert gui_config['help_visible_at_start'] is False

    @pytest.mark.parametrize('key', [
        'F1', 'F2', 'F5', 'Ctrl+F5', 'Ctrl+N', 'Ctrl+O', 'Ctrl+X',
        'Ctrl+Y', 'F10', 'F12', 'Delete', 'Ctrl+E', 'Ctrl+L',
        'Ctrl+1', 'Ctrl+2', 'Ctrl+D',
    ])
    def test_help_lists_documented_hotkeys(self, key):
        """Справка должна перечислять клавиши из README."""
        assert key in HelpDialog.HELP_TEXT

    @pytest.mark.parametrize('fragment', [
        'ГГГГММДД', 'Waypoints_', '10-Tracks', 'ftp_readonly',
        'auto', 'light', 'dark', '✓', 'Ctrl+Shift+C',
        'скрыть панель журнала', 'base_url', 'OziExplorer',
        'кнопка «…»',
    ])
    def test_help_covers_documented_rules(self, fragment):
        assert fragment in HelpDialog.HELP_TEXT

    def test_help_mentions_authopodbstanovka(self):
        assert 'автоподстановка' in HelpDialog.HELP_TEXT.lower()

    def test_help_explains_double_click_on_file(self):
        """README: двойной клик по файлу — меню действий, а не открытие."""
        assert 'меню доступных действий' in HelpDialog.HELP_TEXT

    def test_help_has_no_ozi_api_setting_pointer(self):
        """Справка ссылалась на отсутствующий пункт Configuration → OziAPI."""
        assert 'Configuration' not in HelpDialog.HELP_TEXT


# ─────────────────────────────────────────────────────────────────
# 6. Редактор файла точек .wpt (контекстное меню файла)
# ─────────────────────────────────────────────────────────────────

WPT_HEADER = ('OziExplorer Waypoint File Version 1.1\r\n'
              'WGS 84\r\n'
              'Reserved 2\r\n'
              'garmin\r\n')
WPT_LINE_1 = ('1,Первая,56.1,60.1,46325.2740203,0,1,3,0,255,'
              'Комментарий,0,0,0,-777,6,0,17,0,10.0,2,,,,300')
WPT_LINE_2 = ('2,Вторая,56.2,60.2,46325.2747481,7,1,3,0,255,'
              ',0,0,0,-777,6,0,17,0,10.0,2,,,,300')


def make_wpt(tmp_path, lines=(WPT_LINE_1, WPT_LINE_2)):
    src = tmp_path / 'Waypoints_20261030.wpt'
    text = WPT_HEADER + ''.join(line + '\r\n' for line in lines)
    src.write_bytes(text.encode('cp1251'))
    return src


@pytest.mark.story('Правка одной точки .wpt')
class TestWaypointEditDialog:
    def test_fields_are_prefilled(self, qapp):
        record = WptRecord.from_line(WPT_LINE_1)

        dlg = WaypointEditDialog(None, record)

        assert dlg.name_edit.text() == 'Первая'
        assert dlg.lat_edit.text() == '56.100000'
        assert dlg.lon_edit.text() == '60.100000'
        assert dlg.alt_edit.text() == ''
        assert dlg.date_edit.text() == gui._format_wpt_date(record.date)
        assert dlg.description_edit.text() == 'Комментарий'
        assert dlg.symbol_spin.value() == 0

    @pytest.mark.smoke
    def test_apply_updates_record(self, qapp):
        record = WptRecord.from_line(WPT_LINE_1)
        dlg = WaypointEditDialog(None, record)
        dlg.name_edit.setText('Новая')
        dlg.lat_edit.setText('56,5')
        dlg.lon_edit.setText('60.5')
        dlg.alt_edit.setText('250')
        dlg.description_edit.setText('Пометка, и запятая')
        dlg.symbol_spin.setValue(12)

        dlg._apply()

        assert dlg.result is record
        assert record.name == 'Новая'
        assert record.lat == pytest.approx(56.5)
        assert record.lon == pytest.approx(60.5)
        assert record.altitude_m == pytest.approx(250.0)
        assert record.description == 'Пометка; и запятая'
        assert record.symbol == 12

    @pytest.mark.smoke
    def test_apply_replaces_cyrillic_s(self, qapp):
        """Кириллическая «с» в имени и описании становится латинской."""
        record = WptRecord.from_line(WPT_LINE_1)
        dlg = WaypointEditDialog(None, record)
        dlg.name_edit.setText('Следы')
        dlg.description_edit.setText('сгущёнка')

        dlg._apply()

        assert record.name == 'Cледы'
        assert record.description == 'cгущёнка'

    def test_empty_name_is_rejected(self, qapp, msgbox):
        dlg = WaypointEditDialog(None, WptRecord.from_line(WPT_LINE_1))
        dlg.name_edit.setText('   ')

        dlg._apply()

        assert dlg.result is None
        assert 'critical' in msgbox.kinds

    @pytest.mark.parametrize('value', ['', 'широта', '91', '-91'])
    def test_bad_latitude_is_rejected(self, qapp, msgbox, value):
        dlg = WaypointEditDialog(None, WptRecord.from_line(WPT_LINE_1))
        dlg.lat_edit.setText(value)

        dlg._apply()

        assert dlg.result is None
        assert 'critical' in msgbox.kinds

    @pytest.mark.parametrize('value', ['', 'долгота', '181', '-181'])
    def test_bad_longitude_is_rejected(self, qapp, msgbox, value):
        dlg = WaypointEditDialog(None, WptRecord.from_line(WPT_LINE_1))
        dlg.lon_edit.setText(value)

        dlg._apply()

        assert dlg.result is None
        assert 'critical' in msgbox.kinds

    def test_bad_altitude_is_rejected(self, qapp, msgbox):
        dlg = WaypointEditDialog(None, WptRecord.from_line(WPT_LINE_1))
        dlg.alt_edit.setText('высоко')

        dlg._apply()

        assert dlg.result is None
        assert 'critical' in msgbox.kinds

    def test_empty_altitude_means_unknown(self, qapp):
        record = WptRecord.from_line(WPT_LINE_1)
        record.altitude_m = 100.0
        dlg = WaypointEditDialog(None, record)
        dlg.alt_edit.setText('')

        dlg._apply()

        assert record.altitude_ft == ALTITUDE_UNKNOWN

    def test_bad_date_is_rejected(self, qapp, msgbox):
        dlg = WaypointEditDialog(None, WptRecord.from_line(WPT_LINE_1))
        dlg.date_edit.setText('вчера')

        dlg._apply()

        assert dlg.result is None
        assert 'critical' in msgbox.kinds

    def test_unchanged_date_is_kept_exactly(self, qapp):
        """Правка имени не должна пересчитывать дату OziExplorer."""
        record = WptRecord.from_line(WPT_LINE_1)
        dlg = WaypointEditDialog(None, record)
        dlg.name_edit.setText('Только имя')

        dlg._apply()

        assert record.fields[4] == '46325.2740203'

    def test_date_can_be_changed(self, qapp):
        record = WptRecord.from_line(WPT_LINE_1)
        dlg = WaypointEditDialog(None, record)
        dlg.date_edit.setText('31.10.2026 12:30')

        dlg._apply()

        assert gui._format_wpt_date(record.date) == '31.10.2026 12:30'

    # ---------- цвет точки (фон) ----------

    def test_color_list_has_presets_and_custom(self, qapp):
        dlg = WaypointEditDialog(None, WptRecord.from_line(WPT_LINE_1))

        items = [dlg.color_combo.itemText(i)
                 for i in range(dlg.color_combo.count())]

        assert items == ['Красный', 'Морской волны', 'Зелёный', 'Жёлтый',
                         'Кастомный…']

    def test_color_is_prefilled_from_background(self, qapp):
        """Основной цвет — цвет фона, поле 9 (COLORREF: BGR, а не RGB)."""
        dlg = WaypointEditDialog(None, WptRecord.from_line(WPT_LINE_1))

        assert dlg._color == QColor(255, 0, 0)
        assert dlg.color_combo.currentText() == 'Красный'

    def test_preset_color_is_selected(self, qapp):
        record = WptRecord.from_line(WPT_LINE_1)
        record.fields[9] = '65535'          # жёлтый

        dlg = WaypointEditDialog(None, record)

        assert dlg.color_combo.currentText() == 'Жёлтый'
        assert dlg._color == QColor(255, 255, 0)

    def test_missing_color_means_default(self, qapp):
        record = WptRecord.from_line(WPT_LINE_1)
        record.fields[9] = ''

        dlg = WaypointEditDialog(None, record)

        assert dlg._color is None
        assert dlg.color_combo.currentIndex() == -1

    def test_choosing_preset_sets_color(self, qapp):
        record = WptRecord.from_line(WPT_LINE_1)
        dlg = WaypointEditDialog(None, record)

        dlg.color_combo.setCurrentIndex(1)      # Морской волны

        assert dlg._color == QColor(0, 255, 255)
        dlg._apply()
        assert record.fields[9] == str(16776960)

    def test_custom_color_selects_custom_item(self, qapp, monkeypatch):
        monkeypatch.setattr(gui.QColorDialog, 'getColor',
                            lambda *a, **kw: QColor(1, 2, 3))
        dlg = WaypointEditDialog(None, WptRecord.from_line(WPT_LINE_1))

        dlg.color_combo.setCurrentIndex(dlg._custom_index())

        assert dlg._color == QColor(1, 2, 3)
        assert dlg.color_combo.currentText() == 'Кастомный (#010203)'

    def test_custom_cancel_restores_previous_item(self, qapp, monkeypatch):
        monkeypatch.setattr(gui.QColorDialog, 'getColor',
                            lambda *a, **kw: QColor())
        dlg = WaypointEditDialog(None, WptRecord.from_line(WPT_LINE_1))

        dlg.color_combo.setCurrentIndex(dlg._custom_index())

        assert dlg._color == QColor(255, 0, 0)
        assert dlg.color_combo.currentText() == 'Красный'

    def test_unknown_color_is_shown_as_custom(self, qapp):
        record = WptRecord.from_line(WPT_LINE_1)
        record.fields[9] = str(0x00FF00)        # салатовый, не из списка

        dlg = WaypointEditDialog(None, record)

        assert dlg.color_combo.currentText() == 'Кастомный (#00FF00)'

    def test_color_is_written_as_colorref(self, qapp):
        record = WptRecord.from_line(WPT_LINE_1)
        dlg = WaypointEditDialog(None, record)
        dlg._set_color(QColor(0, 255, 0))

        dlg._apply()

        # Зелёный в COLORREF — 0x0000FF00, а не 0x0000FF.
        assert record.fields[9] == str(0x00FF00)
        assert record.color == 0x00FF00

    def test_color_can_be_reset_to_default(self, qapp):
        record = WptRecord.from_line(WPT_LINE_1)
        dlg = WaypointEditDialog(None, record)
        dlg._set_color(None)

        dlg._apply()

        assert record.fields[9] == ''
        assert record.color == gui.DEFAULT
        assert dlg.color_combo.currentIndex() == -1

    def test_text_color_is_forced_black(self, qapp):
        """Цвет текста не редактируется: при сохранении он чёрный."""
        record = WptRecord.from_line(WPT_LINE_1)
        record.fields[8] = '128'
        dlg = WaypointEditDialog(None, record)

        dlg._apply()

        assert record.fore_color == 0
        assert record.fields[8] == '0'

    def test_text_color_already_black_keeps_raw(self, qapp):
        record = WptRecord.from_line(WPT_LINE_1)
        dlg = WaypointEditDialog(None, record)

        dlg._apply()

        assert record.raw == WPT_LINE_1

    def test_color_picker_sets_color(self, qapp, monkeypatch):
        monkeypatch.setattr(gui.QColorDialog, 'getColor',
                            lambda *a, **kw: QColor(1, 2, 3))
        dlg = WaypointEditDialog(None, WptRecord.from_line(WPT_LINE_1))

        dlg._pick_color()

        assert dlg._color == QColor(1, 2, 3)
        assert dlg.color_combo.currentText() == 'Кастомный (#010203)'

    def test_color_picker_cancel_keeps_color(self, qapp, monkeypatch):
        monkeypatch.setattr(gui.QColorDialog, 'getColor',
                            lambda *a, **kw: QColor())
        dlg = WaypointEditDialog(None, WptRecord.from_line(WPT_LINE_1))

        dlg._pick_color()

        assert dlg._color == QColor(255, 0, 0)
        assert dlg.color_combo.currentText() == 'Красный'

    def test_apply_without_changes_keeps_raw_line(self, qapp):
        record = WptRecord.from_line(WPT_LINE_1)
        dlg = WaypointEditDialog(None, record)

        dlg._apply()

        assert dlg.result is record
        assert record.raw == WPT_LINE_1

    def test_new_record_gets_all_fields(self, qapp):
        record = WptRecord.new(name='', lat=0.0, lon=0.0)
        dlg = WaypointEditDialog(None, record, is_new=True)
        dlg.name_edit.setText('Точка')
        dlg.lat_edit.setText('56.9')
        dlg.lon_edit.setText('60.8')

        dlg._apply()

        assert len(record.to_line().split(',')) == 25
        assert record.name == 'Точка'


@pytest.mark.story('Форматы координат')
class TestCoordFormats:
    @pytest.mark.parametrize('text, expected', [
        ('56.909284', 56.909284),
        ('56,909284', 56.909284),
        ('-56.909284', -56.909284),
        ('56° 54.557′', 56.90928333),
        ('56 54.557', 56.90928333),
        ('N 56 54.557', 56.90928333),
        ('56°54′33.4″', 56.90927778),
        ('56 54 33.4', 56.90927778),
        ('S 56 54 33.4', -56.90927778),
        ('56 54 33.4 W', -56.90927778),
    ])
    def test_parse_all_formats(self, text, expected):
        assert gui._parse_wpt_coord(text) == pytest.approx(expected, abs=1e-6)

    @pytest.mark.parametrize('text', ['', 'широта', '56 61', '56 54 61',
                                      '56 54 33 12'])
    def test_bad_coordinate_is_rejected(self, text):
        assert gui._parse_wpt_coord(text) is None

    def test_format_decimal(self):
        assert gui._format_wpt_coord(56.909284) == '56.909284'
        assert (gui._format_wpt_coord(-56.909284, gui.WPT_COORD_DECIMAL)
                == '-56.909284')

    def test_format_degrees_and_minutes(self):
        assert (gui._format_wpt_coord(56.909284, gui.WPT_COORD_DM)
                == '56° 54.557′')

    def test_format_degrees_minutes_seconds(self):
        assert (gui._format_wpt_coord(56.909284, gui.WPT_COORD_DMS)
                == '56° 54′ 33.4″')

    def test_format_rounds_up_minutes(self):
        """59.9999′ при округлении не должно остаться минутами."""
        assert (gui._format_wpt_coord(56.9999999, gui.WPT_COORD_DM)
                == '57° 0.000′')

    def test_dialog_has_three_formats(self, qapp):
        dlg = WaypointEditDialog(None, WptRecord.from_line(WPT_LINE_1))

        items = [dlg.format_combo.itemText(i)
                 for i in range(dlg.format_combo.count())]

        assert items == ['Десятичные градусы', 'Градусы и минуты',
                         'Градусы, минуты и секунды']
        assert dlg.lat_edit.text() == '56.100000'

    def test_switch_format_reformats_fields(self, qapp):
        dlg = WaypointEditDialog(None, WptRecord.from_line(WPT_LINE_1))

        dlg.format_combo.setCurrentIndex(gui.WPT_COORD_DM)

        assert dlg.lat_edit.text() == '56° 6.000′'
        assert dlg.lon_edit.text() == '60° 6.000′'

        dlg.format_combo.setCurrentIndex(gui.WPT_COORD_DMS)

        assert dlg.lat_edit.text() == '56° 6′ 0.0″'
        assert dlg.lon_edit.text() == '60° 6′ 0.0″'

    def test_placeholders_follow_format(self, qapp):
        dlg = WaypointEditDialog(None, WptRecord.from_line(WPT_LINE_1))

        dlg.format_combo.setCurrentIndex(gui.WPT_COORD_DM)

        assert '°' in dlg.lat_edit.placeholderText()

    def test_apply_accepts_degrees_minutes(self, qapp):
        record = WptRecord.from_line(WPT_LINE_1)
        dlg = WaypointEditDialog(None, record)
        dlg.lat_edit.setText('56° 54.557′')
        dlg.lon_edit.setText('E 60 49.105')

        dlg._apply()

        assert record.lat == pytest.approx(56.90928333, abs=1e-6)
        assert record.lon == pytest.approx(60.81841667, abs=1e-6)

    def test_apply_accepts_degrees_minutes_seconds(self, qapp):
        record = WptRecord.from_line(WPT_LINE_1)
        dlg = WaypointEditDialog(None, record)
        dlg.lat_edit.setText('56 54 33.4')
        dlg.lon_edit.setText('60 49 6.3 E')

        dlg._apply()

        assert record.lat == pytest.approx(56.90927778, abs=1e-6)
        assert record.lon == pytest.approx(60.81841667, abs=1e-6)

    def test_southern_hemisphere_is_negative(self, qapp):
        record = WptRecord.from_line(WPT_LINE_1)
        dlg = WaypointEditDialog(None, record)
        dlg.lat_edit.setText('S 56 54 33.4')

        dlg._apply()

        assert record.lat == pytest.approx(-56.90927778, abs=1e-6)

    def test_bad_minutes_are_rejected(self, qapp, msgbox):
        dlg = WaypointEditDialog(None, WptRecord.from_line(WPT_LINE_1))
        dlg.lat_edit.setText('56 61 0')

        dlg._apply()

        assert dlg.result is None
        assert 'critical' in msgbox.kinds


@pytest.mark.story('Редактор файла .wpt')
class TestWptEditorDialog:
    def test_loads_points_and_columns(self, qapp, tmp_path):
        dlg = WptEditorDialog(None, make_wpt(tmp_path))

        assert dlg.table.rowCount() == 2
        assert dlg.table.columnCount() == 6
        assert dlg.table.item(0, 1).text() == 'Первая'
        assert dlg.table.item(1, 1).text() == 'Вторая'
        assert dlg.title.text().startswith('Waypoints_20261030.wpt')

    def test_height_and_date_columns_are_gone(self, qapp, tmp_path):
        dlg = WptEditorDialog(None, make_wpt(tmp_path))

        headers = [dlg.table.horizontalHeaderItem(i).text()
                   for i in range(dlg.table.columnCount())]

        assert headers == ['№', 'Имя', 'Широта', 'Долгота', 'Цвет',
                           'Описание']

    def test_color_column_shows_color_name(self, qapp, tmp_path):
        """Готовый цвет выводится названием, а не кодом."""
        dlg = WptEditorDialog(None, make_wpt(tmp_path))

        assert dlg.table.item(0, 4).text() == 'Красный'
        assert not dlg.table.item(0, 4).icon().isNull()

    def test_default_color_column_is_empty(self, qapp, tmp_path):
        line = ('1,Первая,56.1,60.1,46325.2740203,0,1,3,0,,Комментарий,'
                '0,0,0,-777,6,0,17,0,10.0,2,,,,300')
        dlg = WptEditorDialog(None, make_wpt(tmp_path, lines=(line,)))

        assert dlg.table.item(0, 4).text() == '—'
        assert dlg.table.item(0, 4).toolTip().startswith('Цвет по умолчанию')

    def test_custom_color_column_keeps_hex(self, qapp, tmp_path):
        src = make_wpt(tmp_path)
        dlg = WptEditorDialog(None, src)
        dlg.records[0].color = 0x00FF00        # не из готовых

        dlg._refresh()

        assert dlg.table.item(0, 4).text() == '#00FF00'
        assert not dlg.table.item(0, 4).icon().isNull()

    # ---------- замена «с» на «c» в окне ----------

    def test_normalize_replaces_cyrillic_s(self, qapp, tmp_path, msgbox):
        line = ('1,Следы,56.1,60.1,46325.274,0,1,3,0,255,'
                'конфеты,0,0,0,-777,6,0,17,0,10.0,2,,,,300')
        src = make_wpt(tmp_path, lines=(line,))
        dlg = WptEditorDialog(None, src)

        dlg._normalize_text()

        assert dlg.table.item(0, 1).text() == 'Cледы'
        assert 'Заменено в точках: 1' in msgbox.texts()[-1]
        # Пока не нажали «Сохранить», файл не менялся.
        assert read_wpt(src)[0].name == 'Следы'

        dlg._save()

        assert read_wpt(src)[0].name == 'Cледы'

    def test_normalize_nothing_to_do_reports(self, qapp, tmp_path, msgbox):
        dlg = WptEditorDialog(None, make_wpt(tmp_path))

        dlg._normalize_text()

        assert msgbox.kinds == ['information']
        assert 'нет' in msgbox.texts()[0]

    def test_normalize_keeps_selection(self, qapp, tmp_path, msgbox):
        line_1 = ('1,Следы,56.1,60.1,46325.274,0,1,3,0,255,'
                  'конфеты,0,0,0,-777,6,0,17,0,10.0,2,,,,300')
        line_2 = ('2,Вторая,56.2,60.2,46325.274,0,1,3,0,255,'
                  'поворот,0,0,0,-777,6,0,17,0,10.0,2,,,,300')
        dlg = WptEditorDialog(None, make_wpt(tmp_path,
                                             lines=(line_1, line_2)))
        dlg.table.selectRow(1)

        dlg._normalize_text()

        assert dlg.table.currentRow() == 1
        assert dlg.table.item(1, 1).text() == 'Вторая'

    def test_empty_file_has_no_rows(self, qapp, tmp_path):
        dlg = WptEditorDialog(None, make_wpt(tmp_path, lines=()))

        assert dlg.table.rowCount() == 0
        assert dlg.records == []

    def test_add_appends_record(self, qapp, tmp_path, monkeypatch):
        class FakeEdit:
            def __init__(self, parent, record, is_new=False):
                record.name = 'Добавлена'
                record.lat = 56.3
                record.lon = 60.3
                self.result = record

            def exec(self):
                return gui.QDialog.Accepted

        monkeypatch.setattr(gui, 'WaypointEditDialog', FakeEdit)
        dlg = WptEditorDialog(None, make_wpt(tmp_path))

        dlg._add()

        assert dlg.table.rowCount() == 3
        assert dlg.records[-1].name == 'Добавлена'
        assert dlg.table.item(2, 1).text() == 'Добавлена'

    def test_cancelled_add_changes_nothing(self, qapp, tmp_path, monkeypatch):
        class FakeEdit:
            def __init__(self, parent, record, is_new=False):
                self.result = None

            def exec(self):
                return gui.QDialog.Rejected

        monkeypatch.setattr(gui, 'WaypointEditDialog', FakeEdit)
        dlg = WptEditorDialog(None, make_wpt(tmp_path))

        dlg._add()

        assert len(dlg.records) == 2

    def test_delete_removes_selected(self, qapp, tmp_path, msgbox):
        dlg = WptEditorDialog(None, make_wpt(tmp_path))
        dlg.table.selectRow(0)

        dlg._delete()

        assert len(dlg.records) == 1
        assert dlg.records[0].name == 'Вторая'
        assert 'question' in msgbox.kinds

    def test_delete_can_be_cancelled(self, qapp, tmp_path, msgbox):
        msgbox.answer = 'No'
        dlg = WptEditorDialog(None, make_wpt(tmp_path))
        dlg.table.selectRow(0)

        dlg._delete()

        assert len(dlg.records) == 2

    def test_move_changes_order(self, qapp, tmp_path):
        dlg = WptEditorDialog(None, make_wpt(tmp_path))
        dlg.table.selectRow(0)

        dlg._move(1)

        assert [record.name for record in dlg.records] == [
            'Вторая', 'Первая']
        assert dlg.table.currentRow() == 1

    def test_move_at_border_does_nothing(self, qapp, tmp_path):
        dlg = WptEditorDialog(None, make_wpt(tmp_path))
        dlg.table.selectRow(0)

        dlg._move(-1)

        assert [record.name for record in dlg.records] == [
            'Первая', 'Вторая']

    def test_save_writes_changes_to_file(self, qapp, tmp_path):
        src = make_wpt(tmp_path)
        dlg = WptEditorDialog(None, src)
        dlg.records[0].name = 'Добавлена'

        dlg._save()

        assert dlg.saved is True
        assert read_wpt(src)[0].name == 'Добавлена'
        assert src.read_bytes().startswith(WPT_HEADER.encode('cp1251'))

    def test_save_keeps_cp1251(self, qapp, tmp_path):
        src = make_wpt(tmp_path)
        dlg = WptEditorDialog(None, src)
        dlg.records[0].name = 'Ёлка'

        dlg._save()

        raw = src.read_bytes()
        assert not raw.startswith(b'\xef\xbb\xbf')
        assert 'Ёлка' in raw.decode('cp1251')

    def test_save_continues_numbering_untouched_lines(self, qapp, tmp_path):
        """Правка описания не меняет форматирование координат."""
        src = make_wpt(tmp_path)
        before = src.read_text(encoding='cp1251').splitlines()
        dlg = WptEditorDialog(None, src)
        dlg.records[1].description = 'Другое'

        dlg._save()

        after = src.read_text(encoding='cp1251').splitlines()
        assert after[4] == before[4]
        assert 'Другое' in after[5]

    def test_read_wpt_file_roundtrip_window(self, qapp, tmp_path):
        """Окно не теряет записи при открытии и сохранении без правок."""
        src = make_wpt(tmp_path)
        before = src.read_bytes()

        dlg = WptEditorDialog(None, src)
        dlg._save()

        assert src.read_bytes() == before
        assert len(read_wpt_file(src).records) == 2
