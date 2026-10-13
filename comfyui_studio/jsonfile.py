"""Атомарная запись и терпимое чтение JSON-файлов комплекта.

Зачем. Раньше ``config.json`` лаунчера писался прямо поверх файла
(``open(path, "w")``): авария, ``taskkill`` или отключение питания в
момент записи оставляли обрезанный файл, а ``load_config()`` молча
возвращал значения по умолчанию -- пользователь терял путь к ComfyUI,
порт, аргументы и переменные окружения без единого предупреждения.

* :func:`atomic_write_json` пишет во временный файл рядом и подменяет им
  целевой через ``os.replace`` -- читатель всегда видит либо старую, либо
  новую версию целиком.
* :func:`load_json` не бросает исключений, а возвращает :class:`JsonLoad`
  со статусом; при ``quarantine=True`` битый файл не затирается, а
  переименовывается в ``<имя>.corrupt-<время>``.

Модуль без зависимостей от Qt и остального пакета: его копия уезжает в
``rvc_app_src`` (см. ComfyUIStudio.spec) вместе с shared_theme.py и
shared_language.py, которые читает отдельный питон Ultimate RVC.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("comfyui_studio.jsonfile")

OK = "ok"
MISSING = "missing"
CORRUPT = "corrupt"  # файл есть, но это не JSON / не тот тип корня
UNREADABLE = "unreadable"  # ОС не дала прочитать (занят, нет прав)

_REPLACE_ATTEMPTS = 5


@dataclass
class JsonLoad:
    data: Any
    status: str
    error: str | None = None
    quarantined_to: str | None = None


def _replace_with_retry(src: str, dst: str) -> None:
    """os.replace; на Windows цель на мгновение может быть занята другим
    процессом (антивирус, индексатор, читающий Remote) -- несколько
    коротких повторов вместо немедленного отказа."""
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if sys.platform != "win32" or attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(0.05 * (attempt + 1))


def atomic_write_json(
    path,
    data,
    *,
    indent: int | None = 2,
    ensure_ascii: bool = False,
    newline: str | None = None,
    trailing_newline: bool = False,
) -> None:
    """Записывает ``data`` в ``path`` атомарно. Каталог создаётся при
    необходимости. Исключения (OSError, TypeError при несериализуемых
    данных) пробрасываются, целевой файл при этом остаётся прежним, а
    временный файл удаляется.

    ``newline`` -- как у ``open()`` (``"\n"`` отключает перевод строк в CRLF
    на Windows: нужно файлам, которые читает и правит кто-то ещё, например
    JSON Prompt Builder'а); ``trailing_newline`` дописывает перевод строки
    в конец файла."""
    path = os.fspath(path)
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=directory, prefix=os.path.basename(path) + ".", suffix=".tmp"
    )
    replaced = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline=newline) as f:
            json.dump(data, f, ensure_ascii=ensure_ascii, indent=indent)
            if trailing_newline:
                f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        _replace_with_retry(tmp_path, path)
        replaced = True
    finally:
        if not replaced:  # любая ошибка (и KeyboardInterrupt): не оставляем мусор
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _quarantine(path: str) -> str | None:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = f"{path}.corrupt-{stamp}"
    n = 1
    while os.path.exists(target):
        n += 1
        target = f"{path}.corrupt-{stamp}-{n}"
    try:
        os.replace(path, target)
    except OSError:
        log.exception("Не удалось отложить повреждённый файл %s", path)
        return None
    return target


def load_json(path, *, expected_type: type | tuple = dict, quarantine: bool = False) -> JsonLoad:
    """Читает JSON, не бросая исключений.

    * нет файла -> status ``missing`` (это не ошибка, в лог не пишется);
    * не JSON / не UTF-8 / корень не ``expected_type`` -> ``corrupt``; при
      ``quarantine=True`` файл переименовывается в ``<имя>.corrupt-<время>``
      (чтобы следующее сохранение не затёрло улики, а пользователь мог
      вытащить из него свои значения);
    * ОС не дала открыть файл -> ``unreadable``, файл не трогаем.
    """
    path = os.fspath(path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return JsonLoad(None, MISSING)
    except ValueError as e:  # JSONDecodeError и UnicodeDecodeError
        return _corrupt(path, f"{type(e).__name__}: {e}", quarantine)
    except OSError as e:
        log.warning("Не удалось прочитать %s: %s", path, e)
        return JsonLoad(None, UNREADABLE, str(e))
    if not isinstance(data, expected_type):
        name = getattr(expected_type, "__name__", str(expected_type))
        return _corrupt(path, f"корень JSON не {name}: {type(data).__name__}", quarantine)
    return JsonLoad(data, OK)


def _corrupt(path: str, error: str, quarantine: bool) -> JsonLoad:
    moved = _quarantine(path) if quarantine else None
    log.error("Файл %s повреждён (%s)%s", path, error, f"; сохранён как {moved}" if moved else "")
    return JsonLoad(None, CORRUPT, error, moved)


def load_json_or_default(path, default, *, expected_type: type | tuple = dict, quarantine: bool = False):
    """``load_json(...).data``, а при отсутствии/повреждении -- ``default``."""
    result = load_json(path, expected_type=expected_type, quarantine=quarantine)
    return result.data if result.status == OK else default
