"""Тесты открытия системного файлового менеджера (ok_helper.gui).

Отдельный файл: здесь проверяется не оформление, а точная команда,
которую получает Проводник, — на ней и была регрессия.

Группировка в Allure:
  epic    = ok-filemanager
  feature = Проводник
  story   = задаётся маркером @pytest.mark.story на каждом классе.
"""

from pathlib import Path

import allure
import pytest

from ok_helper import gui


pytestmark = [
    pytest.mark.gui,
    pytest.mark.epic('ok-filemanager'),
    pytest.mark.feature('Проводник'),
]


@pytest.mark.story('Команда Проводника')
class TestExplorerCommand:
    """Проводник понимает только /select,<путь> без кавычек вокруг ключа.

    Если путь с пробелами уходит одним аргументом, Python берёт в
    кавычки всю строку — «explorer "/select,C:\\...\\file.gpx"» — и
    Проводник открывает «Документы» вместо нужной папки.
    """

    def test_path_without_spaces(self):
        command = gui._explorer_select_command(
            Path(r'C:\Users\me\Downloads\track.gpx'))

        assert command == r'explorer /select,C:\Users\me\Downloads\track.gpx'

    def test_path_with_spaces_quotes_only_path(self):
        command = gui._explorer_select_command(
            Path(r'C:\Users\me\My Tracks\20260917_Lisa.gpx'))

        assert command == (
            r'explorer /select,"C:\Users\me\My Tracks\20260917_Lisa.gpx"')

    def test_key_is_never_inside_quotes(self):
        """Главная проверка регрессии: кавычка идёт после /select,."""
        for raw in (r'C:\a b\file.gpx', r'D:\LA\Треки\10-Tracks\100m.gpx',
                    r'C:\no-spaces\file.plt'):
            command = gui._explorer_select_command(Path(raw))

            assert command.startswith('explorer /select,')
            assert '"/select' not in command
            assert "/select,'" not in command

    def test_spaces_inside_path_stay_quoted(self):
        command = gui._explorer_select_command(
            Path(r'D:\LA\Треки с пробелом\100m.gpx'))

        assert command.endswith(r'100m.gpx"')
        assert 'Треки с пробелом' in command

    def test_embedded_quotes_removed(self):
        """Кавычка в имени сломала бы командную строку."""
        command = gui._explorer_select_command(
            Path(r'C:\tmp\странное "имя".gpx'))

        assert command.count('"') == 2
        assert 'имя' in command


@pytest.mark.story('Запуск Проводника')
class TestRevealInExplorer:
    def test_windows_uses_explorer(self, monkeypatch):
        calls = []

        class FakePopen:
            def __init__(self, command, **kwargs):
                calls.append((command, kwargs))

        monkeypatch.setattr(gui.sys, 'platform', 'win32')
        monkeypatch.setattr(gui.subprocess, 'Popen', FakePopen)

        result = gui.reveal_in_file_manager(Path(r'C:\My Tracks\track.gpx'))

        assert result is True
        assert calls
        command, kwargs = calls[0]
        assert command == r'explorer /select,"C:\My Tracks\track.gpx"'
        assert kwargs.get('shell') is False

    def test_macos_uses_open(self, monkeypatch):
        calls = []
        monkeypatch.setattr(gui.sys, 'platform', 'darwin')
        monkeypatch.setattr(gui.subprocess, 'Popen',
                            lambda args, **kw: calls.append(args))
        target = Path('/tmp/track.gpx')

        result = gui.reveal_in_file_manager(target)

        assert result is True
        # Разделители зависят от ОС: сверяем с тем, что даёт сам Path.
        assert calls == [['open', '-R', str(target)]]

    def test_start_error_is_reported(self, monkeypatch):
        monkeypatch.setattr(gui.sys, 'platform', 'win32')

        def boom(*a, **kw):
            raise OSError('нет Проводника')

        monkeypatch.setattr(gui.subprocess, 'Popen', boom)

        assert gui.reveal_in_file_manager(Path(r'C:\track.gpx')) is False
