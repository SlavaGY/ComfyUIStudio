"""
json_store.py
Загрузка/сохранение characters.json и prompt_builder_config.json.

Формат совместим с загрузчиком расширения (utils/json_loader.py,
utils/char_utils.py, utils/prompt_logic.py из character_search_ui):
- UTF-8, кириллица не экранируется (ensure_ascii=False) — так же, как
  в исходных файлах расширения.
- Перед каждым сохранением создаётся резервная копия рядом с файлом:
  <имя>.json.bak-YYYYMMDD-HHMMSS (сколько последних штук хранить —
  см. pb_settings.get_backup_keep(), настраивается из единого дерева
  настроек ComfyUI Studio; раньше было жёстко зашитым BACKUP_KEEP = 10
  здесь же).
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import time
from typing import Any

from comfyui_studio.prompt_builder.pb_settings import get_backup_keep


class JsonStoreError(Exception):
    pass


def load_json(path: str) -> Any:
    """Читает JSON-файл. Бросает JsonStoreError с понятным сообщением при ошибке."""
    if not os.path.isfile(path):
        raise JsonStoreError(f"Файл не найден: {path}")
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise JsonStoreError(
            f"Некорректный JSON в файле {os.path.basename(path)}:\n"
            f"строка {e.lineno}, столбец {e.colno}: {e.msg}"
        ) from e
    except OSError as e:
        raise JsonStoreError(f"Не удалось прочитать {path}: {e}") from e


def _make_backup(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_path = f"{path}.bak-{stamp}"
    try:
        shutil.copy2(path, backup_path)
    except OSError:
        return None

    # Подчищаем старые бэкапы, оставляя последние get_backup_keep() штук
    # (0 — не хранить ни одного, полностью отключить бэкапы этим же
    # переключателем не получится — сам файл на bak-момент уже
    # скопирован выше; keep=0 просто сразу удалит его же).
    try:
        pattern = f"{path}.bak-*"
        backups = sorted(glob.glob(pattern))
        excess = len(backups) - get_backup_keep()
        for old in backups[:max(0, excess)]:
            os.remove(old)
    except OSError:
        pass

    return backup_path


def save_json(path: str, data: Any, make_backup: bool = True) -> None:
    """Атомарно сохраняет JSON: пишет во временный файл рядом, затем заменяет целевой.
    Перед заменой создаёт резервную копию исходного файла (если он существовал)."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)

    if make_backup:
        _make_backup(path)

    tmp_path = f"{path}.tmp-{os.getpid()}"
    try:
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp_path, path)
    except OSError as e:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise JsonStoreError(f"Не удалось сохранить {path}: {e}") from e
