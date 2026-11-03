"""Анализ имён файлов.

Правила валидации:

  • .gpx и .plt — общая проверка: имя должно быть
    либо в формате «ГГГГММДД_Позывной», либо распознаваемым
    «скачанным» (дата в начале и позывной в остатке).

    Позывной нормализуется:
      – первая буква — заглавная ('lisa' → 'Lisa');
      – хвостовой номер (1–4 цифры после букв) — минимум 2 разряда,
        разделитель '_'/'-' перед ним убирается. Номер может быть
        как в самом конце позывного, так и в середине (с суффиксом
        после него):
            'lisa_1'         → 'Lisa01'
            'lisa1'          → 'Lisa01'
            'lisa-2'         → 'Lisa02'
            'lisa_12'        → 'Lisa12'
            'Lisa_03_Test'   → 'Lisa03_Test'
            'Lisa_04_1'      → 'Lisa04_1'
      – если база УЖЕ оканчивается цифрой, значит номер уже в ней,
        хвостовая группа `_N`/`-N` не трогается
        ('Lisa01_1' → 'Lisa01_1').

  • .gpx без даты в имени — дополнительно читаем содержимое
    файла и берём дату из самой последней временной метки
    (тег <time> в треке).

  • <1-4 цифры>m.gpx и <1-2 цифры>km.gpx — исключение:
    валидация не проводится.

  • .wpt — только в формате Waypoints_ГГГГММДД.

  • Прочие расширения — не проверяются.
"""

import re
from pathlib import Path
from typing import Optional, Tuple

from .translit import transliterate


_LATIN_CALLSIGN_RE = re.compile(r'^[A-Za-z0-9_-]+$')
_FILENAME_RE = re.compile(r'^(\d{8})_(.+)$')

# <1-4 цифры>m.gpx / <1-2 цифры>km.gpx — суффикс дистанции.
_GPX_SIZE_SUFFIX_RE = re.compile(
    r'(?<!\d)(?:\d{1,4}m|\d{1,2}km)$', re.IGNORECASE)

# Waypoints_ГГГГММДД — единственный допустимый формат .wpt.
_WPT_NAME_RE = re.compile(r'^Waypoints_(\d{8})$', re.IGNORECASE)

_DATE_PATTERNS = [
    (re.compile(r'^(\d{4})[-_.](\d{2})[-_.](\d{2})'), 'ymd'),
    (re.compile(r'^(\d{4})(\d{2})(\d{2})'),          'ymd'),
    (re.compile(r'^(\d{2})[-_.](\d{2})[-_.](\d{4})'), 'dmy'),
    (re.compile(r'^(\d{2})(\d{2})(\d{4})'),          'dmy'),
]

_STOPWORDS_LATIN = {
    'bezymyannyy', 'bezymyannaya', 'bez', 'imeni', 'untitled',
    'unnamed', 'track', 'trek', 'treka', 'gpx', 'record',
    'zapis', 'download', 'zagruzka', 'new', 'novyy', 'novaya',
}

_TIME_PREFIX_RE = re.compile(r'^\s*\d{1,2}[-_:.]?\d{2}(?:[-_:.]?\d{2})?\s*')

_LEADING_SEP = ' \t_-–—.'

# Правило 1: буквы + опциональный разделитель + 1–4 цифры +
# (либо конец строки, либо _/- и остаток). Обрабатывает номер как
# в конце, так и в середине позывного.
_CALLSIGN_NUM_RE = re.compile(
    r'^(?P<letters>[A-Za-z]+)'
    r'(?P<sep>[_-]?)'
    r'(?P<num>\d{1,4})'
    r'(?P<tail>(?:[_-].*)?)$'
)

# Группа и её номер одной частью: 'Lisa03' → base='Lisa', num='03'.
_GROUP_NUM_RE = re.compile(
    r'^(?P<base>[A-Za-z][A-Za-z0-9_-]*?)(?P<num>\d{1,4})$')

# Правило 2 (страховка для позывных, не начинающихся с букв):
#   1) base + ('_'|'-') + digits — с явным разделителем;
#   2) pure-letter base + digits — без разделителя.
_TRAILING_NUM_SEP_RE = re.compile(r'^(?P<base>.+?)(?P<sep>[_-])(?P<num>\d+)$')
_TRAILING_NUM_GLUED_RE = re.compile(r'^(?P<base>[A-Za-z]+)(?P<num>\d+)$')

# Дата в содержимом .gpx.
_GPX_DATE_RE = re.compile(rb'(\d{4})-(\d{2})-(\d{2})')
_GPX_TAIL_BYTES = 256 * 1024

# Запрещённые символы в именах файлов/папок.
_INVALID_NAME_CHARS = set('/\\:*?"<>|\x00')


def _ok() -> dict:
    return {'has_error': False, 'new_name': None,
            'confidence': 'none', 'reason': ''}


def is_m_gpx(path: Path) -> bool:
    """True, если path — это *.gpx с суффиксом дистанции перед расширением.

    Под исключение попадают имена вида ``100m.gpx`` (<1-4 цифры>m)
    и ``10km.gpx`` (<1-2 цифры>km).
    """
    return (path.suffix.lower() == '.gpx'
            and bool(_GPX_SIZE_SUFFIX_RE.search(path.stem)))


def validate_new_folder_name(name: str) -> Tuple[bool, str]:
    """Проверяет имя новой папки (для локального создания)."""
    if not name or not name.strip():
        return False, 'пустое имя'
    name = name.strip()
    if name in ('.', '..'):
        return False, 'некорректное имя'
    if any(c in _INVALID_NAME_CHARS for c in name):
        return False, 'имя содержит недопустимые символы (\\ / : * ? " < > |)'
    if name.endswith('.'):
        return False, 'имя не может оканчиваться точкой'
    return True, ''


def sanitize_callsign(cs: str) -> str:
    """Приводит к латинице и вырезает недопустимые символы.

    Не трогает регистр и хвостовой номер — это делает
    normalize_callsign().
    """
    if _LATIN_CALLSIGN_RE.match(cs):
        return cs
    return re.sub(r'[^A-Za-z0-9_-]', '', transliterate(cs))


def normalize_callsign(cs: str) -> str:
    """Приводит позывной к каноническому виду.

    Правила:
      • первая буква — заглавная ('lisa' → 'Lisa');
      • хвостовой номер (1–4 цифры после букв) — минимум 2 разряда,
        разделитель '_'/'-' перед ним убирается. Номер может быть
        как в самом конце позывного, так и в середине (с суффиксом
        после него):
            'lisa_1'         → 'Lisa01'
            'lisa1'          → 'Lisa01'
            'lisa-2'         → 'Lisa02'
            'lisa_12'        → 'Lisa12'
            'Lisa_03_Test'   → 'Lisa03_Test'
            'Lisa_04_1'      → 'Lisa04_1'
      • если база УЖЕ оканчивается цифрой, значит номер уже в ней —
        хвостовая группа `_N`/`-N` не трогается
        ('Lisa01_1' → 'Lisa01_1', 'lisa01_1' → 'Lisa01_1').

    Идемпотентна: normalize(normalize(x)) == normalize(x).
    """
    if not cs:
        return cs

    # Правило 1: буквы, опционально разделитель, 1–4 цифры,
    # затем либо конец строки, либо _/- с остатком.
    m = _CALLSIGN_NUM_RE.match(cs)
    if m:
        letters = m.group('letters')
        num = m.group('num')
        tail = m.group('tail') or ''
        if len(num) < 2:
            num = num.zfill(2)
        letters = letters[0].upper() + letters[1:]
        return letters + num + tail

    # Правило 2 (страховка): позывной не начинается с букв.
    base, num, sep = cs, None, ''
    m = _TRAILING_NUM_SEP_RE.match(cs)
    if m:
        base = m.group('base')
        sep = m.group('sep')
        num = m.group('num')
    else:
        m = _TRAILING_NUM_GLUED_RE.match(cs)
        if m:
            base = m.group('base')
            num = m.group('num')

    base = base.rstrip('_-')
    if not base:
        return cs

    base = base[0].upper() + base[1:]

    if num is None:
        return base

    if base[-1].isdigit():
        return base + sep + num

    if len(num) < 2:
        num = num.zfill(2)

    return base + num


def extract_date_from_gpx(path: Path) -> Optional[str]:
    """Возвращает ГГГГММДД из содержимого .gpx или None."""
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size == 0:
        return None

    read_size = min(size, _GPX_TAIL_BYTES)
    try:
        with open(path, 'rb') as f:
            if size > read_size:
                f.seek(size - read_size)
            data = f.read(read_size)
    except OSError:
        return None

    matches = list(_GPX_DATE_RE.finditer(data))
    if not matches:
        return None

    for m in reversed(matches):
        y, mo, d = m.group(1), m.group(2), m.group(3)
        try:
            if not (1 <= int(mo) <= 12 and 1 <= int(d) <= 31):
                continue
            return f'{y.decode()}{mo.decode()}{d.decode()}'
        except (ValueError, UnicodeDecodeError):
            continue
    return None


def _extract_date_and_rest(stem: str) -> Tuple[Optional[str], str]:
    for pat, kind in _DATE_PATTERNS:
        m = pat.match(stem)
        if not m:
            continue
        a, b, c = m.groups()
        try:
            if kind == 'ymd':
                y, mo, d = a, b, c
            else:
                d, mo, y = a, b, c
            if not (1 <= int(mo) <= 12 and 1 <= int(d) <= 31):
                continue
            return f'{y}{mo}{d}', stem[m.end():]
        except (ValueError, IndexError):
            continue
    return None, stem


def _clean_rest(rest: str) -> Tuple[str, bool]:
    rest = rest.strip()
    rest = rest.lstrip(_LEADING_SEP)
    rest = _TIME_PREFIX_RE.sub('', rest)
    rest = re.sub(r'[\s\-–—_.]+', '_', rest)
    rest = transliterate(rest)
    rest = re.sub(r'[^A-Za-z0-9_-]', '', rest)
    rest = re.sub(r'_+', '_', rest).strip('_-')
    if not rest:
        return '', True
    words = [w for w in re.split(r'[_-]+', rest.lower()) if w]
    is_placeholder = bool(words) and all(w in _STOPWORDS_LATIN for w in words)
    return rest, is_placeholder


def clean_name_part(value: str) -> str:
    """Готовит часть имени (группу или позывной) к записи в файл.

    Пробелы и дефисы становятся разделителями, кириллица — латиницей,
    всё недопустимое вырезается.
    """
    if not value:
        return ''
    text = transliterate(value.strip())
    text = re.sub(r'[\s\-–—.]+', '_', text)
    text = re.sub(r'[^A-Za-z0-9_-]', '', text)
    text = re.sub(r'_+', '_', text).strip('_-')
    return text


def normalize_group_name(value: str) -> str:
    """Имя группы: латиница, первая буква заглавная."""
    cleaned = clean_name_part(value)
    if not cleaned:
        return ''
    return cleaned[0].upper() + cleaned[1:]


def split_name_parts(stem: str, known_groups=()) -> dict:
    """Разбирает имя файла на дату, группу, номер и позывной.

    Понимает и слитную запись ``Lisa03_Test``, и раздельную
    ``Lisa_03_Test``. Возвращает пустые строки для отсутствующих частей.
    Расширение в ``stem`` передавать не нужно.

    ``known_groups`` — имена из словаря: они помогают разобрать группу
    из нескольких слов (``North_Team_03_Test``), которую иначе нельзя
    отличить от группы ``North`` с позывным ``Team``.
    """
    date_str, rest = _extract_date_and_rest(stem)
    cleaned, _ = _clean_rest(rest if date_str else stem)

    known = []
    for value in known_groups or ():
        name = normalize_group_name(value)
        if name and name not in known:
            known.append(name)

    # Словарь знает группу целиком — он точнее эвристики ниже.
    for name in sorted(known, key=len, reverse=True):
        low_name = name.casefold()
        low_cleaned = cleaned.casefold()
        if not low_cleaned.startswith(low_name):
            continue

        after = cleaned[len(name):]
        # Граница группы: конец имени, цифры номера или разделитель.
        # Иначе 'Lisa' не должна съедать группу 'Lisabel'.
        if after and not (after[0].isdigit() or after[0] == '_'):
            continue
        if after.startswith('_'):
            after = after[1:]

        tokens = [t for t in after.split('_') if t]
        number = ''
        if tokens and tokens[0].isdigit() and len(tokens[0]) <= 4:
            number = tokens[0]
            tokens = tokens[1:]
        callsign = '_'.join(tokens)
        return {
            'date': date_str or '',
            'group': name,
            'number': number,
            'callsign': normalize_callsign(callsign) if callsign else '',
        }

    tokens = [t for t in cleaned.split('_') if t]
    group = number = callsign = ''
    if tokens:
        head = tokens[0]
        tail = tokens[1:]
        m = _GROUP_NUM_RE.match(head)
        if m:
            group = m.group('base')
            number = m.group('num')
        else:
            group = head

        # Раздельная запись: Lisa_03_Test — номер отдельным словом.
        if not number and tail and tail[0].isdigit() and len(tail[0]) <= 4:
            number = tail[0]
            tail = tail[1:]

        callsign = '_'.join(tail)

    return {
        'date': date_str or '',
        'group': normalize_group_name(group),
        'number': number,
        'callsign': normalize_callsign(callsign) if callsign else '',
    }


def build_file_name(date: str, group: str, number: str = '',
                    callsign: str = '', suffix: str = '') -> str:
    """Собирает имя файла ``ГГГГММДД_ГруппаНомер_Позывной``.

    Номер из одной цифры дополняется нулём (``3`` → ``03``), как и при
    нормализации позывного. Пустой позывной не добавляет ``_``.
    """
    date = re.sub(r'\D', '', date or '')
    group = normalize_group_name(group)
    number = re.sub(r'\D', '', number or '')
    if len(number) == 1:
        number = number.zfill(2)
    callsign = clean_name_part(callsign)
    if callsign:
        callsign = normalize_callsign(callsign)

    stem = f'{date}_{group}{number}'
    if callsign:
        stem += f'_{callsign}'
    return f'{stem}{suffix}'


def _analyze_gpx_plt(stem: str, suffix: str) -> dict:
    """Общая проверка имён .gpx и .plt (без чтения содержимого)."""
    m = _FILENAME_RE.match(stem)
    if m:
        date, cs = m.groups()
        new_cs = normalize_callsign(sanitize_callsign(cs))
        if new_cs == cs and _LATIN_CALLSIGN_RE.match(cs):
            return _ok()
        if new_cs and _LATIN_CALLSIGN_RE.match(new_cs):
            return {'has_error': True,
                    'new_name': f'{date}_{new_cs}{suffix}',
                    'confidence': 'high',
                    'reason': 'нормализация позывного'}
        return {'has_error': True, 'new_name': None,
                'confidence': 'none', 'reason': 'позывной не исправить'}

    date_str, rest = _extract_date_and_rest(stem)
    if date_str is None:
        return {'has_error': True, 'new_name': None,
                'confidence': 'none', 'reason': 'дата в имени не найдена'}

    cleaned, is_placeholder = _clean_rest(rest)
    if not cleaned:
        return {'has_error': True, 'new_name': None,
                'confidence': 'low',
                'reason': f'дата найдена ({date_str}), позывной пуст'}

    normalized = normalize_callsign(cleaned)
    if is_placeholder:
        return {'has_error': True,
                'new_name': f'{date_str}_{normalized}{suffix}',
                'confidence': 'low',
                'reason': f'похоже на заглушку «{cleaned}»'}
    return {'has_error': True,
            'new_name': f'{date_str}_{normalized}{suffix}',
            'confidence': 'medium',
            'reason': 'дата + позывной из имени'}


def _analyze_gpx_with_content(path: Path, stem: str, suffix: str) -> dict:
    """Fallback для .gpx без даты в имени."""
    date_from_content = extract_date_from_gpx(path)
    if not date_from_content:
        return {'has_error': True, 'new_name': None,
                'confidence': 'none',
                'reason': 'дата не найдена ни в имени, ни в файле'}

    cleaned, is_placeholder = _clean_rest(stem)
    if not cleaned:
        return {'has_error': True, 'new_name': None,
                'confidence': 'low',
                'reason': f'дата {date_from_content} из файла, '
                          f'но имя не даёт позывного'}

    normalized = normalize_callsign(cleaned)
    new_name = f'{date_from_content}_{normalized}{suffix}'
    if is_placeholder:
        return {'has_error': True,
                'new_name': new_name,
                'confidence': 'low',
                'reason': f'дата {date_from_content} из содержимого файла; '
                          f'имя похоже на заглушку'}
    return {'has_error': True,
            'new_name': new_name,
            'confidence': 'medium',
            'reason': f'дата {date_from_content} из содержимого файла'}


def _analyze_wpt(stem: str, suffix: str) -> dict:
    """Проверка имён .wpt: только Waypoints_ГГГГММДД."""
    if _WPT_NAME_RE.match(stem):
        return _ok()

    date_str, _ = _extract_date_and_rest(stem)
    if date_str:
        return {'has_error': True,
                'new_name': f'Waypoints_{date_str}{suffix}',
                'confidence': 'medium',
                'reason': 'приведём к Waypoints_ГГГГММДД'}
    return {'has_error': True, 'new_name': None,
            'confidence': 'none',
            'reason': 'нужен формат Waypoints_ГГГГММДД'}


def analyze_file(path: Path) -> dict:
    """Возвращает: has_error, new_name, confidence, reason."""
    if not path.is_file():
        return _ok()

    suffix_lower = path.suffix.lower()
    stem = path.stem
    suffix = path.suffix

    if is_m_gpx(path):
        return _ok()

    if suffix_lower == '.wpt':
        return _analyze_wpt(stem, suffix)

    if suffix_lower in ('.gpx', '.plt'):
        result = _analyze_gpx_plt(stem, suffix)
        if (result['has_error'] and result['new_name'] is None
                and result['reason'] == 'дата в имени не найдена'
                and suffix_lower == '.gpx'):
            return _analyze_gpx_with_content(path, stem, suffix)
        return result

    return _ok()


def human_size(n: int) -> str:
    x = float(n)
    for unit in ('Б', 'КБ', 'МБ', 'ГБ', 'ТБ'):
        if x < 1024 or unit == 'ТБ':
            if unit == 'Б':
                return f'{int(x)} {unit}'
            return f'{x:.1f} {unit}'
        x /= 1024
    return f'{x:.1f} ТБ'
