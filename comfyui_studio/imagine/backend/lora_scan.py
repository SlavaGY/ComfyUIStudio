"""
Поиск файлов LoRA сканированием папки на диске — тот же механизм, что
и в comfyui_studio/prompt_builder/lora_combo.py (scan_lora_files),
перенесённый без Qt-зависимости.

Почему не /object_info: у пользователя MultiLoraLoader — кастомный
узел (character_search_ui), и его combo-вход lora_1 на практике не
всегда воспроизводит актуальный список файлов через /object_info (см.
отчёт "ComfyUI не сообщил файлов LoRA" при живом запуске) — то ли из-за
того, как узел строит список COMBO при регистрации, то ли из-за
таймингов сканирования моделей самим ComfyUI. Прямое сканирование
папки на диске не зависит от того, что конкретно отдаёт кастомный узел,
и это ровно то, что уже проверенно работает в Studio.
"""

from pathlib import Path

LORA_FILE_EXTENSIONS = {".safetensors", ".pt", ".ckpt", ".bin"}


def scan_lora_files(folder: str) -> list[str]:
    """Рекурсивно ищет файлы LoRA в folder, возвращает отсортированный
    список путей относительно folder (разделитель '/', независимо от
    ОС — так же отдаёт path.as_posix() Studio-версия). Тихо возвращает
    [] на пустую/отсутствующую/недоступную папку."""
    if not folder:
        return []
    root = Path(folder)
    if not root.is_dir():
        return []
    results = []
    try:
        for p in root.rglob("*"):
            if p.is_file() and p.suffix.lower() in LORA_FILE_EXTENSIONS:
                results.append(p.relative_to(root).as_posix())
    except OSError:
        pass
    return sorted(results, key=str.lower)


def guess_lora_folder(bat_path: str) -> str:
    """Если пользователь ещё не указал папку LoRA явно, но уже указал
    launch_bat_path (портативный ComfyUI), пробуем угадать стандартный
    путь `<папка с .bat>/ComfyUI/models/loras`. Это только подсказка
    для первого запуска дев-режима — если её не существует, вызывающая
    сторона просто получит пустой список и покажет поле для ручного
    ввода пути."""
    if not bat_path:
        return ""
    bat_dir = Path(bat_path).parent
    candidate = bat_dir / "ComfyUI" / "models" / "loras"
    return str(candidate) if candidate.is_dir() else ""
