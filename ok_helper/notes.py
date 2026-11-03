"""Текстовые заметки к папкам.

Заметка привязана к папке (абсолютному пути), а не к файлу: у каждой
папки может быть одна заметка. Хранятся заметки рядом с конфигом в
``notes.json`` — так они не раздувают ``config.toml`` и переживают
переименование ключей конфига.

Формат файла::

    {
      "version": 1,
      "notes": {
        "C:\\\\Tracks\\\\2026-09-23_Moscow": {
          "text": "Проверить трек перед заливкой",
          "updated": "2026-11-03T12:34:56"
        }
      }
    }

Читается и старый упрощённый вид ``{"путь": "текст"}`` — на случай
правки файла руками.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from config import config_path


NOTES_FILE_NAME = 'notes.json'

# Сколько символов заметки показывать в результатах поиска.
SNIPPET_LIMIT = 180

# Сколько символов контекста слева от найденного слова оставлять.
SNIPPET_LEFT = 60


@dataclass(frozen=True)
class Note:
    """Заметка: папка, текст и время последнего изменения."""

    path: Path
    text: str
    updated: str = ''


@dataclass(frozen=True)
class NoteMatch:
    """Совпадение при поиске по всем заметкам."""

    path: Path
    text: str
    snippet: str
    updated: str = ''


def notes_path_for(config: dict) -> Path:
    """Путь к файлу заметок рядом с config.toml.

    В тестах конфиг подменяется через ``_path``, поэтому файл заметок
    должен считаться от него, а не от реального %APPDATA%.
    """
    base = config.get('_path') if isinstance(config, dict) else None
    try:
        if base:
            return Path(base).with_name(NOTES_FILE_NAME)
    except (TypeError, ValueError):
        pass
    return config_path().with_name(NOTES_FILE_NAME)


def _normalize(path) -> tuple:
    """Возвращает (ключ для сравнения, путь для показа).

    На Windows пути регистронезависимы, поэтому ключ приводится через
    ``normcase``. Для отображения сохраняется исходный абсолютный путь.
    """
    p = Path(path).expanduser()
    try:
        resolved = p.resolve()
    except (OSError, RuntimeError):
        resolved = Path(os.path.abspath(str(p)))
    return os.path.normcase(str(resolved)), str(resolved)


def _flatten(text: str) -> str:
    """Переводы строк и повторные пробелы → одна строка."""
    return ' '.join(str(text).split())


def _make_snippet(text: str, terms: List[str]) -> str:
    """Короткий фрагмент заметки вокруг первого совпадения."""
    flat = _flatten(text)
    if not flat:
        return ''

    low = flat.casefold()
    positions = [low.find(term) for term in terms if term]
    positions = [pos for pos in positions if pos >= 0]
    if not positions:
        if len(flat) <= SNIPPET_LIMIT:
            return flat
        return flat[:SNIPPET_LIMIT] + '…'

    pos = min(positions)
    start = max(0, pos - SNIPPET_LEFT)
    end = min(len(flat), start + SNIPPET_LIMIT)
    # Если фрагмент короткий, добираем справа, чтобы окно было полным.
    start = max(0, min(start, end - SNIPPET_LIMIT))

    snippet = flat[start:end]
    if start > 0:
        snippet = '…' + snippet
    if end < len(flat):
        snippet = snippet + '…'
    return snippet


class NotesStore:
    """Заметки к папкам с сохранением в JSON.

    Ошибки чтения не мешают запуску: испорченный файл считается пустым,
    а причина остаётся в ``load_error``. Ошибки записи поднимаются
    наружу — вызывающий решает, показать их в журнале или плашкой.
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path is not None else None
        self.load_error = ''
        # Ключ (normcase) → Note. Путь внутри Note — для отображения.
        self._notes: Dict[str, Note] = {}
        if self.path is not None:
            self._load()

    # ---------- чтение / запись ----------
    def _load(self):
        try:
            raw = self.path.read_text(encoding='utf-8-sig')
        except FileNotFoundError:
            return
        except OSError as e:
            self.load_error = str(e)
            return

        try:
            data = json.loads(raw)
        except ValueError as e:
            self.load_error = str(e)
            return

        if isinstance(data, dict) and isinstance(data.get('notes'), dict):
            entries = data['notes']
        elif isinstance(data, dict):
            # Старый/ручной вид: {"путь": "текст"}.
            entries = data
        else:
            self.load_error = 'ожидался объект JSON'
            return

        for key, value in entries.items():
            if not isinstance(key, str):
                continue
            updated = ''
            if isinstance(value, str):
                text = value
            elif isinstance(value, dict):
                text = value.get('text', '')
                updated = str(value.get('updated', '') or '')
            else:
                continue
            if not isinstance(text, str) or not text.strip():
                continue
            norm, display = _normalize(key)
            self._notes[norm] = Note(Path(display), text, updated)

    def _serialize(self) -> str:
        notes: Dict[str, dict] = {}
        for key in sorted(self._notes, key=lambda k: k.casefold()):
            note = self._notes[key]
            notes[str(note.path)] = {
                'text': note.text,
                'updated': note.updated,
            }
        data = {'version': 1, 'notes': notes}
        return json.dumps(data, ensure_ascii=False, indent=2) + '\n'

    def save(self):
        """Пишет файл заметок атомарно (через временный файл)."""
        if self.path is None:
            raise OSError('не задан путь к файлу заметок')

        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = self._serialize()
        tmp_name = None
        try:
            fd, tmp_name = tempfile.mkstemp(
                prefix=self.path.name + '.', suffix='.tmp',
                dir=str(self.path.parent))
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(text)
            os.replace(tmp_name, self.path)
            tmp_name = None
        finally:
            if tmp_name is not None:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass

    # ---------- доступ ----------
    def __len__(self) -> int:
        return len(self._notes)

    def note(self, folder) -> Optional[Note]:
        """Заметка папки или None."""
        key, _ = _normalize(folder)
        return self._notes.get(key)

    def get(self, folder) -> str:
        """Текст заметки папки; пустая строка, если заметки нет."""
        note = self.note(folder)
        return note.text if note is not None else ''

    def set(self, folder, text: str) -> bool:
        """Сохраняет заметку. Пустой текст удаляет её.

        Возвращает True, если что-то действительно изменилось.
        """
        key, display = _normalize(folder)
        value = str(text)
        existing = self._notes.get(key)

        if not value.strip():
            if existing is None:
                return False
            del self._notes[key]
            self.save()
            return True

        if existing is not None and existing.text == value:
            return False

        self._notes[key] = Note(
            Path(display), value, datetime.now().isoformat(timespec='seconds'))
        self.save()
        return True

    def remove(self, folder) -> bool:
        """Удаляет заметку папки. Возвращает True, если она была."""
        key, _ = _normalize(folder)
        if key not in self._notes:
            return False
        del self._notes[key]
        self.save()
        return True

    def all_notes(self) -> List[Note]:
        """Все заметки в порядке пути."""
        return sorted(self._notes.values(),
                      key=lambda n: str(n.path).casefold())

    # ---------- поиск ----------
    @staticmethod
    def _terms(query: str) -> List[str]:
        return [part.casefold()
                for part in re.split(r'\s+', str(query).strip())
                if part]

    def search(self, query: str = '') -> List[NoteMatch]:
        """Ищет заметки по тексту и по пути папки.

        Запрос разбивается по пробелам: должны найтись все слова, каждое
        — либо в заметке, либо в пути. Регистр не важен. Пустой запрос
        возвращает все заметки.
        """
        terms = self._terms(query)
        matches: List[NoteMatch] = []

        for note in self._notes.values():
            text_low = note.text.casefold()
            path_low = str(note.path).casefold()
            if terms and not all(term in text_low or term in path_low
                                 for term in terms):
                continue
            matches.append(NoteMatch(
                path=note.path,
                text=note.text,
                snippet=_make_snippet(note.text, terms),
                updated=note.updated,
            ))

        matches.sort(key=lambda m: (m.path.name.casefold(),
                                    str(m.path).casefold()))
        return matches
