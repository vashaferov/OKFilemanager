"""FTP-загрузка, листинг и удаление файлов; валидация имени папки."""

import ftplib
import re
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .analyzer import analyze_file, is_m_gpx


_FOLDER_RE = re.compile(r'^(\d{4})-(\d{2})-(\d{2})_([A-Za-z0-9_-]+)$')

ALLOWED_UPLOAD_EXTENSIONS = {'.gpx', '.plt', '.wpt'}

_TENTRACKS_DIR_NAME = '10-tracks'
_MAX_10TRACKS_LOOKUP = 20


# ─────────────────────────────────────────────────────────────────
# Имена целевых папок
# ─────────────────────────────────────────────────────────────────

def is_target_folder_name(name: str) -> bool:
    return bool(_FOLDER_RE.match((name or '').strip()))


def validate_folder_name(name: str) -> Tuple[bool, str]:
    if not name or not name.strip():
        return False, 'пустое имя'
    m = _FOLDER_RE.match(name.strip())
    if not m:
        return False, (
            'формат ГГГГ-ММ-ДД_Место; допустимы латиница, цифры, «_», «-»'
        )
    y, mo, d = m.group(1), m.group(2), m.group(3)
    try:
        if not (1 <= int(mo) <= 12 and 1 <= int(d) <= 31):
            return False, 'некорректная дата (месяц/день)'
    except ValueError:
        return False, 'некорректная дата'
    return True, ''


def validate_upload_target(base_path: str, folder: str) -> Tuple[bool, str]:
    f = (folder or '').strip()
    if not f:
        return False, 'папка назначения не задана'
    if f in ('/', '\\', '.', '..'):
        return False, 'нельзя заливать файлы в корень сервера'
    if f.startswith('/') or f.startswith('\\'):
        return False, 'имя папки не должно начинаться с «/»'
    if any(part == '..' for part in re.split(r'[\\/]+', f)):
        return False, 'некорректный путь папки'
    return True, ''


# ─────────────────────────────────────────────────────────────────
# Политика отправки файлов
# ─────────────────────────────────────────────────────────────────

def is_in_10_tracks(path: Path, max_levels: int = _MAX_10TRACKS_LOOKUP) -> bool:
    cur = path.parent
    for _ in range(max_levels):
        if cur.name.lower() == _TENTRACKS_DIR_NAME:
            return True
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return False


def is_uploadable(path: Path, info: Optional[dict] = None) -> bool:
    suf = path.suffix.lower()
    if suf not in ALLOWED_UPLOAD_EXTENSIONS:
        return False
    if is_in_10_tracks(path):
        return False
    if suf == '.gpx':
        return is_m_gpx(path)
    if info is None:
        info = analyze_file(path)
    return not info['has_error']


def upload_extensions_hint() -> str:
    return '*.plt, *.wpt, *<1-4 цифры>m.gpx, *<1-2 цифры>km.gpx'


def upload_reason_for(path: Path, info: Optional[dict] = None) -> str:
    suf = path.suffix.lower()
    if suf not in ALLOWED_UPLOAD_EXTENSIONS:
        return f'расширение {path.suffix} не разрешено'
    if is_in_10_tracks(path):
        return 'файлы из папки «10-Tracks» на FTP не отправляются'
    if suf == '.gpx':
        return ('для .gpx разрешены только имена вида '
                '<1-4 цифры>m.gpx или <1-2 цифры>km.gpx')
    if info is None:
        info = analyze_file(path)
    return info['reason'] or 'имя не соответствует требованиям'


# ─────────────────────────────────────────────────────────────────
# Поиск целевой папки (локально)
# ─────────────────────────────────────────────────────────────────

def find_target_folder(start: Path, max_levels: int = 5) -> Tuple[str, bool]:
    cur = start
    for _ in range(max_levels + 1):
        if _FOLDER_RE.match(cur.name):
            return cur.name, True
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return start.name, False


class FtpError(Exception):
    pass


# ─────────────────────────────────────────────────────────────────
# Листинг папки
# ─────────────────────────────────────────────────────────────────

def _cwd_to_folder(ftp: ftplib.FTP, base_path: str, folder: str,
                   create_missing: bool = False,
                   log: Optional[Callable[[str], None]] = None) -> None:
    """Спускается по base_path/folder. Если create_missing=False —
    папки должны существовать, иначе FtpError."""
    def _log(msg: str):
        if log:
            log(msg)

    base = (base_path or '/').strip()
    if base and base != '/':
        for part in base.strip('/').split('/'):
            if not part:
                continue
            try:
                ftp.cwd(part)
            except ftplib.error_perm:
                if not create_missing:
                    raise FtpError(f'нет папки {part} на сервере')
                _log(f'[FTP] Создаю папку {part}')
                try:
                    ftp.mkd(part)
                except ftplib.error_perm as e:
                    raise FtpError(f'не создать {part}: {e}')
                ftp.cwd(part)

    for part in folder.strip('/').split('/'):
        if not part:
            continue
        try:
            ftp.cwd(part)
        except ftplib.error_perm:
            if not create_missing:
                raise FtpError(f'нет папки {part} на сервере')
            _log(f'[FTP] Создаю папку {part}')
            try:
                ftp.mkd(part)
            except ftplib.error_perm as e:
                raise FtpError(f'не создать папку {part}: {e}')
            ftp.cwd(part)


def list_folder(
    host: str,
    port: int,
    user: str,
    password: str,
    base_path: str,
    folder: str,
    log: Optional[Callable[[str], None]] = None,
) -> List[Tuple[str, bool, int]]:
    """Возвращает содержимое папки на FTP: [(name, is_dir, size), ...]."""
    def _log(msg: str):
        if log:
            log(msg)

    if not host:
        raise FtpError('ftp_host не задан в конфиге')
    if not user:
        raise FtpError('ftp_user не задан в конфиге')
    if not (folder or '').strip():
        raise FtpError('папка на сервере не задана')

    ftp = ftplib.FTP()
    try:
        ftp.connect(host, port, timeout=30)
        ftp.login(user, password)
        ftp.set_pasv(True)

        _cwd_to_folder(ftp, base_path, folder, create_missing=False, log=log)

        entries: List[Tuple[str, bool, int]] = []

        try:
            for name, facts in ftp.mlsd():
                if name in ('.', '..'):
                    continue
                t = (facts.get('type') or '').lower()
                if t == 'dir':
                    entries.append((name, True, 0))
                elif t == 'file':
                    try:
                        size = int(facts.get('size', '0'))
                    except (ValueError, TypeError):
                        size = 0
                    entries.append((name, False, size))
            _log(f'[FTP] Листинг {folder}: {len(entries)} элементов')
        except (ftplib.error_perm, ftplib.error_proto, AttributeError):
            _log('[FTP] MLSD не поддерживается, использую NLST')
            names = []
            try:
                names = ftp.nlst()
            except ftplib.all_errors as e:
                raise FtpError(f'не получить листинг: {e}')

            for name in names:
                if name in ('.', '..'):
                    continue
                if '/' in name:
                    name = name.rsplit('/', 1)[-1]
                try:
                    ftp.cwd(name)
                    ftp.cwd('..')
                    entries.append((name, True, 0))
                except ftplib.error_perm:
                    entries.append((name, False, 0))

        entries.sort(key=lambda e: (not e[1], e[0].lower()))
        return entries
    except ftplib.all_errors as e:
        raise FtpError(str(e))
    finally:
        try:
            ftp.quit()
        except Exception:
            try:
                ftp.close()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────
# Загрузка файла
# ─────────────────────────────────────────────────────────────────

def upload_file(
    host: str,
    port: int,
    user: str,
    password: str,
    base_path: str,
    folder: str,
    local_file: Path,
    log: Optional[Callable[[str], None]] = None,
    readonly: bool = False,
) -> None:
    """Загружает local_file в base_path/folder/ на FTP-сервере."""
    def _log(msg: str):
        if log:
            log(msg)

    if not host:
        raise FtpError('ftp_host не задан в конфиге')
    if not user:
        raise FtpError('ftp_user не задан в конфиге')

    if not is_uploadable(local_file):
        raise FtpError(
            f'недопустимый файл: {local_file.name} '
            f'({upload_reason_for(local_file)}); '
            f'разрешены {upload_extensions_hint()}'
        )

    ok, reason = validate_folder_name(folder)
    if not ok:
        raise FtpError(f'некорректное имя папки: {reason}')

    ok, reason = validate_upload_target(base_path, folder)
    if not ok:
        raise FtpError(reason)

    if not local_file.is_file():
        raise FtpError(f'локальный файл не найден: {local_file}')

    ftp = ftplib.FTP()
    try:
        _log(f'[FTP] Подключение к {host}:{port}...')
        ftp.connect(host, port, timeout=30)
        ftp.login(user, password)
        ftp.set_pasv(True)
        _log('[FTP] Авторизация OK')

        if readonly:
            _log('[FTP] *** РЕЖИМ ЭМУЛЯЦИИ: изменения на сервере '
                 'заблокированы ***')
            # В эмуляции: переходим и показываем, что «было бы».
            _cwd_to_folder(ftp, base_path, folder,
                           create_missing=False, log=log)
            try:
                size = local_file.stat().st_size
            except OSError:
                size = 0
            _log(f'[FTP] [эмуляция] {local_file.name} ({size} Б) был бы '
                 f'загружен в {folder}/ (запись пропущена)')
            _log('[FTP] [эмуляция] имитация завершена успешно')
            return

        # Реальная загрузка с созданием папок при необходимости.
        try:
            _cwd_to_folder(ftp, base_path, folder,
                           create_missing=True, log=log)
        except FtpError:
            # Возможно базовая папка не существует — попробуем с
            # созданием по частям.
            raise

        _log(f'[FTP] Папка {folder} готова')
        try:
            names = ftp.nlst()
            if local_file.name in names:
                _log(f'[FTP] Внимание: {local_file.name} уже есть '
                     f'на сервере — реальная загрузка перезапишет')
        except ftplib.all_errors:
            pass

        size = local_file.stat().st_size
        _log(f'[FTP] Загружаю {local_file.name} ({size} Б)...')
        with open(local_file, 'rb') as f:
            ftp.storbinary(f'STOR {local_file.name}', f)
        _log(f'[FTP] Готово: {folder}/{local_file.name}')
    except ftplib.all_errors as e:
        raise FtpError(str(e))
    finally:
        try:
            ftp.quit()
        except Exception:
            try:
                ftp.close()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────
# Удаление файла
# ─────────────────────────────────────────────────────────────────

def delete_file(
    host: str,
    port: int,
    user: str,
    password: str,
    base_path: str,
    folder: str,
    filename: str,
    log: Optional[Callable[[str], None]] = None,
    readonly: bool = False,
) -> None:
    """Удаляет файл filename из base_path/folder/ на FTP-сервере.

    В режиме эмуляции (readonly=True) выбрасывает FtpError — удаление
    запрещено и не выполняется. UI должен проверять флаг заранее и
    не открывать модалку подтверждения.
    """
    def _log(msg: str):
        if log:
            log(msg)

    if readonly:
        raise FtpError('удаление запрещено в режиме эмуляции '
                       '(ftp_readonly=true)')

    if not host:
        raise FtpError('ftp_host не задан в конфиге')
    if not user:
        raise FtpError('ftp_user не задан в конфиге')
    if not (folder or '').strip():
        raise FtpError('папка на сервере не задана')

    # Защита от path traversal и попыток уйти в родителя.
    name = (filename or '').strip()
    if (not name or name in ('.', '..')
            or any(c in name for c in ('/', '\\', '\x00'))):
        raise FtpError(f'некорректное имя файла: {filename!r}')

    ftp = ftplib.FTP()
    try:
        _log(f'[FTP] Подключение к {host}:{port}...')
        ftp.connect(host, port, timeout=30)
        ftp.login(user, password)
        ftp.set_pasv(True)

        _cwd_to_folder(ftp, base_path, folder,
                       create_missing=False, log=log)

        _log(f'[FTP] Удаляю {folder}/{name}...')
        try:
            ftp.delete(name)
        except ftplib.error_perm as e:
            raise FtpError(f'не удалить {name}: {e}')
        _log(f'[FTP] Удалён: {folder}/{name}')
    except ftplib.all_errors as e:
        raise FtpError(str(e))
    finally:
        try:
            ftp.quit()
        except Exception:
            try:
                ftp.close()
            except Exception:
                pass
