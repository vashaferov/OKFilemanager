"""Тесты главного окна (ok_helper.gui.MainWindow).

Функциональность: дерево и навигация, журнал и статус, создание папки,
переименование, перенос, копирование имени, автоправка и «Исправить
все», вкладка FTP (листинг, автоподстановка, удаление, загрузка,
режим эмуляции), watchdog и настройки.

Окно не показывается: проверяются слоты и обработчики напрямую.
FTP-задачи, watchdog-наблюдатели и модальные окна подменяются.

Группировка в Allure:
  epic    = ok-helper-tui
  feature = Главное окно
  story   = задаётся маркером @pytest.mark.story на каждом классе.
"""

import threading
import types
from pathlib import Path

import allure
import pytest
from PySide6.QtCore import QEvent, QItemSelectionModel, QPoint

from ok_helper import gui


pytestmark = [
    pytest.mark.gui,
    pytest.mark.epic('ok-helper-tui'),
    pytest.mark.feature('Главное окно'),
]


# ─────────────────────────────────────────────────────────────────
# Вспомогательные подмены
# ─────────────────────────────────────────────────────────────────

class FakeSignal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def emit(self, *args):
        for slot in list(self.slots):
            slot(*args)


class FakeTask:
    """Общая заготовка для FtpListTask / FtpUploadTask / FtpDeleteTask."""

    def __init__(self, signal_name, recorder, kind):
        self._signal = FakeSignal()
        setattr(self, signal_name, self._signal)
        self._recorder = recorder
        self._kind = kind

    def start(self):
        self._recorder.started.append(self._kind)

    def emit(self, *args):
        self._signal.emit(*args)


class FakeObserver:
    instances = []

    def __init__(self):
        self.handler = None
        self.path = None
        self.recursive = None
        self.started = False
        self.stopped = False
        self.joined = False
        FakeObserver.instances.append(self)

    def schedule(self, handler, path, recursive=False):
        self.handler = handler
        self.path = path
        self.recursive = recursive

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def join(self, timeout=None):
        self.joined = True


class TaskRecorder:
    def __init__(self):
        self.list = []
        self.upload = []
        self.delete = []
        self.started = []
        self.tasks = []

    def last(self, kind):
        return [t for t in self.tasks if t._kind == kind][-1]


@pytest.fixture
def observers(monkeypatch):
    FakeObserver.instances = []
    monkeypatch.setattr(gui, 'Observer', FakeObserver)
    return FakeObserver


@pytest.fixture
def tasks(monkeypatch):
    """Подменяет три фоновые FTP-задачи: сеть не трогается."""
    rec = TaskRecorder()

    class ListTask(FakeTask):
        def __init__(self, parent, host, port, user, password, base, folder):
            super().__init__('done', rec, 'list')
            rec.list.append((host, port, user, password, base, folder))
            rec.tasks.append(self)

    class UploadTask(FakeTask):
        def __init__(self, parent, host, port, user, password, base, folder,
                     src, readonly, log_cb):
            super().__init__('finished', rec, 'upload')
            rec.upload.append((host, port, user, password, base, folder,
                               src, readonly))
            rec.tasks.append(self)

    class DeleteTask(FakeTask):
        def __init__(self, parent, host, port, user, password, base, folder,
                     filename):
            super().__init__('finished', rec, 'delete')
            rec.delete.append((host, port, user, password, base, folder,
                               filename))
            rec.tasks.append(self)

    monkeypatch.setattr(gui, 'FtpListTask', ListTask)
    monkeypatch.setattr(gui, 'FtpUploadTask', UploadTask)
    monkeypatch.setattr(gui, 'FtpDeleteTask', DeleteTask)
    return rec


@pytest.fixture
def timers(monkeypatch):
    """Перехватывает QTimer.singleShot."""
    import types

    rec = types.SimpleNamespace(calls=[])

    class FakeTimer:
        @staticmethod
        def singleShot(ms, callback=None):
            rec.calls.append((ms, callback))

    monkeypatch.setattr(gui, 'QTimer', FakeTimer)
    return rec


@pytest.fixture
def files(gui_config):
    """Набор файлов в корне дерева (создаётся до окна)."""
    root = gui_config['root_dir_resolved'].resolve()
    moscow = root / '2026-09-23_Moscow'
    moscow.mkdir()
    (moscow / '100m.gpx').write_text('')
    (moscow / '10-Tracks').mkdir()
    (root / '2026-09-17_11-55_бабушка.gpx').write_text('x' * 1024)
    (root / 'track.plt').write_text('')
    return root


class FakeMenu:
    """Немодальная замена QMenu: запоминает пункты, ничего не показывает."""

    instances = []

    def __init__(self, parent=None):
        self.items = []
        self.signals = {}
        self.separators = 0
        self.executed = False
        FakeMenu.instances.append(self)

    def addAction(self, text, slot=None):
        self.items.append(text)
        signal = FakeSignal()
        if slot is not None:
            signal.connect(slot)
        self.signals[text] = signal
        return types.SimpleNamespace(triggered=signal)

    def addSeparator(self):
        self.separators += 1

    def exec(self, *args):
        self.executed = True
        return None


@pytest.fixture
def menus(monkeypatch):
    FakeMenu.instances = []
    monkeypatch.setattr(gui, 'QMenu', FakeMenu)
    return FakeMenu


@pytest.fixture
def window(qapp, gui_config, observers, msgbox, files):
    """Окно строится уже по готовому дереву файлов."""
    win = gui.MainWindow(gui_config, gui_config['root_dir_resolved'])
    yield win
    destroy(qapp, win)


def destroy(qapp, win):
    """Реально уничтожает окно: deleteLater без цикла событий не срабатывает.

    Иначе окна копятся, и каждый QApplication.setStyleSheet в apply_theme
    переполировывает всё больше виджетов — прогон вырождается в минуты.
    """
    win._stop_watch()
    win._stop_downloads_watch()
    win.close()
    win.setParent(None)
    win.deleteLater()
    qapp.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()


def stub_dialog(monkeypatch, attr, result=None, accepted=True,
                with_10_tracks=False):
    class Stub:
        def __init__(self, parent, *a, **kw):
            self.result = result
            self.with_10_tracks = with_10_tracks

        def exec(self):
            return gui.QDialog.Accepted if accepted else gui.QDialog.Rejected

    monkeypatch.setattr(gui, attr, Stub)


def log_text(win):
    return win.log_edit.toPlainText()


def select(win, path):
    item = win.tree_model.find_item(path)
    assert item is not None, f'нет элемента для {path}'
    win.tree_view.setCurrentIndex(win.tree_model.indexFromItem(item))
    return item


# ─────────────────────────────────────────────────────────────────
# 1. Построение окна
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Построение окна')
class TestConstruction:
    def test_title(self, window):
        assert window.windowTitle() == 'ok-helper'

    @pytest.mark.smoke
    def test_root_is_resolved(self, window, gui_config):
        assert window.tree_root == \
            gui_config['root_dir_resolved'].resolve()

    def test_path_edit_shows_root(self, window):
        assert window.path_edit.text() == str(window.tree_root)

    @pytest.mark.smoke
    def test_two_tabs(self, window):
        assert window.tabs.count() == 2
        assert [window.tabs.tabText(i) for i in range(2)] == \
            ['Файлы', 'FTP']
        assert window.tabs.currentIndex() == 0

    @pytest.mark.smoke
    def test_follow_checkbox_checked_by_default(self, window):
        assert window.follow_checkbox.isChecked() is True

    def test_tree_model_root_set(self, window):
        assert window.tree_model.root_path == window.tree_root
        assert window.tree_model.rowCount() == 1

    def test_tree_editing_disabled(self, window):
        assert window.tree_view.editTriggers() == gui.QTreeView.NoEditTriggers

    def test_ftp_models_start_empty(self, window):
        assert window.ftp_model.rowCount() == 0
        assert window.ftp_model._entries == []
        assert window.ftp_path_edit.text() == ''

    def test_status_bar_has_counts(self, window):
        """Счётчики живут бейджами в статусбаре, а не в сообщении."""
        assert 'Файлов:' in window.status_line()
        assert 'ошибок:' in window.status_line()
        assert 'залито на FTP:' in window.status_line()

    def test_double_click_signal_is_wired(self, window, files):
        """Сигнал doubleClicked действительно ведёт в слот окна."""
        item = window.tree_model.item(0).child(0)
        assert item.text() == '2026-09-23_Moscow'
        assert item.child(0).text() == gui.STUB_TEXT

        window.tree_view.doubleClicked.emit(
            window.tree_model.indexFromItem(item))

        assert item.child(0).text() != gui.STUB_TEXT

    def test_set_tree_root_without_save_does_not_write_file(
            self, window, gui_config):
        assert not Path(gui_config['_path']).exists()

    def test_close_stops_observers(self, window):
        window.close()

        assert window.observer is None
        assert window.downloads_observer is None


# ─────────────────────────────────────────────────────────────────
# 1b. Кнопки действия в шапке зависят от активной вкладки
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Кнопки шапки по вкладке')
class TestTabToolbar:
    @staticmethod
    def _visible(window, group):
        return [a.text() for a in window._toolbar_groups[group]
                if a.isVisible() and a.text()]

    def test_files_tab_shows_file_actions(self, window):
        window._update_toolbar_for_tab(0)

        visible = self._visible(window, 'files')
        for label in ('Вверх', 'Обновить', 'Исправить все', 'Папка',
                      'Переименовать', 'Переместить', 'Копировать имя',
                      'На FTP', 'В проводнике', 'Проверить загрузки'):
            assert label in visible
        assert self._visible(window, 'ftp') == []

    def test_ftp_tab_shows_ftp_actions(self, window):
        window._update_toolbar_for_tab(1)

        visible = self._visible(window, 'ftp')
        for label in ('Подключиться', 'Обновить', 'Копировать имя',
                      'Загрузить сюда…', 'Удалить'):
            assert label in visible
        assert self._visible(window, 'files') == []

    def test_log_action_is_common(self, window):
        window._update_toolbar_for_tab(0)
        assert window.log_action.isVisible() is True

        window._update_toolbar_for_tab(1)
        assert window.log_action.isVisible() is True

    def test_switching_tab_switches_toolbar(self, window, monkeypatch):
        monkeypatch.setattr(gui.MainWindow, 'ftp_load',
                            lambda self, folder: None)

        window.tabs.setCurrentIndex(1)
        assert self._visible(window, 'files') == []
        assert 'Удалить' in self._visible(window, 'ftp')

        window.tabs.setCurrentIndex(0)
        assert self._visible(window, 'ftp') == []
        assert 'Папка' in self._visible(window, 'files')

    def test_delete_action_follows_readonly(self, window):
        # Кнопка видна только на вкладке FTP; у скрытого QAction
        # isEnabled() всегда false, поэтому сначала показываем группу.
        window._update_toolbar_for_tab(1)

        window.config['ftp_readonly'] = True
        window._sync_ftp_actions()

        assert window.ftp_delete_action.isEnabled() is False
        assert window.btn_ftp_delete.isEnabled() is False

        window.config['ftp_readonly'] = False
        window._sync_ftp_actions()

        assert window.ftp_delete_action.isEnabled() is True
        assert window.btn_ftp_delete.isEnabled() is True


# ─────────────────────────────────────────────────────────────────
# 2. Выделение и навигация
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Навигация по дереву')
class TestNavigation:
    def test_selected_path_none_without_selection(self, window):
        assert window._selected_path() is None

    def test_selected_path_after_select(self, window, files):
        target = files / '2026-09-17_11-55_бабушка.gpx'
        select(window, target)

        assert window._selected_path() == target

    def test_selected_path_for_directory(self, window, files):
        select(window, files / '2026-09-23_Moscow')

        assert window._selected_path() == files / '2026-09-23_Moscow'

    def test_path_from_invalid_index(self, window):
        assert window._path_from_index(gui.QModelIndex()) is None

    @pytest.mark.smoke
    def test_set_tree_root_changes_root(self, window, files):
        sub = files / '2026-09-23_Moscow'
        before = window.config['root_dir']
        window.set_tree_root(sub)

        assert window.tree_root == sub
        assert window.watch_dir == sub
        assert window.path_edit.text() == str(sub)
        # «Корневая папка» из настроек при навигации не меняется.
        assert window.config['root_dir'] == before

    def test_set_tree_root_ignores_files(self, window, files):
        target = files / 'track.plt'
        before = window.tree_root

        window.set_tree_root(target)

        assert window.tree_root == before

    def test_set_tree_root_does_not_save_config(self, window, files,
                                                monkeypatch):
        """Навигация не переписывает конфиг (ошибка: подменяла root_dir)."""
        saved = []
        monkeypatch.setattr(gui, 'save_config',
                            lambda cfg: saved.append(dict(cfg)))

        window.set_tree_root(files / '2026-09-23_Moscow')

        assert saved == []
        assert window.config['root_dir'] == str(files)

    def test_set_tree_root_does_not_write_config_file(self, window, files,
                                                      gui_config):
        window.set_tree_root(files / '2026-09-23_Moscow')

        assert not Path(gui_config['_path']).exists()

    def test_set_tree_root_restarts_watcher(self, window, files, observers):
        before = len(observers.instances)
        window.set_tree_root(files / '2026-09-23_Moscow')

        assert len(observers.instances) == before + 1
        assert observers.instances[-1].started is True

    def test_on_up_goes_to_parent(self, window, files):
        window.set_tree_root(files / '2026-09-23_Moscow')
        window.on_up()

        assert window.tree_root == files

    def test_on_up_at_root_does_nothing(self, window):
        window.tree_root = Path(window.tree_root.anchor)
        before = window.tree_root

        window.on_up()

        assert window.tree_root == before

    def test_on_path_entered_valid(self, window, files):
        sub = files / '2026-09-23_Moscow'
        window.path_edit.setText(str(sub))

        window.on_path_entered()

        assert window.tree_root == sub

    def test_on_path_entered_invalid(self, window, msgbox, tmp_path):
        before = window.tree_root
        window.path_edit.setText(str(tmp_path / 'нет-такой'))

        window.on_path_entered()

        assert window.tree_root == before
        assert msgbox.kinds == ['critical']
        assert 'Не директория' in msgbox.texts()[0]

    def test_on_browse_dir_cancel(self, window, monkeypatch):
        class FakeFileDialog:
            @staticmethod
            def getExistingDirectory(*a, **kw):
                return ''

        monkeypatch.setattr(gui, 'QFileDialog', FakeFileDialog)
        before = window.tree_root

        window.on_browse_dir()

        assert window.tree_root == before

    def test_on_browse_dir_selected(self, window, files, monkeypatch):
        sub = files / '2026-09-23_Moscow'

        class FakeFileDialog:
            @staticmethod
            def getExistingDirectory(*a, **kw):
                return str(sub)

        monkeypatch.setattr(gui, 'QFileDialog', FakeFileDialog)
        window.on_browse_dir()

        assert window.tree_root == sub

    def test_on_tree_expanded_tracks_paths(self, window, files):
        moscow = files / '2026-09-23_Moscow'
        item = window.tree_model.find_item(moscow)
        window.tree_model.set_root(files)
        item = window.tree_model.find_item(moscow)
        window.tree_model.expanded_paths.discard(moscow)

        window.on_tree_expanded(window.tree_model.indexFromItem(item))

        assert moscow in window.tree_model.expanded_paths
        assert item.child(0).text() != gui.STUB_TEXT

    def test_on_tree_collapsed_removes_path(self, window, files):
        select(window, files / '2026-09-23_Moscow')
        item = window.tree_model.find_item(files / '2026-09-23_Moscow')
        index = window.tree_model.indexFromItem(item)

        window.on_tree_collapsed(index)

        assert (files / '2026-09-23_Moscow') not in \
            window.tree_model.expanded_paths

    def test_on_tree_expanded_ignores_invalid(self, window):
        window.on_tree_expanded(gui.QModelIndex())
        window.on_tree_collapsed(gui.QModelIndex())

    def test_on_tree_double_click_directory_loads(self, window, files):
        item = window.tree_model.find_item(files / '2026-09-23_Moscow')

        window.on_tree_double_click(window.tree_model.indexFromItem(item))

        assert [item.child(r).text() for r in range(item.rowCount())] == \
            ['10-Tracks', '100m.gpx']

    def test_on_tree_double_click_file_opens_menu(self, window, files,
                                                  monkeypatch, menus):
        """README: двойной клик по файлу — меню доступных действий."""
        opened = []
        monkeypatch.setattr(gui.os, 'startfile', opened.append, raising=False)
        target = files / 'track.plt'
        item = window.tree_model.find_item(target)

        window.on_tree_double_click(window.tree_model.indexFromItem(item))

        assert menus.instances, 'меню не было создано'
        menu = menus.instances[-1]
        assert menu.executed is True
        assert menu.items == ['Копировать имя', 'Копировать путь',
                              'Показать в проводнике', 'Переименовать…',
                              'Переместить…', 'Открыть']
        assert opened == []                 # сам файл не открывается
        assert window._selected_path() == target

    def test_double_click_file_menu_open_action_opens(self, window, files,
                                                      monkeypatch, menus):
        opened = []
        monkeypatch.setattr(gui.os, 'startfile', opened.append, raising=False)
        target = files / 'track.plt'
        item = window.tree_model.find_item(target)

        window.on_tree_double_click(window.tree_model.indexFromItem(item))
        menus.instances[-1].signals['Открыть'].emit()

        assert opened == [str(target)]
        assert '[ОТКР] track.plt' in log_text(window)

    def test_double_click_file_can_copy_name(self, window, files,
                                             monkeypatch, menus):
        copied = []
        monkeypatch.setattr(gui, 'copy_to_clipboard',
                            lambda text: copied.append(text) or True)
        item = window.tree_model.find_item(files / 'track.plt')

        window.on_tree_double_click(window.tree_model.indexFromItem(item))
        menus.instances[-1].signals['Копировать имя'].emit()

        assert copied == ['track.plt']

    def test_on_tree_double_click_directory_has_no_menu(self, window, files,
                                                        menus):
        item = window.tree_model.find_item(files / '2026-09-23_Moscow')

        window.on_tree_double_click(window.tree_model.indexFromItem(item))

        assert menus.instances == []
        assert [item.child(r).text() for r in range(item.rowCount())] == \
            ['10-Tracks', '100m.gpx']

    def test_on_tree_double_click_invalid_index(self, window, menus):
        window.on_tree_double_click(gui.QModelIndex())

        assert menus.instances == []

    def test_on_tree_activated_makes_root(self, window, files):
        item = window.tree_model.find_item(files / '2026-09-23_Moscow')

        window.on_tree_activated(window.tree_model.indexFromItem(item))

        assert window.tree_root == files / '2026-09-23_Moscow'

    def test_on_tree_activated_file_does_nothing(self, window, files):
        select(window, files / 'track.plt')
        item = window.tree_model.find_item(files / 'track.plt')
        before = window.tree_root

        window.on_tree_activated(window.tree_model.indexFromItem(item))

        assert window.tree_root == before

    def test_open_path_error_is_logged(self, window, files, monkeypatch):
        def boom(_path):
            raise OSError('нет ассоциации')

        monkeypatch.setattr(gui.os, 'startfile', boom, raising=False)

        window.open_path(files / 'track.plt')

        assert '[ОШБ] Открыть не удалось' in log_text(window)

    def test_on_refresh_scans(self, window, files):
        window.on_refresh()

        assert '[КАТАЛОГ]' in log_text(window)


# ─────────────────────────────────────────────────────────────────
# 2a. Выделение в дереве и его сохранение
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Выделение в дереве')
class TestTreeSelection:
    """Перестройка дерева не должна терять выделенный элемент.

    FileTreeModel.refresh() пересоздаёт все элементы, и без
    восстановления файл выглядел выделенным, но действия («В
    проводнике», F2, Ctrl+Y) срабатывали только после повторного клика.
    """

    def test_select_path_sets_current_item(self, window, files):
        target = files / 'track.plt'

        assert window._select_path(target) is True
        assert window._selected_path() == target
        index = window.tree_view.currentIndex()
        assert index.isValid()
        assert index in window.tree_view.selectionModel().selectedIndexes()

    def test_select_path_of_missing_item(self, window, files):
        assert window._select_path(files / 'нет-такого.gpx') is False
        assert window._select_path(None) is False

    @pytest.mark.smoke
    def test_rebuild_keeps_selection(self, window, files):
        target = files / '2026-09-17_11-55_бабушка.gpx'
        select(window, target)

        window.rebuild_tree()

        assert window._selected_path() == target
        assert window.tree_view.currentIndex().isValid()
        assert window.tree_view.selectionModel().selectedIndexes()

    def test_rebuild_keeps_directory_selection(self, window, files):
        target = files / '2026-09-23_Moscow'
        select(window, target)

        window.rebuild_tree()

        assert window._selected_path() == target

    def test_rebuild_keeps_selection_in_nested_folder(self, window, files):
        """Выделение внутри раскрытой вложенной папки тоже сохраняется."""
        target = files / '2026-09-23_Moscow' / '100m.gpx'
        select(window, target)

        window.rebuild_tree()

        assert window._selected_path() == target

    def test_rebuild_after_file_disappears(self, window, files):
        """Если выбранный файл исчез, выделения просто нет."""
        target = files / 'track.plt'
        select(window, target)
        target.unlink()

        window.rebuild_tree()

        assert window._selected_path() is None

    def test_selected_path_falls_back_to_selection_model(self, window, files):
        """Запасной путь: выделение есть, а текущий индекс пуст.

        Именно так ведёт себя Qt: после перестройки модели текущий
        индекс может очиститься, а выделенная строка остаться.
        """
        target = files / 'track.plt'
        item = window.tree_model.find_item(target)
        index = window.tree_model.indexFromItem(item)
        window.tree_view.setCurrentIndex(gui.QModelIndex())
        window.tree_view.selectionModel().select(
            index, QItemSelectionModel.Select | QItemSelectionModel.Rows)

        assert window.tree_view.currentIndex().isValid() is False
        assert window._selected_path() == target

    def test_selected_path_without_selection(self, window, files):
        window.tree_view.clearSelection()
        window.tree_view.setCurrentIndex(gui.QModelIndex())

        assert window._selected_path() is None

    def test_rename_selects_new_name(self, window, files, monkeypatch):
        src = files / 'track.plt'
        select(window, src)
        stub_dialog(monkeypatch, 'RenameDialog', result='renamed.plt')

        window.on_rename()

        assert window._selected_path() == files / 'renamed.plt'

    def test_apply_fix_selects_new_name(self, window, files):
        src = files / '2026-09-17_11-55_бабушка.gpx'
        select(window, src)

        window.on_apply_fix()

        assert window._selected_path() == files / '20260917_Babushka.gpx'

    def test_fix_all_selects_last_fixed(self, window, files, msgbox):
        select(window, files / '2026-09-17_11-55_бабушка.gpx')

        window.on_fix_all()

        assert window._selected_path() == files / '20260917_Babushka.gpx'

    def test_selection_survives_refresh_after_new_file(self, window, files,
                                                       qapp):
        """Сценарий пользователя: файл скачался -> дерево перестроилось."""
        target = files / 'track.plt'
        select(window, target)
        (files / 'новая_папка').mkdir()
        qapp.processEvents()

        window._do_refresh()

        assert window._selected_path() == target


# ─────────────────────────────────────────────────────────────────
# 2b. Контекстное меню дерева (правый клик)
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Контекстное меню')
class TestContextMenu:
    def open_menu(self, window, monkeypatch, menus, path):
        """Открывает меню для элемента path (без реального показа)."""
        item = window.tree_model.find_item(path)
        index = window.tree_model.indexFromItem(item)
        monkeypatch.setattr(window.tree_view, 'indexAt', lambda pos: index)

        window.on_tree_context_menu(QPoint(0, 0))

        assert menus.instances, 'меню не было создано'
        return menus.instances[-1]

    @pytest.mark.smoke
    def test_directory_menu(self, window, files, monkeypatch, menus):
        menu = self.open_menu(window, monkeypatch, menus,
                              files / '2026-09-23_Moscow')

        assert menu.items == ['Сделать корнем дерева', 'Раскрыть / скрыть',
                              'Копировать имя', 'Копировать путь',
                              'Показать в проводнике']
        assert menu.separators == 1
        assert menu.executed is True

    @pytest.mark.smoke
    def test_file_menu(self, window, files, monkeypatch, menus):
        menu = self.open_menu(window, monkeypatch, menus,
                              files / 'track.plt')

        assert menu.items == ['Копировать имя', 'Копировать путь',
                              'Показать в проводнике', 'Переименовать…',
                              'Переместить…', 'Открыть']

    def test_menu_without_item(self, window, monkeypatch, menus):
        monkeypatch.setattr(window.tree_view, 'indexAt',
                            lambda pos: gui.QModelIndex())

        window.on_tree_context_menu(QPoint(0, 0))

        assert menus.instances == []

    def test_menu_selects_item(self, window, files, monkeypatch, menus):
        target = files / 'track.plt'
        self.open_menu(window, monkeypatch, menus, target)

        assert window._selected_path() == target

    def test_make_root_action(self, window, files, monkeypatch, menus):
        moscow = files / '2026-09-23_Moscow'
        menu = self.open_menu(window, monkeypatch, menus, moscow)

        menu.signals['Сделать корнем дерева'].emit()

        assert window.tree_root == moscow

    def test_copy_name_action(self, window, files, monkeypatch, menus):
        copied = []
        monkeypatch.setattr(gui, 'copy_to_clipboard',
                            lambda text: copied.append(text) or True)
        menu = self.open_menu(window, monkeypatch, menus,
                              files / 'track.plt')

        menu.signals['Копировать имя'].emit()

        assert copied == ['track.plt']

    def test_open_action(self, window, files, monkeypatch, menus):
        opened = []
        monkeypatch.setattr(gui.os, 'startfile', opened.append, raising=False)
        target = files / 'track.plt'
        menu = self.open_menu(window, monkeypatch, menus, target)

        menu.signals['Открыть'].emit()

        assert opened == [str(target)]

    def test_copy_path_action(self, window, files, monkeypatch, menus):
        copied = []
        monkeypatch.setattr(gui, 'copy_to_clipboard',
                            lambda text: copied.append(text) or True)
        target = files / 'track.plt'
        menu = self.open_menu(window, monkeypatch, menus, target)

        menu.signals['Копировать путь'].emit()

        assert copied == [str(target)]
        assert '[БУФЕР] Путь скопирован' in log_text(window)

    def test_reveal_action(self, window, files, monkeypatch, menus):
        revealed = []
        monkeypatch.setattr(gui, 'reveal_in_file_manager',
                            lambda path: revealed.append(path) or True)
        target = files / 'track.plt'
        menu = self.open_menu(window, monkeypatch, menus, target)

        menu.signals['Показать в проводнике'].emit()

        assert revealed == [target]
        assert '[ОТКР] Проводник' in log_text(window)


# ─────────────────────────────────────────────────────────────────
# 3. Журнал и статус
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Журнал и статус')
class TestLogAndStatus:
    def test_log_appends_line_with_timestamp(self, window):
        window.log('привет', 'info')

        text = log_text(window)
        assert 'привет' in text
        assert text.split('  ')[0].count(':') == 2   # ЧЧ:ММ:СС

    @pytest.mark.parametrize('level', ['info', 'ok', 'warn', 'error', 'cfg'])
    def test_all_levels_are_logged(self, window, level):
        window.log(f'уровень {level}', level)

        assert f'уровень {level}' in log_text(window)

    def test_unknown_level_falls_back(self, window):
        window.log('неизвестный уровень', 'не-такой')

        assert 'неизвестный уровень' in log_text(window)

    def test_log_from_worker_thread_is_deferred(self, window, timers):
        thread = threading.Thread(target=window.log, args=('из потока',))
        thread.start()
        thread.join()

        assert timers.calls, 'log из другого потока должен уйти в QTimer'
        assert 'из потока' not in log_text(window)

    def test_clear_log(self, window):
        window.log('что-то')
        window.on_clear_log()

        assert 'Журнал очищен' in log_text(window)
        assert 'что-то' not in log_text(window)

    def test_set_status(self, window):
        window.set_status('Готово к работе')

        assert window.status_label.text() == 'Готово к работе'

    @pytest.mark.smoke
    def test_scan_dir_reports_all_ok(self, window, tmp_path):
        empty = tmp_path / 'clean'
        empty.mkdir()

        window.scan_dir(empty)

        assert '[КАТАЛОГ] Все имена корректны' in log_text(window)

    def test_scan_dir_reports_problems(self, window, files):
        window.scan_dir(files)

        text = log_text(window)
        assert '[!] 2026-09-17_11-55_бабушка.gpx' in text
        assert '[КАТАЛОГ] Проблем: 2' in text

    def test_scan_dir_ignores_non_directory(self, window, tmp_path):
        f = tmp_path / 'x.txt'
        f.write_text('')

        window.scan_dir(f)      # не должно падать

    @pytest.mark.smoke
    def test_update_status_counts(self, window, files):
        select(window, files / '2026-09-17_11-55_бабушка.gpx')
        window.update_status()

        assert 'Файлов:' in window.status_line()
        assert 'ошибок:' in window.status_line()
        assert 'залито на FTP:' in window.status_line()
        assert str(window.tree_root) in window.path_label.text()

    def test_iter_loaded_files_only_loaded(self, window, files):
        names = {p.name for p in window._iter_loaded_files()}

        assert 'track.plt' in names
        assert '100m.gpx' not in names   # папка ещё не раскрыта

        select(window, files / '2026-09-23_Moscow' / '100m.gpx')
        names = {p.name for p in window._iter_loaded_files()}
        assert '100m.gpx' in names

    def test_is_uploaded_to_ftp_predicate(self, window, files):
        window.tree_model.set_ftp_sync('2026-09-23_Moscow', {'100m.gpx'})

        assert window._is_uploaded_to_ftp(
            files / '2026-09-23_Moscow' / '100m.gpx') is True
        assert window._is_uploaded_to_ftp(files / 'track.plt') is False


# ─────────────────────────────────────────────────────────────────
# 3b. Состояние интерфейса: геометрия, журнал, плашка
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Состояние интерфейса')
class TestUiState:
    def test_save_writes_geometry_and_log_height(self, window, files):
        window.resize(1000, 700)
        window._save_ui_state()

        geometry = window.config['window_geometry']
        assert window.config['tree_column_widths'] != ''
        # Ключ прежних версий больше не используется и обнуляется.
        assert window.config['splitter_state'] == ''

        width, height, x, y = (int(part) for part in geometry.split(','))
        assert width > 0 and height > 0
        assert x == window.pos().x() and y == window.pos().y()

    def test_geometry_round_trip_restores_size(self, qapp, gui_config, files,
                                               observers, msgbox):
        """Новый запуск открывается в сохранённых размерах."""
        win = gui.MainWindow(gui_config, gui_config['root_dir_resolved'])
        win.resize(1000, 700)
        win._save_ui_state()
        destroy(qapp, win)

        restored = gui.MainWindow(gui_config, gui_config['root_dir_resolved'])
        try:
            assert restored.width() == 1000
            assert restored.height() == 700
        finally:
            destroy(qapp, restored)

    @pytest.mark.parametrize('raw', ['', 'мусор', '1000,700', '0,0,0,0',
                                     'a,b,c,d'])
    def test_broken_geometry_falls_back(self, qapp, gui_config, files,
                                        observers, msgbox, raw):
        """Испорченная запись не должна ломать запуск окна."""
        gui_config['window_geometry'] = raw

        win = gui.MainWindow(gui_config, gui_config['root_dir_resolved'])
        try:
            assert win.width() == 1280
            assert win.height() == 800
        finally:
            destroy(qapp, win)

    def test_log_dock_height_round_trip(self, qapp, gui_config, files,
                                        observers, msgbox):
        """Высота панели журнала переживает перезапуск."""
        win = gui.MainWindow(gui_config, gui_config['root_dir_resolved'])
        win.show()
        qapp.processEvents()
        win.resizeDocks([win.log_dock], [260], gui.Qt.Vertical)
        qapp.processEvents()
        win._save_ui_state()
        saved = gui_config['log_dock_height']
        assert saved > 0
        destroy(qapp, win)

        restored = gui.MainWindow(gui_config, gui_config['root_dir_resolved'])
        try:
            assert restored.config['log_dock_height'] == saved
            assert restored.log_frame.isVisibleTo(restored) is True
        finally:
            destroy(qapp, restored)

    def test_column_widths_round_trip(self, qapp, gui_config, files,
                                      observers, msgbox):
        """Ширина колонки, заданная пользователем, переживает перезапуск."""
        win = gui.MainWindow(gui_config, gui_config['root_dir_resolved'])
        win.show()
        qapp.processEvents()
        header = win.tree_view.header()
        for column in range(header.count()):
            header.setSectionResizeMode(column, gui.QHeaderView.Interactive)
        header.resizeSection(1, 140)
        qapp.processEvents()
        win._save_ui_state()
        destroy(qapp, win)

        restored = gui.MainWindow(gui_config, gui_config['root_dir_resolved'])
        try:
            assert restored.tree_view.header().sectionSize(1) == 140
        finally:
            destroy(qapp, restored)

    @pytest.mark.smoke
    def test_toggle_log_hides_panel(self, window):
        assert window.log_action.isChecked() is True
        assert window.log_frame.isVisibleTo(window) is True

        window.log_action.setChecked(False)

        assert window.log_frame.isVisibleTo(window) is False
        assert window.config['log_visible'] is False
        assert 'журнала: скрыта' in log_text(window)

    def test_toggle_log_shows_panel_back(self, window):
        window.log_action.setChecked(False)

        window.log_action.setChecked(True)

        assert window.log_frame.isVisibleTo(window) is True
        assert window.config['log_visible'] is True

    def test_log_hidden_state_survives_restart(self, qapp, gui_config, files,
                                               observers, msgbox):
        win = gui.MainWindow(gui_config, gui_config['root_dir_resolved'])
        win.log_action.setChecked(False)
        destroy(qapp, win)

        restored = gui.MainWindow(gui_config, gui_config['root_dir_resolved'])
        try:
            assert restored.log_action.isChecked() is False
            assert restored.log_frame.isVisibleTo(restored) is False
        finally:
            destroy(qapp, restored)

    def test_notice_is_hidden_at_start(self, window):
        assert window.notice_bar.isVisibleTo(window) is False

    def test_show_and_hide_notice(self, window):
        window.show_notice('Что-то не так', 'error')

        assert window.notice_bar.isVisibleTo(window) is True
        assert window.notice_bar.property('level') == 'error'
        assert window.notice_label.text() == 'Что-то не так'

        window.hide_notice()

        assert window.notice_bar.isVisibleTo(window) is False
        assert window.notice_label.text() == ''

    def test_unknown_notice_level_becomes_info(self, window):
        window.show_notice('текст', 'не-такой')

        assert window.notice_bar.property('level') == 'info'

    def test_cfg_bool_handles_missing_and_broken(self, window):
        assert gui.MainWindow._cfg_bool({}, 'log_visible') is True
        assert gui.MainWindow._cfg_bool({}, 'blocking_errors', False) is False
        assert gui.MainWindow._cfg_bool({'log_visible': 'нет'}, 'log_visible') \
            is True

    def test_copy_log_puts_text_to_clipboard(self, window, monkeypatch):
        copied = []
        monkeypatch.setattr(gui, 'copy_to_clipboard',
                            lambda text: copied.append(text) or True)
        window.log('строка журнала')

        window.on_copy_log()

        assert copied and 'строка журнала' in copied[0]
        assert '[БУФЕР] Журнал скопирован' in log_text(window)

    def test_copy_empty_log_does_nothing(self, window, monkeypatch):
        copied = []
        monkeypatch.setattr(gui, 'copy_to_clipboard',
                            lambda text: copied.append(text) or True)
        window.log_edit.clear()

        window.on_copy_log()

        assert copied == []

    def test_real_copy_to_clipboard_uses_qt(self, window):
        """В GUI реальный copy_to_clipboard идёт через буфер Qt.

        Проверяет, что копирование имени папки/файла не зависит от
        внешнего процесса clip.exe и корректно сохраняет кириллицу.
        """
        from PySide6.QtGui import QGuiApplication
        from ok_helper import clipboard

        assert clipboard.copy_to_clipboard('Ёжик_тест') is True
        assert QGuiApplication.instance().clipboard().text() == \
            'Ёжик_тест'

    def test_save_log_writes_file(self, window, tmp_path, monkeypatch):
        target = tmp_path / 'log.txt'
        window.log('важная строка')

        class FakeFileDialog:
            @staticmethod
            def getSaveFileName(*a, **kw):
                return str(target), ''

        monkeypatch.setattr(gui, 'QFileDialog', FakeFileDialog)

        window.on_save_log()

        assert 'важная строка' in target.read_text(encoding='utf-8')
        assert '[OK] Журнал сохранён' in log_text(window)

    def test_save_log_cancelled_writes_nothing(self, window, tmp_path,
                                               monkeypatch):
        class FakeFileDialog:
            @staticmethod
            def getSaveFileName(*a, **kw):
                return '', ''

        monkeypatch.setattr(gui, 'QFileDialog', FakeFileDialog)

        window.on_save_log()

        assert not list(tmp_path.glob('*.txt'))

    def test_save_log_error_is_reported(self, window, tmp_path, monkeypatch):
        window.log('строка')

        class FakeFileDialog:
            @staticmethod
            def getSaveFileName(*a, **kw):
                return str(tmp_path / 'нет' / 'log.txt'), ''

        monkeypatch.setattr(gui, 'QFileDialog', FakeFileDialog)

        window.on_save_log()

        assert window.notice_bar.isVisibleTo(window) is True
        assert 'Не сохранить журнал' in window.notice_label.text()


@pytest.mark.story('Контекстные горячие клавиши')
class TestTabGuards:
    def test_rename_on_ftp_tab_is_refused(self, window, files, msgbox,
                                          monkeypatch):
        """F2 на вкладке FTP не должен открывать диалог локального файла."""
        select(window, files / 'track.plt')
        window.tabs.setCurrentIndex(1)
        opened = []
        monkeypatch.setattr(gui, 'RenameDialog',
                            lambda *a, **kw: opened.append(a) or None)

        window.on_rename()

        assert opened == []
        assert msgbox.kinds == []
        assert window.notice_bar.isVisibleTo(window) is True
        assert 'вкладке «Файлы»' in window.notice_label.text()

    def test_move_on_ftp_tab_is_refused(self, window, files, msgbox):
        select(window, files / 'track.plt')
        window.tabs.setCurrentIndex(1)

        window.on_move()

        assert msgbox.kinds == []
        assert window.notice_bar.isVisibleTo(window) is True

    def test_upload_on_ftp_tab_is_refused(self, window, files, tasks):
        select(window, files / '2026-09-23_Moscow' / '100m.gpx')
        window.tabs.setCurrentIndex(1)

        window.on_upload_selected()

        assert tasks.upload == []
        assert window.notice_bar.isVisibleTo(window) is True

    def test_commands_work_again_on_files_tab(self, window, files, tasks):
        select(window, files / '2026-09-23_Moscow' / '100m.gpx')
        window.tabs.setCurrentIndex(1)
        window.on_upload_selected()
        window.tabs.setCurrentIndex(0)

        window.on_upload_selected()

        assert len(tasks.upload) == 1

    def test_reveal_without_selection_does_nothing(self, window, monkeypatch):
        called = []
        monkeypatch.setattr(gui, 'reveal_in_file_manager',
                            lambda path: called.append(path) or True)

        window.on_reveal_selected()

        assert called == []

    def test_copy_path_without_selection_shows_hint(self, window, monkeypatch):
        copied = []
        monkeypatch.setattr(gui, 'copy_to_clipboard',
                            lambda text: copied.append(text) or True)

        window.on_copy_path()

        assert copied == []
        assert window.notice_bar.isVisibleTo(window) is True
        assert 'скопировать путь' in window.notice_label.text()

    def test_reveal_failure_is_reported(self, window, files, monkeypatch):
        select(window, files / 'track.plt')
        monkeypatch.setattr(gui, 'reveal_in_file_manager', lambda path: False)

        window.on_reveal_selected()

        assert window.notice_bar.isVisibleTo(window) is True
        assert 'файловый менеджер' in window.notice_label.text()


@pytest.mark.story('Журнал: автопрокрутка')
class TestLogAutoscroll:
    def scrollbar(self, qapp, window):
        """Готовит журнал с заведомо возможной прокруткой."""
        window.log_edit.resize(400, 100)
        for i in range(300):
            window.log(f'строка {i}')
        qapp.processEvents()

        bar = window.log_edit.verticalScrollBar()
        if bar.maximum() <= 0:
            pytest.skip('журнал не прокручивается в offscreen-режиме')
        return bar

    def test_scrolled_up_log_is_not_yanked_down(self, qapp, window):
        bar = self.scrollbar(qapp, window)
        bar.setValue(0)

        window.log('новая строка')
        qapp.processEvents()

        assert bar.value() == 0
        assert 'новая строка' in log_text(window)

    def test_log_scrolls_when_at_bottom(self, qapp, window):
        bar = self.scrollbar(qapp, window)
        bar.setValue(bar.maximum())

        window.log('самая новая строка')
        qapp.processEvents()

        assert bar.value() == bar.maximum()


# ─────────────────────────────────────────────────────────────────
# 4. Создание папки (Ctrl+N)
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Создание папки')
class TestNewFolder:
    @pytest.mark.smoke
    def test_creates_folder(self, window, files, monkeypatch):
        target = files / '2026-09-23_Moscow2'
        stub_dialog(monkeypatch, 'NewFolderDialog', result=target)

        window.on_new_folder()

        assert target.is_dir()
        assert '[OK] Создана папка' in log_text(window)

    def test_creates_ten_tracks_subfolder(self, window, files, monkeypatch):
        target = files / '2026-09-23_Moscow3'
        stub_dialog(monkeypatch, 'NewFolderDialog', result=target,
                    with_10_tracks=True)

        window.on_new_folder()

        assert (target / '10-Tracks').is_dir()

    def test_rejected_dialog_creates_nothing(self, window, files,
                                            monkeypatch):
        target = files / '2026-09-23_Moscow4'
        stub_dialog(monkeypatch, 'NewFolderDialog', result=target,
                    accepted=False)

        window.on_new_folder()

        assert not target.exists()

    def test_missing_result_creates_nothing(self, window, files,
                                            monkeypatch):
        stub_dialog(monkeypatch, 'NewFolderDialog', result=None)

        window.on_new_folder()      # не должно падать

    @pytest.mark.smoke
    def test_existing_folder_refused(self, window, files, monkeypatch,
                                     msgbox):
        existing = files / '2026-09-23_Moscow'
        stub_dialog(monkeypatch, 'NewFolderDialog', result=existing)

        window.on_new_folder()

        assert msgbox.kinds == ['critical']
        assert 'Уже существует' in msgbox.texts()[0]

    def test_mkdir_error_logged(self, window, files, monkeypatch):
        target = files / '2026-09-23_Moscow5'
        stub_dialog(monkeypatch, 'NewFolderDialog', result=target)

        def boom(self, *a, **kw):
            raise OSError('нет прав')

        monkeypatch.setattr(Path, 'mkdir', boom)

        window.on_new_folder()

        assert '[ОШБ] Не создать папку' in log_text(window)

    def test_ten_tracks_error_logged(self, window, files, monkeypatch):
        target = files / '2026-09-23_Moscow6'
        stub_dialog(monkeypatch, 'NewFolderDialog', result=target,
                    with_10_tracks=True)

        real_mkdir = Path.mkdir

        def boom(self, *a, **kw):
            if self.name == '10-Tracks':
                raise OSError('нет прав')
            return real_mkdir(self, *a, **kw)

        monkeypatch.setattr(Path, 'mkdir', boom)

        window.on_new_folder()

        assert '[ОШБ] Не создать 10-Tracks' in log_text(window)

    def test_new_folder_date_at_configured_root_only(self, window, tmp_path):
        """Дата и 10-Tracks — в корне из настроек, а не в любой папке.

        Регрессия: навигация переписывала ``config['root_dir']`` и
        ``root_dir_resolved``, из-за чего предзаполнение даты
        пропадало, а конфиг незаметно менял корневой путь.
        """
        from datetime import datetime

        configured = window.config['root_dir_resolved']

        dlg = gui.NewFolderDialog(window, window.config, window.tree_root)
        assert dlg.is_root is True
        assert dlg.edit.text() == datetime.now().strftime('%Y-%m-%d_')
        assert dlg.tracks_cb is not None

        other = tmp_path / 'other_root'
        other.mkdir()
        window.set_tree_root(other)

        # Навигация не трогает корень из настроек...
        assert window.config['root_dir_resolved'] == configured
        assert Path(window.config['root_dir']).resolve() == configured
        # ...поэтому чужая папка — вложенная: без даты и чек-бокса.
        nested = gui.NewFolderDialog(window, window.config, window.tree_root)
        assert nested.is_root is False
        assert nested.edit.text() == ''
        assert nested.tracks_cb is None


# ─────────────────────────────────────────────────────────────────
# 5. Переименование (F2) и автоправка
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Переименование')
class TestRename:
    def test_without_selection(self, window, msgbox):
        window.on_rename()

        assert msgbox.kinds == ['information']
        assert 'Выберите файл' in msgbox.texts()[0]

    def test_directory_selection_refused(self, window, files, msgbox):
        select(window, files / '2026-09-23_Moscow')

        window.on_rename()

        assert msgbox.kinds == ['information']

    @pytest.mark.smoke
    def test_renames_file(self, window, files, monkeypatch):
        src = files / '2026-09-17_11-55_бабушка.gpx'
        select(window, src)
        stub_dialog(monkeypatch, 'RenameDialog',
                    result='20260917_Babushka.gpx')

        window.on_rename()

        assert not src.exists()
        assert (files / '20260917_Babushka.gpx').is_file()
        assert '[OK] 2026-09-17_11-55_бабушка.gpx -> 20260917_Babushka.gpx' \
            in log_text(window)

    def test_same_name_is_noop(self, window, files, monkeypatch):
        src = files / 'track.plt'
        select(window, src)
        stub_dialog(monkeypatch, 'RenameDialog', result='track.plt')

        window.on_rename()

        assert src.is_file()
        assert '[OK]' not in log_text(window)

    def test_existing_target_refused(self, window, files, monkeypatch,
                                     msgbox):
        src = files / 'track.plt'
        select(window, src)
        stub_dialog(monkeypatch, 'RenameDialog',
                    result='2026-09-17_11-55_бабушка.gpx')

        window.on_rename()

        assert src.is_file()
        assert msgbox.kinds == ['critical']
        assert 'Уже существует' in msgbox.texts()[0]

    def test_rejected_dialog_does_nothing(self, window, files, monkeypatch):
        src = files / 'track.plt'
        select(window, src)
        stub_dialog(monkeypatch, 'RenameDialog', result='new.plt',
                    accepted=False)

        window.on_rename()

        assert src.is_file()

    def test_rename_error_logged(self, window, files, monkeypatch):
        src = files / 'track.plt'
        select(window, src)
        stub_dialog(monkeypatch, 'RenameDialog', result='new.plt')

        def boom(self, *a, **kw):
            raise OSError('занято')

        monkeypatch.setattr(Path, 'rename', boom)

        window.on_rename()

        assert '[ОШБ]' in log_text(window)


@pytest.mark.story('Автоправка имён')
class TestApplyFix:
    def test_without_selection(self, window, msgbox):
        window.on_apply_fix()

        assert msgbox.kinds == ['information']

    def test_already_ok_file(self, window, files):
        select(window, files / '2026-09-23_Moscow' / '100m.gpx')

        window.on_apply_fix()

        assert '— ок' in log_text(window)

    @pytest.mark.smoke
    def test_fixes_file(self, window, files):
        src = files / '2026-09-17_11-55_бабушка.gpx'
        select(window, src)

        window.on_apply_fix()

        assert (files / '20260917_Babushka.gpx').is_file()
        assert '[OK] 2026-09-17_11-55_бабушка.gpx -> 20260917_Babushka.gpx' \
            in log_text(window)

    def test_unfixable_file_warns(self, window, files):
        select(window, files / 'track.plt')

        window.on_apply_fix()

        assert '[!] track.plt' in log_text(window)
        assert files.joinpath('track.plt').is_file()

    def test_existing_target_logged(self, window, files):
        (files / '20260917_Babushka.gpx').write_text('')
        select(window, files / '2026-09-17_11-55_бабушка.gpx')

        window.on_apply_fix()

        assert '[ОШБ] Уже существует' in log_text(window)

    def test_fix_error_logged(self, window, files, monkeypatch):
        select(window, files / '2026-09-17_11-55_бабушка.gpx')

        def boom(self, *a, **kw):
            raise OSError('занято')

        monkeypatch.setattr(Path, 'rename', boom)

        window.on_apply_fix()

        assert '[ОШБ]' in log_text(window)


@pytest.mark.story('Исправить все (Ctrl+F5)')
class TestFixAll:
    def test_nothing_to_fix(self, window, tmp_path):
        empty = tmp_path / 'clean'
        empty.mkdir()
        window.set_tree_root(empty)

        window.on_fix_all()

        assert '[i] Нет файлов для автоисправления' in log_text(window)

    @pytest.mark.smoke
    def test_fixes_all_after_confirmation(self, window, files, msgbox):
        select(window, files / '2026-09-17_11-55_бабушка.gpx')

        window.on_fix_all()

        assert msgbox.kinds == ['question']
        assert (files / '20260917_Babushka.gpx').is_file()
        assert 'Исправлено: 1' in log_text(window)

    def test_cancel_changes_nothing(self, window, files, msgbox):
        select(window, files / '2026-09-17_11-55_бабушка.gpx')
        msgbox.answer = 'No'

        window.on_fix_all()

        assert (files / '2026-09-17_11-55_бабушка.gpx').is_file()
        assert 'Исправлено' not in log_text(window)

    def test_low_confidence_skipped(self, window, tmp_path):
        root = tmp_path / 'lowconf'
        root.mkdir()
        (root / '2026-09-23 Безымянный трек.gpx').write_text(
            '<?xml version="1.0"?><gpx></gpx>', encoding='utf-8')
        window.set_tree_root(root)

        window.on_fix_all()

        assert '[i] Нет файлов для автоисправления' in log_text(window)

    def test_existing_target_skipped(self, window, files, msgbox):
        (files / '20260917_Babushka.gpx').write_text('')
        select(window, files / '2026-09-17_11-55_бабушка.gpx')

        window.on_fix_all()

        assert '[ОШБ] Уже существует' in log_text(window)
        assert 'Исправлено: 0' in log_text(window)

    def test_rename_error_skipped(self, window, files, msgbox, monkeypatch):
        select(window, files / '2026-09-17_11-55_бабушка.gpx')

        def boom(self, *a, **kw):
            raise OSError('занято')

        monkeypatch.setattr(Path, 'rename', boom)

        window.on_fix_all()

        assert '[ОШБ]' in log_text(window)


# ─────────────────────────────────────────────────────────────────
# 6. Перенос (Ctrl+X) и копирование имени (Ctrl+Y)
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Перенос файла')
class TestMove:
    def test_without_selection(self, window, msgbox):
        window.on_move()

        assert msgbox.kinds == ['information']
        assert 'Выберите файл' in msgbox.texts()[0]

    @pytest.mark.smoke
    def test_moves_file(self, window, files, monkeypatch):
        src = files / 'track.plt'
        select(window, src)
        stub_dialog(monkeypatch, 'MoveDialog',
                    result=files / '2026-09-23_Moscow')

        window.on_move()

        assert not src.exists()
        assert (files / '2026-09-23_Moscow' / 'track.plt').is_file()
        assert '[ПЕРЕНОС] track.plt' in log_text(window)

    def test_existing_target_refused(self, window, files, monkeypatch,
                                     msgbox):
        src = files / 'track.plt'
        select(window, src)
        target_dir = files / '2026-09-23_Moscow'
        (target_dir / 'track.plt').write_text('уже есть')

        stub_dialog(monkeypatch, 'MoveDialog', result=target_dir)

        window.on_move()

        assert src.is_file()
        assert msgbox.kinds == ['critical']

    def test_rejected_dialog_does_nothing(self, window, files, monkeypatch):
        src = files / 'track.plt'
        select(window, src)
        stub_dialog(monkeypatch, 'MoveDialog',
                    result=files / '2026-09-23_Moscow', accepted=False)

        window.on_move()

        assert src.is_file()

    def test_move_error_logged(self, window, files, monkeypatch):
        src = files / 'track.plt'
        select(window, src)
        stub_dialog(monkeypatch, 'MoveDialog',
                    result=files / '2026-09-23_Moscow')

        def boom(*a, **kw):
            raise OSError('занято')

        monkeypatch.setattr(gui.shutil, 'move', boom)

        window.on_move()

        assert '[ОШБ] Перенос' in log_text(window)


@pytest.mark.story('Копирование имени (Ctrl+Y)')
class TestCopyName:
    def test_copies_tree_selection(self, window, files, monkeypatch):
        copied = []
        monkeypatch.setattr(gui, 'copy_to_clipboard',
                            lambda text: copied.append(text) or True)
        select(window, files / 'track.plt')

        window.on_copy_name()

        assert copied == ['track.plt']
        assert '[БУФЕР] Скопировано: track.plt' in log_text(window)

    def test_reports_clipboard_failure(self, window, files, monkeypatch):
        monkeypatch.setattr(gui, 'copy_to_clipboard', lambda text: False)
        select(window, files / 'track.plt')

        window.on_copy_name()

        assert '[ОШБ] Буфер обмена недоступен' in log_text(window)

    @pytest.mark.smoke
    def test_falls_back_to_ftp_selection(self, window, monkeypatch):
        """README: в списке FTP копируется имя объекта на сервере."""
        copied = []
        monkeypatch.setattr(gui, 'copy_to_clipboard',
                            lambda text: copied.append(text) or True)
        window.ftp_model.set_entries([('100m.gpx', False, 0)])
        window.ftp_view.setCurrentIndex(window.ftp_model.index(0, 0))

        window.on_copy_name()

        assert copied == ['100m.gpx']

    def test_nothing_selected_is_noop(self, window, monkeypatch):
        copied = []
        monkeypatch.setattr(gui, 'copy_to_clipboard',
                            lambda text: copied.append(text) or True)

        window.on_copy_name()

        assert copied == []

    def test_invalid_ftp_selection_is_noop(self, window, monkeypatch):
        copied = []
        monkeypatch.setattr(gui, 'copy_to_clipboard',
                            lambda text: copied.append(text) or True)
        window.ftp_model.set_entries([('100m.gpx', False, 0)])

        window.on_copy_name()

        assert copied == []

    def test_directory_name_is_copied(self, window, files, monkeypatch):
        copied = []
        monkeypatch.setattr(gui, 'copy_to_clipboard',
                            lambda text: copied.append(text) or True)
        select(window, files / '2026-09-23_Moscow')

        window.on_copy_name()

        assert copied == ['2026-09-23_Moscow']


# ─────────────────────────────────────────────────────────────────
# 7. Вкладка FTP: листинг и автоподстановка
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Листинг FTP')
class TestFtpListing:
    def test_connect_without_folder(self, window, msgbox):
        window.on_ftp_connect()

        assert msgbox.kinds == ['information']
        assert 'Укажите папку' in msgbox.texts()[0]

    def test_connect_with_bad_folder(self, window, msgbox):
        window.ftp_path_edit.setText('Moscow')

        window.on_ftp_connect()

        assert msgbox.kinds == ['critical']
        assert 'Первый компонент пути' in msgbox.texts()[0]

    def test_connect_with_valid_folder(self, window, monkeypatch):
        calls = []
        monkeypatch.setattr(gui.MainWindow, 'ftp_load',
                            lambda self, folder: calls.append(folder))
        window.ftp_path_edit.setText('2026-09-23_Moscow')

        window.on_ftp_connect()

        assert calls == ['2026-09-23_Moscow']

    def test_refresh_without_folder(self, window, msgbox):
        window.on_ftp_refresh()

        assert msgbox.kinds == ['information']

    def test_refresh_loads_current_path(self, window, monkeypatch):
        calls = []
        monkeypatch.setattr(gui.MainWindow, 'ftp_load',
                            lambda self, folder: calls.append(folder))
        window.ftp_path_edit.setText('2026-09-23_Moscow')

        window.on_ftp_refresh()

        assert calls == ['2026-09-23_Moscow']

    def test_load_without_host(self, window, gui_config, msgbox):
        gui_config['ftp_host'] = ''

        window.ftp_load('2026-09-23_Moscow')

        assert msgbox.kinds == ['critical']
        assert 'ftp_host' in msgbox.texts()[0]

    def test_load_without_user(self, window, gui_config, msgbox):
        gui_config['ftp_user'] = ''

        window.ftp_load('2026-09-23_Moscow')

        assert msgbox.kinds == ['critical']

    @pytest.mark.smoke
    def test_load_starts_task(self, window, gui_config, tasks):
        window.ftp_load('2026-09-23_Moscow')

        assert tasks.list == [('ftp.example.com', 21, 'user', 'secret',
                              '/upload', '2026-09-23_Moscow')]
        assert tasks.started == ['list']
        assert window.ftp_loading is True
        assert 'Подключение к FTP' in window.status_label.text()

    def test_second_load_while_pending_is_ignored(self, window, tasks):
        window.ftp_load('2026-09-23_Moscow')
        window.ftp_load('2026-09-23_Moscow')

        assert len(tasks.list) == 1

    def test_missing_port_defaults(self, window, gui_config, tasks):
        gui_config.pop('ftp_port')

        window.ftp_load('2026-09-23_Moscow')

        assert tasks.list[0][1] == 21

    def test_empty_base_defaults_to_root(self, window, gui_config, tasks):
        gui_config['ftp_path'] = ''

        window.ftp_load('2026-09-23_Moscow')

        assert tasks.list[0][4] == '/'

    @pytest.mark.smoke
    def test_result_error_clears_state(self, window, files, monkeypatch):
        monkeypatch.setattr(window, 'rebuild_tree', lambda: None)
        window.ftp_loading = True
        window.ftp_model.set_entries([('100m.gpx', False, 0)])

        window._on_ftp_result([], 'нет связи', '2026-09-23_Moscow')

        assert window.ftp_loading is False
        assert window.ftp_connected is False
        assert window.ftp_model.rowCount() == 0
        assert window.tree_model.ftp_sync_folder == ''
        assert window.status_label.text() == 'FTP: ошибка'
        assert '[FTP] Ошибка: нет связи' in log_text(window)

    @pytest.mark.smoke
    def test_result_success_marks_local_files(self, window, files,
                                              monkeypatch):
        monkeypatch.setattr(window, 'rebuild_tree', lambda: None)
        select(window, files / '2026-09-23_Moscow' / '100m.gpx')

        window._on_ftp_result(
            [('100m.gpx', False, 100)], '', '2026-09-23_Moscow')

        assert window.ftp_connected is True
        assert window.ftp_folder == '2026-09-23_Moscow'
        assert window.ftp_model.rowCount() == 1
        assert window.tree_model.ftp_sync_folder == '2026-09-23_Moscow'
        assert window.tree_model.ftp_sync_names == {'100m.gpx'}
        assert window.status_label.text() == 'FTP: 2026-09-23_Moscow'

    def test_count_status_wins_after_rebuild(self, window, files):
        """Счётчики видны всегда, «FTP: …» — только в сообщении слева.

        Раньше счётчики жили в самом сообщении и rebuild_tree() затирал
        статус FTP; теперь это разные элементы статусбара.
        """
        window._on_ftp_result([], '', '2026-09-23_Moscow')

        assert 'Файлов:' in window.status_line()
        assert window.status_label.text() == 'FTP: 2026-09-23_Moscow'

    def test_result_with_nested_folder_clears_sync(self, window, files):
        window.tree_model.set_ftp_sync('2026-09-23_Moscow', {'100m.gpx'})

        window._on_ftp_result([], '', '2026-09-23_Moscow/sub')

        assert window.tree_model.ftp_sync_folder == ''

    def test_result_ignores_directories_when_syncing(self, window, files):
        window._on_ftp_result([('sub', True, 0), ('a.gpx', False, 1)], '',
                              '2026-09-23_Moscow')

        assert window.tree_model.ftp_sync_names == {'a.gpx'}

    def test_result_logs_count(self, window, files):
        window._on_ftp_result([('a.gpx', False, 1)], '', '2026-09-23_Moscow')

        assert '[FTP] Открыта папка 2026-09-23_Moscow (1 элементов)' in \
            log_text(window)

    def test_open_directory_by_double_click(self, window, monkeypatch):
        calls = []
        monkeypatch.setattr(gui.MainWindow, 'ftp_load',
                            lambda self, folder: calls.append(folder))
        window.ftp_model.set_entries(
            [('sub', True, 0), ('a.gpx', False, 1)])
        window.ftp_path_edit.setText('2026-09-23_Moscow')

        window.on_ftp_double_click(window.ftp_model.index(0, 0))

        assert window.ftp_path_edit.text() == '2026-09-23_Moscow/sub'
        assert calls == ['2026-09-23_Moscow/sub']

    def test_double_click_on_file_does_nothing(self, window, monkeypatch):
        calls = []
        monkeypatch.setattr(gui.MainWindow, 'ftp_load',
                            lambda self, folder: calls.append(folder))
        window.ftp_model.set_entries([('a.gpx', False, 1)])

        window.on_ftp_double_click(window.ftp_model.index(0, 0))

        assert calls == []

    def test_double_click_without_name_does_nothing(self, window,
                                                    monkeypatch):
        calls = []
        monkeypatch.setattr(gui.MainWindow, 'ftp_load',
                            lambda self, folder: calls.append(folder))
        window.ftp_model.set_entries([('sub', True, 0)])
        window.ftp_model.item(0).setData(None, gui.ROLE_FTP_NAME)

        window.on_ftp_double_click(window.ftp_model.index(0, 0))

        assert calls == []


@pytest.mark.story('Автоподстановка пути FTP')
class TestFtpAutofill:
    def test_other_tab_does_nothing(self, window, monkeypatch):
        calls = []
        monkeypatch.setattr(gui.MainWindow, 'ftp_load',
                            lambda self, folder: calls.append(folder))

        window._on_tab_changed(0)

        assert calls == []

    def test_follow_disabled_does_nothing(self, window, monkeypatch):
        calls = []
        monkeypatch.setattr(gui.MainWindow, 'ftp_load',
                            lambda self, folder: calls.append(folder))
        window.follow_checkbox.setChecked(False)

        window._on_tab_changed(1)

        assert calls == []

    def test_warns_when_folder_not_found(self, window, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(gui.MainWindow, 'ftp_load',
                            lambda self, folder: calls.append(folder))
        empty = tmp_path / 'plain'
        empty.mkdir()
        window.set_tree_root(empty)

        window._on_tab_changed(1)

        assert calls == []
        assert 'не найдено' in log_text(window)

    @pytest.mark.smoke
    def test_substitutes_folder_from_selection(self, window, files,
                                               monkeypatch):
        calls = []
        monkeypatch.setattr(gui.MainWindow, 'ftp_load',
                            lambda self, folder: calls.append(folder))
        select(window, files / '2026-09-23_Moscow' / '100m.gpx')

        window._on_tab_changed(1)

        assert window.ftp_path_edit.text() == '2026-09-23_Moscow'
        assert calls == ['2026-09-23_Moscow']
        assert 'Автоподстановка папки' in log_text(window)

    def test_substitutes_from_selected_directory(self, window, files,
                                                 monkeypatch):
        calls = []
        monkeypatch.setattr(gui.MainWindow, 'ftp_load',
                            lambda self, folder: calls.append(folder))
        select(window, files / '2026-09-23_Moscow' / '10-Tracks')

        window._on_tab_changed(1)

        assert calls == ['2026-09-23_Moscow']

    def test_already_connected_folder_is_skipped(self, window, files,
                                                 monkeypatch):
        calls = []
        monkeypatch.setattr(gui.MainWindow, 'ftp_load',
                            lambda self, folder: calls.append(folder))
        select(window, files / '2026-09-23_Moscow' / '100m.gpx')
        window.ftp_folder = '2026-09-23_Moscow'
        window.ftp_connected = True

        window._on_tab_changed(1)

        assert calls == []

    def test_follow_toggle_is_logged(self, window):
        window._on_follow_changed(False)
        window._on_follow_changed(True)

        text = log_text(window)
        assert 'выкл' in text
        assert 'вкл' in text

    def test_set_tree_root_retriggers_autofill_on_ftp_tab(self, window,
                                                          files):
        """Переключение корня на вкладке FTP снова запускает подстановку."""
        window.tabs.setCurrentIndex(0)
        window.set_tree_root(files)
        assert 'Автоподстановка' not in log_text(window)

        window.tabs.setCurrentIndex(1)          # переход на вкладку FTP
        assert log_text(window).count('Автоподстановка') == 1

        window.set_tree_root(files)             # смена корня на вкладке FTP
        assert log_text(window).count('Автоподстановка') == 2

    def test_autofill_disabled_by_checkbox_after_root_change(self, window,
                                                             files):
        window.tabs.setCurrentIndex(1)
        window.follow_checkbox.setChecked(False)
        before = log_text(window).count('Автоподстановка')

        window.set_tree_root(files)

        assert log_text(window).count('Автоподстановка') == before


# ─────────────────────────────────────────────────────────────────
# 8. Вкладка FTP: удаление
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Удаление на FTP')
class TestFtpDelete:
    def open_ftp_tab(self, window):
        window.tabs.setCurrentIndex(1)

    def test_ignored_on_files_tab(self, window, tasks, msgbox):
        window.ftp_model.set_entries([('a.gpx', False, 1)])
        window.ftp_view.setCurrentIndex(window.ftp_model.index(0, 0))

        window.on_ftp_delete_selected()

        assert msgbox.calls == []
        assert tasks.delete == []

    def test_without_selection(self, window, tasks, msgbox):
        self.open_ftp_tab(window)

        window.on_ftp_delete_selected()

        assert msgbox.kinds == ['information']
        assert 'Выберите файл на сервере' in msgbox.texts()[0]

    def test_directory_refused(self, window, tasks, msgbox):
        self.open_ftp_tab(window)
        window.ftp_model.set_entries([('sub', True, 0)])
        window.ftp_view.setCurrentIndex(window.ftp_model.index(0, 0))

        window.on_ftp_delete_selected()

        assert 'Удаление папок не поддерживается' in msgbox.texts()[0]
        assert tasks.delete == []

    @pytest.mark.smoke
    def test_readonly_refused(self, window, gui_config, tasks, msgbox):
        """README: удаление только при ftp_readonly = false."""
        gui_config['ftp_readonly'] = True
        self.open_ftp_tab(window)
        window.ftp_model.set_entries([('a.gpx', False, 1)])
        window.ftp_view.setCurrentIndex(window.ftp_model.index(0, 0))

        window.on_ftp_delete_selected()

        assert 'ftp_readonly = true' in ' '.join(msgbox.texts())
        assert tasks.delete == []

    def test_cancel_does_not_delete(self, window, tasks, msgbox):
        self.open_ftp_tab(window)
        window.ftp_model.set_entries([('a.gpx', False, 1)])
        window.ftp_view.setCurrentIndex(window.ftp_model.index(0, 0))
        msgbox.answer = 'No'

        window.on_ftp_delete_selected()

        assert msgbox.kinds == ['question']
        assert tasks.delete == []

    @pytest.mark.smoke
    def test_confirmed_delete_starts_task(self, window, tasks, msgbox):
        self.open_ftp_tab(window)
        window.ftp_folder = '2026-09-23_Moscow'
        window.ftp_model.set_entries([('a.gpx', False, 1)])
        window.ftp_view.setCurrentIndex(window.ftp_model.index(0, 0))

        window.on_ftp_delete_selected()

        assert tasks.delete == [('ftp.example.com', 21, 'user', 'secret',
                                 '/upload', '2026-09-23_Moscow', 'a.gpx')]
        assert tasks.started == ['delete']

    def test_delete_done_removes_sync_mark(self, window, files, monkeypatch):
        reloads = []
        monkeypatch.setattr(gui.MainWindow, 'ftp_load',
                            lambda self, folder: reloads.append(folder))
        window.ftp_folder = '2026-09-23_Moscow'
        window.tree_model.set_ftp_sync('2026-09-23_Moscow',
                                       {'100m.gpx', 'other.gpx'})

        window._on_ftp_delete_done('2026-09-23_Moscow', '100m.gpx', '')

        assert window.tree_model.ftp_sync_names == {'other.gpx'}
        assert reloads == ['2026-09-23_Moscow']
        assert '[FTP] Удалён: 2026-09-23_Moscow/100m.gpx' in log_text(window)

    def test_delete_error_logged(self, window, monkeypatch):
        monkeypatch.setattr(gui.MainWindow, 'ftp_load', lambda self, f: None)

        window._on_ftp_delete_done('2026-09-23_Moscow', 'a.gpx', 'нет прав')

        assert '[FTP] Ошибка удаления a.gpx: нет прав' in log_text(window)

    def test_delete_done_other_folder_keeps_marks(self, window, monkeypatch):
        monkeypatch.setattr(gui.MainWindow, 'ftp_load', lambda self, f: None)
        window.tree_model.set_ftp_sync('2026-09-23_Moscow', {'100m.gpx'})

        window._on_ftp_delete_done('2026-09-24_Other', '100m.gpx', '')

        assert window.tree_model.ftp_sync_names == {'100m.gpx'}


# ─────────────────────────────────────────────────────────────────
# 9. Вкладка FTP: загрузка и режим эмуляции
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Загрузка на FTP')
class TestFtpUpload:
    def test_without_selection(self, window, msgbox):
        window.on_upload_selected()

        assert msgbox.kinds == ['information']

    def test_non_uploadable_file_refused(self, window, files, msgbox):
        """Предсказуемый отказ показывается плашкой, а не окном."""
        select(window, files / 'track.plt')

        window.on_upload_selected()

        assert msgbox.kinds == []
        assert window.notice_bar.isVisibleTo(window) is True
        assert 'Нельзя отправить' in window.notice_label.text()
        assert 'track.plt' in window.notice_label.text()

    def test_blocking_errors_keeps_modal_dialog(self, window, files, msgbox):
        """blocking_errors = true возвращает привычное модальное окно."""
        window.config['blocking_errors'] = True
        select(window, files / 'track.plt')

        window.on_upload_selected()

        assert msgbox.kinds == ['critical']
        assert 'Нельзя отправить' in msgbox.texts()[0]
        assert window.notice_bar.isVisibleTo(window) is False

    def test_uploadable_file_starts_upload(self, window, files, tasks,
                                           monkeypatch):
        select(window, files / '2026-09-23_Moscow' / '100m.gpx')

        window.on_upload_selected()

        assert len(tasks.upload) == 1
        assert tasks.upload[0][5] == '2026-09-23_Moscow'
        assert tasks.upload[0][7] is False      # readonly
        assert window.status_label.text() != ''

    def test_manual_folder_when_not_found(self, window, tmp_path, tasks,
                                          monkeypatch):
        root = tmp_path / 'plain'
        root.mkdir()
        (root / '100m.gpx').write_text('')
        window.set_tree_root(root)
        select(window, root / '100m.gpx')

        class FakeInputDialog:
            @staticmethod
            def getText(*a, **kw):
                return '2026-09-23_Moscow', True

        monkeypatch.setattr(gui, 'QInputDialog', FakeInputDialog)

        window.on_upload_selected()

        assert tasks.upload[0][5] == '2026-09-23_Moscow'

    def test_manual_folder_cancelled(self, window, tmp_path, tasks,
                                     monkeypatch):
        root = tmp_path / 'plain'
        root.mkdir()
        (root / '100m.gpx').write_text('')
        window.set_tree_root(root)
        select(window, root / '100m.gpx')

        class FakeInputDialog:
            @staticmethod
            def getText(*a, **kw):
                return '', False

        monkeypatch.setattr(gui, 'QInputDialog', FakeInputDialog)

        window.on_upload_selected()

        assert tasks.upload == []

    def test_manual_folder_invalid(self, window, tmp_path, tasks, msgbox,
                                   monkeypatch):
        root = tmp_path / 'plain'
        root.mkdir()
        (root / '100m.gpx').write_text('')
        window.set_tree_root(root)
        select(window, root / '100m.gpx')

        class FakeInputDialog:
            @staticmethod
            def getText(*a, **kw):
                return 'плохое имя', True

        monkeypatch.setattr(gui, 'QInputDialog', FakeInputDialog)

        window.on_upload_selected()

        assert msgbox.kinds == []
        assert window.notice_bar.isVisibleTo(window) is True
        assert tasks.upload == []

    def test_upload_here_without_folder(self, window, msgbox):
        window.on_ftp_upload_here()

        assert msgbox.kinds == ['information']

    def test_upload_here_cancelled(self, window, monkeypatch):
        window.ftp_path_edit.setText('2026-09-23_Moscow')

        class FakeFileDialog:
            @staticmethod
            def getOpenFileName(*a, **kw):
                return '', ''

        monkeypatch.setattr(gui, 'QFileDialog', FakeFileDialog)
        called = []
        monkeypatch.setattr(gui.MainWindow, 'do_upload',
                            lambda self, src, folder: called.append(src))

        window.on_ftp_upload_here()

        assert called == []

    def test_upload_here_rejects_bad_file(self, window, files, monkeypatch,
                                          msgbox):
        window.ftp_path_edit.setText('2026-09-23_Moscow')
        bad = files / 'track.plt'

        class FakeFileDialog:
            @staticmethod
            def getOpenFileName(*a, **kw):
                return str(bad), ''

        monkeypatch.setattr(gui, 'QFileDialog', FakeFileDialog)

        window.on_ftp_upload_here()

        assert msgbox.kinds == ['critical']

    def test_upload_here_uploads(self, window, files, monkeypatch, tasks):
        window.ftp_path_edit.setText('2026-09-23_Moscow')
        good = files / '2026-09-23_Moscow' / '100m.gpx'

        class FakeFileDialog:
            @staticmethod
            def getOpenFileName(*a, **kw):
                return str(good), ''

        monkeypatch.setattr(gui, 'QFileDialog', FakeFileDialog)

        window.on_ftp_upload_here()

        assert tasks.upload[0][6] == good

    # --- do_upload ---

    def test_do_upload_normal(self, window, files, tasks):
        src = files / '2026-09-23_Moscow' / '100m.gpx'

        window.do_upload(src, '2026-09-23_Moscow')

        assert tasks.upload == [('ftp.example.com', 21, 'user', 'secret',
                                 '/upload', '2026-09-23_Moscow', src, False)]
        assert 'Начинаю загрузку' in log_text(window)

    @pytest.mark.smoke
    def test_do_upload_readonly_logs_emulation(self, window, gui_config,
                                               files, tasks):
        """README: ftp_readonly = true — только эмуляция."""
        gui_config['ftp_readonly'] = True
        src = files / '2026-09-23_Moscow' / '100m.gpx'

        window.do_upload(src, '2026-09-23_Moscow')

        assert tasks.upload[0][7] is True
        assert '[FTP] [эмуляция]' in log_text(window)

    # --- _on_upload_done ---

    def test_upload_done_error(self, window, files):
        window._on_upload_done('2026-09-23_Moscow', '100m.gpx', 'нет места')

        assert '[FTP] Ошибка: нет места' in log_text(window)

    def test_upload_done_readonly_reports_nothing(self, window, files):
        window._on_upload_done('', '', '')

        assert 'Успешно' not in log_text(window)

    @pytest.mark.smoke
    def test_upload_done_adds_sync_mark(self, window, files):
        select(window, files / '2026-09-23_Moscow' / '100m.gpx')
        window._on_ftp_result([], '', '2026-09-23_Moscow')

        window._on_upload_done('2026-09-23_Moscow', '100m.gpx', '')

        assert window.tree_model.ftp_sync_names == {'100m.gpx'}
        assert '[FTP] Успешно: 2026-09-23_Moscow/100m.gpx' in \
            log_text(window)

    def test_upload_done_nested_folder_not_marked(self, window, files):
        window._on_ftp_result([], '', '2026-09-23_Moscow')
        window.tree_model.set_ftp_sync('2026-09-23_Moscow', set())

        window._on_upload_done('2026-09-23_Moscow/sub', 'a.gpx', '')

        assert window.tree_model.ftp_sync_names == set()

    def test_upload_done_other_folder_not_marked(self, window, files):
        window.tree_model.set_ftp_sync('2026-09-23_Moscow', set())

        window._on_upload_done('2026-09-24_Other', 'a.gpx', '')

        assert window.tree_model.ftp_sync_names == set()


# ─────────────────────────────────────────────────────────────────
# 10. Watchdog и автообновление
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Watchdog')
class TestWatchdog:
    @pytest.mark.smoke
    def test_tree_observer_wired(self, window, observers, files):
        obs = observers.instances[0]

        assert obs.started is True
        assert obs.recursive is True
        assert obs.path == str(window.watch_dir)
        assert isinstance(obs.handler, gui.DirHandler)

    def test_restart_stops_previous(self, window, observers):
        first = observers.instances[-1]
        window._start_watch()

        assert first.stopped is True
        assert first.joined is True
        assert observers.instances[-1].started is True

    def test_stop_watch_without_observer(self, window):
        window.observer = None
        window._stop_watch()      # не должно падать

    def test_observer_start_error_logged(self, window, monkeypatch):
        class BrokenObserver:
            def __init__(self):
                pass

            def schedule(self, *a, **kw):
                pass

            def start(self):
                raise OSError('нет доступа к папке')

        monkeypatch.setattr(gui, 'Observer', BrokenObserver)

        window._start_watch()

        assert window.observer is None
        assert 'не удалось запустить' in log_text(window)

    def test_fs_event_schedules_refresh(self, window, timers):
        window._on_fs_event(None)

        assert timers.calls and timers.calls[0][0] == 0

    def test_schedule_refresh_is_debounced(self, window, timers):
        window._schedule_refresh()
        window._schedule_refresh()

        assert window._refresh_pending is True
        assert len(timers.calls) == 1
        assert timers.calls[0][0] == 200

    def test_do_refresh_clears_flag(self, window, files, timers):
        window._schedule_refresh()

        window._do_refresh()

        assert window._refresh_pending is False

    def test_downloads_watch_disabled(self, window):
        assert window.downloads_observer is None

    @pytest.mark.smoke
    def test_downloads_watch_enabled(self, window, gui_config, monkeypatch):
        gui_config['watch_downloads'] = True
        FakeObserver.instances = []
        monkeypatch.setattr(gui, 'Observer', FakeObserver)

        window._start_downloads_watch()

        assert window.downloads_observer is not None
        assert FakeObserver.instances[-1].recursive is False
        assert 'Слежение включено' in log_text(window)
        assert window.downloads_watch_active() is True

    def test_downloads_watch_disabled_is_logged(self, window, gui_config):
        """Выключенное слежение должно быть видно в журнале, а не молчать."""
        gui_config['watch_downloads'] = False

        window._start_downloads_watch()

        assert window.downloads_observer is None
        assert window.downloads_watch_active() is False
        assert 'Слежение выключено' in log_text(window)

    def test_downloads_watch_missing_dir(self, window, gui_config):
        gui_config['watch_downloads'] = True
        gui_config['downloads_dir_resolved'] = None

        window._start_downloads_watch()

        assert window.downloads_observer is None
        assert 'Папка загрузок не задана' in log_text(window)

    def test_downloads_watch_starts_poll_timer(self, window, gui_config,
                                               monkeypatch):
        """Кроме событий системы есть периодическая проверка папки."""
        gui_config['watch_downloads'] = True
        monkeypatch.setattr(gui, 'Observer', FakeObserver)

        window._start_downloads_watch()

        assert window.downloads_poll_timer is not None
        assert window.downloads_poll_timer.interval() == gui.DOWNLOAD_POLL_MS

    def test_stop_downloads_watch_stops_timer(self, window, gui_config,
                                              monkeypatch):
        gui_config['watch_downloads'] = True
        monkeypatch.setattr(gui, 'Observer', FakeObserver)
        window._start_downloads_watch()

        window._stop_downloads_watch()

        assert window.downloads_poll_timer is None
        assert window.downloads_observer is None

    def test_downloads_watch_not_a_directory(self, window, gui_config,
                                             tmp_path):
        gui_config['watch_downloads'] = True
        gui_config['downloads_dir_resolved'] = tmp_path / 'нет-такой'

        window._start_downloads_watch()

        assert window.downloads_observer is None
        assert 'Папки нет' in log_text(window)
        assert 'слежение не запущено' in log_text(window)

    def test_downloads_watch_skips_tree_root(self, window, gui_config):
        gui_config['watch_downloads'] = True
        gui_config['downloads_dir_resolved'] = window.tree_root

        window._start_downloads_watch()

        assert window.downloads_observer is None

    def test_new_download_is_deduplicated(self, window, tmp_path, timers):
        p = tmp_path / 'track.gpx'
        p.write_text('')

        window._handle_new_download(p)
        window._handle_new_download(p)

        assert window._seen_downloads == {p}
        assert len([c for c in timers.calls if c[0] == 1200]) == 1

    def test_offer_move_missing_file(self, window, tmp_path):
        window._offer_move_download(tmp_path / 'нет.gpx')

        assert 'Новый файл' not in log_text(window)

    def test_offer_move_ignored(self, window, tmp_path, msgbox):
        p = tmp_path / 'track.gpx'
        p.write_text('')
        msgbox.click_text = 'Игнорировать'

        window._offer_move_download(p)

        assert p.is_file()
        assert '[ЗАГРУЗКИ] Новый файл: track.gpx' in log_text(window)

    @pytest.mark.smoke
    def test_offer_move_to_root(self, window, tmp_path, msgbox):
        p = tmp_path / 'track.gpx'
        p.write_text('')
        msgbox.click_text = 'Переместить в корень'

        window._offer_move_download(p)

        assert not p.exists()
        assert (window.tree_root / 'track.gpx').is_file()
        assert '[ЗАГРУЗКИ] track.gpx' in log_text(window)

    def test_download_move_dir_none_without_ten_tracks(self, window):
        """Прямой дочерней 10-Tracks нет — прежняя логика."""
        assert window._download_move_dir() is None

    def test_download_move_dir_for_direct_child(self, window):
        ten = window.tree_root / '10-Tracks'
        ten.mkdir()

        assert window._download_move_dir() == ten

    def test_offer_move_prefers_ten_tracks_child(self, window, tmp_path,
                                                 msgbox):
        """Есть дочерняя 10-Tracks — переносим новый файл именно туда."""
        ten = window.tree_root / '10-Tracks'
        ten.mkdir()
        p = tmp_path / 'track.gpx'
        p.write_text('')
        msgbox.click_text = 'Переместить в 10-Tracks'

        window._offer_move_download(p)

        assert not p.exists()
        assert (ten / 'track.gpx').is_file()
        assert not (window.tree_root / 'track.gpx').exists()

    def test_offer_move_ignores_deeper_ten_tracks(self, window, tmp_path,
                                                  msgbox):
        """10-Tracks глубже одного уровня — прежняя логика (в корень)."""
        deep = window.tree_root / '2026-09-23_Moscow' / '10-Tracks'
        assert deep.is_dir()          # создаётся фикстурой files
        p = tmp_path / 'deep.gpx'
        p.write_text('')
        msgbox.click_text = 'Переместить в корень'

        window._offer_move_download(p)

        assert (window.tree_root / 'deep.gpx').is_file()
        assert not (deep / 'deep.gpx').exists()

    def test_offer_move_existing_target(self, window, tmp_path, msgbox):
        (window.tree_root / 'track.gpx').write_text('уже есть')
        p = tmp_path / 'track.gpx'
        p.write_text('')
        msgbox.click_text = 'Переместить в корень'

        window._offer_move_download(p)

        assert p.is_file()
        assert '[ОШБ] Уже существует' in log_text(window)

    def test_offer_move_error_logged(self, window, tmp_path, msgbox,
                                     monkeypatch):
        p = tmp_path / 'track.gpx'
        p.write_text('')
        msgbox.click_text = 'Переместить в корень'

        def boom(*a, **kw):
            raise OSError('занято')

        monkeypatch.setattr(gui.shutil, 'move', boom)

        window._offer_move_download(p)

        assert '[ОШБ] Перенос' in log_text(window)

    def test_real_observer_starts_and_stops(self, qapp, gui_config, msgbox):
        """Проверка настоящего watchdog без подмены."""
        win = gui.MainWindow(gui_config, gui_config['root_dir_resolved'])
        try:
            assert win.observer is not None
            assert win.observer.is_alive() is True
        finally:
            destroy(qapp, win)

        assert win.observer is None


# ─────────────────────────────────────────────────────────────────
# 10b. Папка загрузок: подстраховка, ручная проверка, меню
# ─────────────────────────────────────────────────────────────────

@pytest.fixture
def poll_timers(monkeypatch):
    """Перехватывает QTimer: и singleShot, и периодические таймеры.

    Позволяет «выстрелить» таймером проверки загрузок вручную.
    """
    rec = types.SimpleNamespace(instances=[], calls=[])

    class FakeTimer:
        def __init__(self, parent=None):
            rec.instances.append(self)
            self._interval = 0
            self.started = False
            self.timeout = FakeSignal()

        def setInterval(self, ms):
            self._interval = ms

        def interval(self):
            return self._interval

        def start(self):
            self.started = True

        def stop(self):
            self.started = False

        @staticmethod
        def singleShot(ms, callback=None):
            rec.calls.append((ms, callback))

    monkeypatch.setattr(gui, 'QTimer', FakeTimer)
    return rec


@pytest.mark.story('Папка загрузок')
class TestDownloadsWatch:
    def downloads_dir(self, gui_config, window):
        """Направляет слежение на отдельную папку рядом с tmp_path."""
        d = Path(gui_config['root_dir_resolved']).parent / 'downloads'
        d.mkdir(exist_ok=True)
        gui_config['downloads_dir_resolved'] = d
        gui_config['downloads_dir'] = str(d)
        return d

    def test_poll_offers_new_file(self, window, gui_config, monkeypatch,
                                  poll_timers, msgbox):
        """Подстраховка находит файл, даже если события не пришли."""
        d = self.downloads_dir(gui_config, window)
        gui_config['watch_downloads'] = True
        monkeypatch.setattr(gui, 'Observer', FakeObserver)
        window._start_downloads_watch()
        msgbox.click_text = 'Игнорировать'
        p = d / 'track.gpx'
        p.write_text('')

        offered = window.poll_downloads(ignore_before=0)

        assert offered == 1
        assert len([c for c in poll_timers.calls if c[0] == 1200]) == 1

    def test_poll_skips_uninteresting_files(self, window, gui_config,
                                            monkeypatch, poll_timers):
        d = self.downloads_dir(gui_config, window)
        (d / 'readme.txt').write_text('')
        (d / 'archive.zip').write_text('')

        assert window.poll_downloads(ignore_before=0) == 0

    def test_poll_skips_stale_files(self, window, gui_config, monkeypatch,
                                    poll_timers):
        """Скачанное до запуска сервиса не предлагается."""
        d = self.downloads_dir(gui_config, window)
        p = d / 'old.gpx'
        p.write_text('')

        import time as _time
        offered = window.poll_downloads(ignore_before=_time.time() + 10)

        assert offered == 0
        assert p in window._seen_downloads
        assert poll_timers.calls == []

    def test_poll_is_idempotent(self, window, gui_config, monkeypatch,
                               poll_timers):
        d = self.downloads_dir(gui_config, window)
        (d / 'track.gpx').write_text('')

        assert window.poll_downloads(ignore_before=0) == 1
        assert window.poll_downloads(ignore_before=0) == 0

    def test_check_downloads_reports_empty_folder(self, window, gui_config,
                                                 poll_timers):
        self.downloads_dir(gui_config, window)

        window.on_check_downloads()

        assert 'нет файлов .gpx, .plt, .wpt' in log_text(window)

    def test_check_downloads_offers_old_file(self, window, gui_config,
                                             monkeypatch, poll_timers,
                                             msgbox):
        """Ручная проверка предлагает и то, что скачано раньше."""
        d = self.downloads_dir(gui_config, window)
        (d / 'track.plt').write_text('')
        msgbox.click_text = 'Игнорировать'

        window.on_check_downloads()

        assert 'найдено 1' in log_text(window)
        assert len([c for c in poll_timers.calls if c[0] == 1200]) == 1

    def test_check_downloads_without_folder(self, window, gui_config,
                                            poll_timers):
        gui_config['downloads_dir_resolved'] = None

        window.on_check_downloads()

        assert 'Слежение недоступно' in log_text(window)

    def test_timer_triggers_poll_with_baseline(self, window, gui_config,
                                               monkeypatch, poll_timers,
                                               msgbox):
        """Таймер проверяет папку с учётом времени запуска."""
        d = self.downloads_dir(gui_config, window)
        gui_config['watch_downloads'] = True
        monkeypatch.setattr(gui, 'Observer', FakeObserver)
        window._start_downloads_watch()
        msgbox.click_text = 'Игнорировать'
        poll_timers.calls.clear()
        (d / 'fresh.gpx').write_text('')

        window.downloads_poll_timer.timeout.emit()

        assert len([c for c in poll_timers.calls if c[0] == 1200]) == 1

    def test_baseline_has_tolerance_for_fresh_files(self, window, gui_config,
                                                   monkeypatch, poll_timers):
        """Файл, скачанный в момент запуска, не должен теряться.

        Время изменения файла бывает на доли миллисекунды «старше»
        метки запуска, поэтому фильтр обязан иметь небольшой допуск.
        """
        d = self.downloads_dir(gui_config, window)
        gui_config['watch_downloads'] = True
        monkeypatch.setattr(gui, 'Observer', FakeObserver)

        window._start_downloads_watch()
        p = d / 'right-at-start.gpx'
        p.write_text('')

        import time as _time
        assert p.stat().st_mtime > window._downloads_baseline
        assert window._downloads_baseline <= _time.time()

    def test_toggle_action_updates_config_and_watch(self, window, gui_config,
                                                    monkeypatch,
                                                    poll_timers):
        monkeypatch.setattr(gui, 'Observer', FakeObserver)
        gui_config['downloads_dir_resolved'] = None

        window.watch_downloads_action.setChecked(True)

        assert window.config['watch_downloads'] is True
        assert 'Слежение' in log_text(window)

        window.watch_downloads_action.setChecked(False)

        assert window.config['watch_downloads'] is False
        assert window.downloads_observer is None

    def test_sync_downloads_actions_reflects_settings(self, window,
                                                      gui_config):
        gui_config['watch_downloads'] = False

        window._sync_downloads_actions()

        assert window.watch_downloads_action.isChecked() is False
        assert 'выключено' in window.downloads_action.toolTip()

    def test_open_downloads_dir_without_folder(self, window, gui_config,
                                              poll_timers):
        gui_config['downloads_dir_resolved'] = None

        window.on_open_downloads_dir()

        assert window.notice_bar.isVisibleTo(window) is True

    def test_open_downloads_dir_uses_file_manager(self, window, gui_config,
                                                  monkeypatch, poll_timers):
        d = self.downloads_dir(gui_config, window)
        revealed = []
        monkeypatch.setattr(gui, 'reveal_in_file_manager',
                            lambda path: revealed.append(path) or True)

        window.on_open_downloads_dir()

        assert revealed == [d]

    def test_downloads_menu_has_items(self, window):
        """Меню «Загрузки» доступно из главного меню."""
        texts = [a.text() for a in window.menuBar().actions()]
        assert 'Загрузки' in texts
        menu = window.menuBar().actions()[texts.index('Загрузки')].menu()
        items = [a.text() for a in menu.actions() if a.text()]
        assert 'Проверить папку загрузок' in items
        assert 'Следить за папкой загрузок' in items
        assert 'Открыть папку загрузок' in items

    def test_check_action_shortcut(self, window):
        assert window.downloads_action.shortcut() == \
            gui.QKeySequence('Ctrl+D')


# ─────────────────────────────────────────────────────────────────
# 11. Настройки, справка, тема
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Настройки и справка')
class TestSettings:
    def test_open_settings_applies(self, window, monkeypatch):
        applied = []
        monkeypatch.setattr(gui.MainWindow, '_on_settings_saved',
                            lambda self: applied.append(True))
        stub_dialog(monkeypatch, 'SettingsDialog')

        window.open_settings()

        assert applied == [True]

    def test_open_settings_cancelled(self, window, monkeypatch):
        applied = []
        monkeypatch.setattr(gui.MainWindow, '_on_settings_saved',
                            lambda self: applied.append(True))
        stub_dialog(monkeypatch, 'SettingsDialog', accepted=False)

        window.open_settings()

        assert applied == []

    @pytest.mark.smoke
    def test_settings_saved_persists_and_logs(self, window, monkeypatch):
        saved = []
        monkeypatch.setattr(gui, 'save_config',
                            lambda cfg: saved.append(dict(cfg)))

        window._on_settings_saved()

        assert saved
        assert '[НАСТР] Сохранено' in log_text(window)

    def test_settings_save_error_logged(self, window, monkeypatch):
        def boom(cfg):
            raise OSError('нет доступа')

        monkeypatch.setattr(gui, 'save_config', boom)

        window._on_settings_saved()

        assert '[НАСТР] Не сохранить конфиг' in log_text(window)

    def test_settings_applies_new_root(self, window, files, monkeypatch):
        monkeypatch.setattr(gui, 'save_config', lambda cfg: None)
        new_root = files / '2026-09-23_Moscow'
        window.config['root_dir'] = str(new_root)

        window._on_settings_saved()

        assert window.tree_root == new_root

    def test_settings_ignores_missing_new_root(self, window, tmp_path,
                                               monkeypatch):
        monkeypatch.setattr(gui, 'save_config', lambda cfg: None)
        before = window.tree_root
        window.config['root_dir'] = str(tmp_path / 'нет-такой')

        window._on_settings_saved()

        assert window.tree_root == before

    def test_open_help_saves_config(self, window, monkeypatch):
        saved = []
        monkeypatch.setattr(gui, 'save_config',
                            lambda cfg: saved.append(True))
        stub_dialog(monkeypatch, 'HelpDialog')

        window.open_help()

        assert saved == [True]

    def test_open_help_survives_save_error(self, window, monkeypatch):
        def boom(cfg):
            raise OSError('нет доступа')

        monkeypatch.setattr(gui, 'save_config', boom)
        stub_dialog(monkeypatch, 'HelpDialog')

        window.open_help()   # не должно падать

    def test_open_about(self, window, msgbox):
        window.open_about()

        assert msgbox.kinds == ['information']
        assert 'ok-helper' in msgbox.texts()[0]

    def test_apply_theme_sets_stylesheet(self, window, qapp):
        window.config['theme'] = 'dark'

        window.apply_theme()

        assert qapp.styleSheet() == gui.build_stylesheet('dark')
        assert window.tree_model._theme_name == 'dark'
        assert window.ftp_model._theme_name == 'dark'

    def test_apply_theme_restyles_log(self, window, qapp):
        window.config['theme'] = 'dark'

        window.apply_theme()

        assert gui.THEMES['dark']['log_bg'] in window.log_edit.styleSheet()

    @pytest.mark.parametrize('setting,expected', [
        ('light', 'light'), ('dark', 'dark'),
    ])
    def test_apply_theme_explicit(self, window, setting, expected):
        window.config['theme'] = setting

        window.apply_theme()

        assert window.tree_model._theme_name == expected

    def test_apply_theme_auto(self, window, monkeypatch):
        window.config['theme'] = 'auto'
        monkeypatch.setattr(gui, '_system_is_dark', lambda: True)

        window.apply_theme()

        assert window.tree_model._theme_name == 'dark'
