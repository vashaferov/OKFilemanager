#!/usr/bin/env python3
"""Точка входа ok-filemanager.

GUI-версия на PySide6 (Qt 6). Только Windows.

Требуется пакет PySide6 — устанавливается через requirements.txt.
"""

import argparse
import sys
from pathlib import Path

from config import load_config, config_path


def main():
    parser = argparse.ArgumentParser(
        description='ok-filemanager: транслит и умная проверка имён файлов.')
    parser.add_argument(
        'directory', nargs='?', default=None,
        help='Начальная директория. Если не задана — берётся из конфига.',
    )
    args = parser.parse_args()

    config = load_config()

    if args.directory is None:
        d = config['root_dir_resolved']
    else:
        d = Path(args.directory).expanduser().resolve()

    if not d.is_dir():
        print(f'Не директория: {d}', file=sys.stderr)
        print(f'Проверьте root_dir в {config_path()}', file=sys.stderr)
        sys.exit(1)

    try:
        import PySide6  # noqa: F401
    except ImportError:
        print('PySide6 не установлен.', file=sys.stderr)
        print('Установите зависимости:', file=sys.stderr)
        print('  pip install -r requirements.txt', file=sys.stderr)
        sys.exit(1)

    from ok_helper.gui import run_gui
    run_gui(d, config)


if __name__ == '__main__':
    main()
