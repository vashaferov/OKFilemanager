"""Извлечение KML из KMZ.

KMZ — это ZIP-архив, внутри которого лежит один или несколько
файлов ``.kml``. Функция повторяет ручной сценарий:

  1. делаем копию ``.kmz`` и меняем у копии расширение на ``.zip``;
  2. из этого архива извлекаем файл ``.kml`` и называем его так же,
     как исходный ``.kmz``;
  3. удаляем промежуточную копию ``.zip`` (остаются только ``.kmz``
     и ``.kml``).
"""

import shutil
import zipfile
from pathlib import Path


class KmzError(Exception):
    """Ошибка распаковки KMZ."""


def _select_kml_entry(names) -> str:
    """Выбирает нужный ``.kml`` из списка имён архива.

    Приоритет отдаём каноническому ``doc.kml`` — именно так обычно
    называется основной файл внутри KMZ. Если его нет, берём первый
    ``.kml`` в порядке следования в архиве.
    """
    kmls = [n for n in names if n.lower().endswith('.kml')]
    if not kmls:
        raise KmzError('в архиве нет файла .kml')
    for name in kmls:
        if Path(name).name.lower() == 'doc.kml':
            return name
    return kmls[0]


def kmz_to_kml(kmz_path: Path) -> Path:
    """Распаковывает ``.kmz`` в одноимённый ``.kml``.

    Возвращает путь к созданному ``.kml``. Промежуточный ``.zip``
    удаляется в любом случае. При ошибке (нет файла, битый архив,
    внутри нет ``.kml``) поднимает :class:`KmzError`.
    """
    kmz_path = Path(kmz_path)
    if kmz_path.suffix.lower() != '.kmz':
        raise KmzError(f'не файл .kmz: {kmz_path.name}')
    if not kmz_path.is_file():
        raise KmzError(f'файл не найден: {kmz_path}')

    zip_path = kmz_path.with_suffix('.zip')
    kml_path = kmz_path.with_suffix('.kml')

    # Шаг 1: копия с расширением .zip.
    try:
        shutil.copyfile(kmz_path, zip_path)
    except OSError as e:
        raise KmzError(f'не создать копию .zip: {e}')

    try:
        # Шаг 2: достаём .kml и сохраняем под именем исходного .kmz.
        try:
            with zipfile.ZipFile(zip_path) as zf:
                entry = _select_kml_entry(zf.namelist())
                with zf.open(entry) as src, open(kml_path, 'wb') as dst:
                    shutil.copyfileobj(src, dst)
        except zipfile.BadZipFile:
            raise KmzError(f'не читается архив: {kmz_path.name}')
        except OSError as e:
            raise KmzError(f'не извлечь .kml: {e}')
    finally:
        # Шаг 3: убираем промежуточную копию .zip.
        try:
            zip_path.unlink()
        except OSError:
            pass

    return kml_path
