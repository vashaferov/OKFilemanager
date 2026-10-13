"""Тесты копирования в системный буфер обмена (ok_helper.clipboard).

Функциональность: copy_to_clipboard() — pyperclip, если установлен,
иначе системные утилиты: clip.exe / pbcopy / wl-copy / xclip / xsel.

Внешние зависимости подменяются, реальный буфер обмена не затрагивается.

Группировка в Allure:
  epic    = ok-helper-tui
  feature = Буфер обмена
  story   = задаётся маркером @pytest.mark.story на каждом классе.
"""

import sys
import types

import allure
import pytest

from ok_helper import clipboard


pytestmark = [
    pytest.mark.clipboard,
    pytest.mark.epic('ok-helper-tui'),
    pytest.mark.feature('Буфер обмена'),
]


class Recorder:
    """Запоминает вызовы subprocess.run и позволяет задать результат."""

    def __init__(self, result=True, exc=None):
        self.calls = []
        self.result = result
        self.exc = exc

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), kwargs))
        if self.exc is not None:
            raise self.exc
        return types.SimpleNamespace(returncode=0)


@pytest.fixture
def runner(monkeypatch):
    """Подменяет subprocess.run и убирает pyperclip из импорта."""
    rec = Recorder()
    monkeypatch.setattr(clipboard.subprocess, 'run', rec)
    # sys.modules[<name>] = None → `import pyperclip` падает ImportError.
    monkeypatch.setitem(sys.modules, 'pyperclip', None)
    return rec


@pytest.fixture(autouse=True)
def _no_qt_clipboard(monkeypatch):
    """Проверяем системные утилиты, а не буфер обмена Qt.

    Если в процессе уже создано QApplication (например, GUI-тесты шли
    раньше), реальный copy_to_clipboard ушёл бы в Qt и тесты утилит
    ничего бы не проверили.
    """
    monkeypatch.setattr(clipboard, '_qt_clipboard_copy', lambda text: False)


# ─────────────────────────────────────────────────────────────────
# 1. Ранние выходы
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Ранние выходы')
class TestEarlyExits:
    @pytest.mark.smoke
    def test_empty_text_returns_false(self, runner):
        assert clipboard.copy_to_clipboard('') is False

    def test_empty_text_does_not_call_anything(self, runner):
        clipboard.copy_to_clipboard('')
        assert runner.calls == []

    @pytest.mark.parametrize('text', ['', None])
    def test_falsy_text_is_rejected(self, runner, text):
        assert clipboard.copy_to_clipboard(text) is False


# ─────────────────────────────────────────────────────────────────
# 2. pyperclip — приоритетный путь
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('pyperclip')
class TestPyperclip:
    @pytest.mark.smoke
    def test_used_when_available(self, monkeypatch):
        copied = []
        fake = types.ModuleType('pyperclip')
        fake.copy = lambda text: copied.append(text)
        monkeypatch.setitem(sys.modules, 'pyperclip', fake)

        rec = Recorder()
        monkeypatch.setattr(clipboard.subprocess, 'run', rec)

        assert clipboard.copy_to_clipboard('Иванов') is True
        assert copied == ['Иванов']
        assert rec.calls == [], 'системная утилита не должна вызываться'

    def test_falling_pyperclip_falls_through(self, monkeypatch):
        """pyperclip есть, но бросает — пробуем системную утилиту."""
        fake = types.ModuleType('pyperclip')

        def boom(_text):
            raise RuntimeError('нет дисплея')

        fake.copy = boom
        monkeypatch.setitem(sys.modules, 'pyperclip', fake)

        rec = Recorder()
        monkeypatch.setattr(clipboard.subprocess, 'run', rec)
        monkeypatch.setattr(clipboard.sys, 'platform', 'win32')

        assert clipboard.copy_to_clipboard('текст') is True
        assert [c[0] for c in rec.calls] == [['clip']]

    def test_import_error_falls_through(self, runner, monkeypatch):
        monkeypatch.setattr(clipboard.sys, 'platform', 'win32')
        assert clipboard.copy_to_clipboard('текст') is True
        assert [c[0] for c in runner.calls] == [['clip']]


# ─────────────────────────────────────────────────────────────────
# 2b. Буфер обмена Qt — приоритетный путь в GUI-приложении
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Буфер обмена Qt')
class TestQtClipboard:
    @pytest.mark.smoke
    def test_qt_path_wins_over_system_tools(self, monkeypatch):
        """Если Qt-буфер доступен, внешние утилиты не запускаются."""
        monkeypatch.setattr(clipboard, '_qt_clipboard_copy',
                            lambda text: True)
        rec = Recorder()
        monkeypatch.setattr(clipboard.subprocess, 'run', rec)

        assert clipboard.copy_to_clipboard('Иванов') is True
        assert rec.calls == [], 'утилиты не должны вызываться при Qt-пути'

    def test_qt_miss_falls_back_to_tools(self, runner, monkeypatch):
        monkeypatch.setattr(clipboard.sys, 'platform', 'win32')

        assert clipboard.copy_to_clipboard('текст') is True
        assert [c[0] for c in runner.calls] == [['clip']]

    def test_empty_text_skips_qt(self, monkeypatch):
        called = []
        monkeypatch.setattr(clipboard, '_qt_clipboard_copy',
                            lambda text: called.append(text) or True)

        assert clipboard.copy_to_clipboard('') is False
        assert called == []


# ─────────────────────────────────────────────────────────────────
# 3. Системные утилиты по платформам
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Системные утилиты')
class TestPlatformCommands:
    @pytest.mark.smoke
    def test_windows_uses_clip_with_utf16(self, runner, monkeypatch):
        monkeypatch.setattr(clipboard.sys, 'platform', 'win32')
        assert clipboard.copy_to_clipboard('Ёжик') is True

        cmd, kwargs = runner.calls[0]
        assert cmd == ['clip']
        # UTF-16-LE, иначе кириллица превращается в кракозябры.
        assert kwargs['input'] == 'Ёжик'.encode('utf-16-le')
        assert kwargs['input'].decode('utf-16-le') == 'Ёжик'
        assert kwargs['timeout'] == 5
        assert kwargs['check'] is True

    def test_macos_uses_pbcopy_with_utf8(self, runner, monkeypatch):
        monkeypatch.setattr(clipboard.sys, 'platform', 'darwin')
        assert clipboard.copy_to_clipboard('Ёжик') is True

        cmd, kwargs = runner.calls[0]
        assert cmd == ['pbcopy']
        assert kwargs['input'] == 'Ёжик'.encode('utf-8')

    @pytest.mark.parametrize('available,expected', [
        ('wl-copy', ['wl-copy']),
        ('xclip',   ['xclip', '-selection', 'clipboard']),
        ('xsel',    ['xsel', '--clipboard', '--input']),
    ])
    def test_linux_prefers_first_available(self, runner, monkeypatch,
                                           available, expected):
        monkeypatch.setattr(clipboard.sys, 'platform', 'linux')
        monkeypatch.setattr(clipboard.shutil, 'which',
                            lambda name: f'/usr/bin/{name}'
                            if name == available else None)

        assert clipboard.copy_to_clipboard('текст') is True
        cmd, kwargs = runner.calls[0]
        assert cmd == expected
        assert kwargs['input'] == 'текст'.encode('utf-8')

    def test_linux_wl_copy_wins_over_xclip(self, runner, monkeypatch):
        monkeypatch.setattr(clipboard.sys, 'platform', 'linux')
        monkeypatch.setattr(clipboard.shutil, 'which', lambda name: '/usr/bin/'
                            + name)

        clipboard.copy_to_clipboard('текст')
        assert [c[0][0] for c in runner.calls] == ['wl-copy']

    def test_linux_falls_through_broken_wl_copy(self, runner, monkeypatch):
        monkeypatch.setattr(clipboard.sys, 'platform', 'linux')
        monkeypatch.setattr(clipboard.shutil, 'which', lambda name: '/usr/bin/'
                            + name)
        calls = []

        def run(cmd, **kwargs):
            calls.append(list(cmd))
            if cmd[0] == 'wl-copy':
                raise OSError('нет Wayland')
            return types.SimpleNamespace(returncode=0)

        monkeypatch.setattr(clipboard.subprocess, 'run', run)

        assert clipboard.copy_to_clipboard('текст') is True
        assert [c[0] for c in calls] == ['wl-copy', 'xclip']

    def test_linux_without_tools_returns_false(self, runner, monkeypatch):
        monkeypatch.setattr(clipboard.sys, 'platform', 'linux')
        monkeypatch.setattr(clipboard.shutil, 'which', lambda _name: None)

        assert clipboard.copy_to_clipboard('текст') is False
        assert runner.calls == []


# ─────────────────────────────────────────────────────────────────
# 4. Никогда не бросает исключений
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('Отказоустойчивость')
class TestNeverRaises:
    @pytest.mark.parametrize('exc', [
        OSError('нет файла'),
        RuntimeError('сломалось'),
        FileNotFoundError('нет clip.exe'),
        UnicodeError('кодировка'),
    ])
    def test_windows_failure_returns_false(self, monkeypatch, exc):
        monkeypatch.setitem(sys.modules, 'pyperclip', None)
        monkeypatch.setattr(clipboard.sys, 'platform', 'win32')
        monkeypatch.setattr(clipboard.subprocess, 'run', Recorder(exc=exc))

        assert clipboard.copy_to_clipboard('текст') is False

    def test_timeout_is_swallowed(self, monkeypatch):
        import subprocess as real_subprocess

        monkeypatch.setitem(sys.modules, 'pyperclip', None)
        monkeypatch.setattr(clipboard.sys, 'platform', 'win32')
        monkeypatch.setattr(
            clipboard.subprocess, 'run',
            Recorder(exc=real_subprocess.TimeoutExpired('clip', 5)))

        assert clipboard.copy_to_clipboard('текст') is False


# ─────────────────────────────────────────────────────────────────
# 5. _try_run — вспомогательная функция
# ─────────────────────────────────────────────────────────────────

@pytest.mark.story('_try_run')
class TestTryRun:
    def test_success_returns_true(self, monkeypatch):
        rec = Recorder()
        monkeypatch.setattr(clipboard.subprocess, 'run', rec)

        assert clipboard._try_run(['clip'], b'data') is True
        cmd, kwargs = rec.calls[0]
        assert cmd == ['clip']
        assert kwargs['input'] == b'data'

    def test_exception_returns_false(self, monkeypatch):
        monkeypatch.setattr(clipboard.subprocess, 'run',
                            Recorder(exc=OSError('нет')))
        assert clipboard._try_run(['nope'], b'') is False

    def test_nonzero_exit_returns_false(self, monkeypatch):
        import subprocess as real_subprocess

        def run(cmd, **kwargs):
            raise real_subprocess.CalledProcessError(1, cmd)

        monkeypatch.setattr(clipboard.subprocess, 'run', run)
        assert clipboard._try_run(['clip'], b'') is False
