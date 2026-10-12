"""Тесты диалогов GUI (ok_helper.gui).

Функциональность: RenameDialog (F2), NewFolderDialog (Ctrl+N),
MoveDialog (Ctrl+X), SettingsDialog (F10), HelpDialog (F1).

Диалоги не показываются: модальные окна и exec() не вызываются —
проверяются поля, правила предзаполнения и результат _apply/_save.

Группировка в Allure:
  epic    = ok-helper-tui
  feature = Диалоги GUI
  story   = задаётся маркером @pytest.mark.story на каждом классе.
"""

from datetime import datetime
from pathlib import Path

import allure
import pytest

from ok_helper import gui
from ok_helper.gui import (
    HelpDialog,
    MoveDialog,
    NewFolderDialog,
    RenameDialog,
    SettingsDialog,
)


pytestmark = [
    pytest.mark.gui,
    pytest.mark.epic('ok-helper-tui'),
    pytest.mark.feature('Диалоги GUI'),
]


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
        assert dlg.edit.text() == '20260917_Babushka.gpx'
        assert dlg.result is None

    def test_suggestion_falls_back_to_current_name(self, qapp, tmp_path):
        p = tmp_path / '20260923_Ivanov.gpx'
        p.write_text('')

        dlg = RenameDialog(None, p)

        assert dlg.suggestion == '20260923_Ivanov.gpx'

    def test_path_is_kept(self, qapp, tmp_path):
        p = tmp_path / 'notes.txt'
        p.write_text('')

        assert RenameDialog(None, p).path == p

    def test_has_ctrl_t_action(self, qapp, tmp_path):
        p = tmp_path / 'x.gpx'
        p.write_text('')

        assert len(ctrl_t_actions(RenameDialog(None, p))) == 1

    def test_apply_sets_result(self, qapp, tmp_path):
        p = tmp_path / 'x.gpx'
        p.write_text('')
        dlg = RenameDialog(None, p)
        dlg.edit.setText('20260923_Ivanov.gpx')

        dlg._apply()

        assert dlg.result == '20260923_Ivanov.gpx'

    def test_apply_strips_whitespace(self, qapp, tmp_path):
        p = tmp_path / 'x.gpx'
        p.write_text('')
        dlg = RenameDialog(None, p)
        dlg.edit.setText('  20260923_Ivanov.gpx  ')

        dlg._apply()

        assert dlg.result == '20260923_Ivanov.gpx'

    def test_apply_ignores_empty_name(self, qapp, tmp_path):
        p = tmp_path / 'x.gpx'
        p.write_text('')
        dlg = RenameDialog(None, p)
        dlg.edit.setText('   ')

        dlg._apply()

        assert dlg.result is None

    # --- Ctrl+T ---

    @pytest.mark.smoke
    def test_translit_and_normalize(self, qapp, tmp_path):
        p = tmp_path / 'x.gpx'
        p.write_text('')
        dlg = RenameDialog(None, p)
        dlg.edit.setText('20260923_иванов.gpx')

        dlg._translit()

        assert dlg.edit.text() == '20260923_Ivanov.gpx'

    def test_translit_normalizes_number(self, qapp, tmp_path):
        p = tmp_path / 'x.gpx'
        p.write_text('')
        dlg = RenameDialog(None, p)
        dlg.edit.setText('20260923_lisa_1.gpx')

        dlg._translit()

        assert dlg.edit.text() == '20260923_Lisa01.gpx'

    def test_translit_without_underscore(self, qapp, tmp_path):
        p = tmp_path / 'x.gpx'
        p.write_text('')
        dlg = RenameDialog(None, p)
        dlg.edit.setText('Ёжик.gpx')

        dlg._translit()

        assert dlg.edit.text() == 'Yozhik.gpx'

    def test_translit_keeps_date_with_dashes(self, qapp, tmp_path):
        """Дата не в формате ГГГГММДД — только транслит."""
        p = tmp_path / 'x.gpx'
        p.write_text('')
        dlg = RenameDialog(None, p)
        dlg.edit.setText('2026-09-17_11-55_бабушка.gpx')

        dlg._translit()

        assert dlg.edit.text() == '2026-09-17_11-55_babushka.gpx'

    def test_translit_action_triggers_same_slot(self, qapp, tmp_path):
        p = tmp_path / 'x.gpx'
        p.write_text('')
        dlg = RenameDialog(None, p)
        dlg.edit.setText('бабушка.gpx')

        ctrl_t_actions(dlg)[0].trigger()

        assert dlg.edit.text() == 'babushka.gpx'


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
        assert dlg.tracks_cb.isChecked() is False
        assert dlg.with_10_tracks is False

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
        assert gui_config['theme'] == 'dark'
        assert gui_config['watch_downloads'] is True
        assert gui_config['help_visible_at_start'] is True
        assert gui_config['ftp_readonly'] is True
        assert gui_config['log_visible'] is False
        assert gui_config['blocking_errors'] is True

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
        'Ctrl+1', 'Ctrl+2',
    ])
    def test_help_lists_documented_hotkeys(self, key):
        """Справка должна перечислять клавиши из README."""
        assert key in HelpDialog.HELP_TEXT

    @pytest.mark.parametrize('fragment', [
        'ГГГГММДД', 'Waypoints_', '10-Tracks', 'ftp_readonly',
        'auto', 'light', 'dark', '✓', 'Ctrl+Shift+C',
        'скрыть панель журнала',
    ])
    def test_help_covers_documented_rules(self, fragment):
        assert fragment in HelpDialog.HELP_TEXT

    def test_help_mentions_authopodbstanovka(self):
        assert 'автоподстановка' in HelpDialog.HELP_TEXT.lower()

    def test_help_explains_double_click_on_file(self):
        """README: двойной клик по файлу — меню действий, а не открытие."""
        assert 'меню доступных действий' in HelpDialog.HELP_TEXT
