"""
Конфигурация лаунчера: чтение/запись config.json, поиск и подготовка
скриптов запуска ComfyUI portable, аргументы командной строки.

Вынесено из comfyui_launcher.py (этап 1 дорожной карты).
"""

import os
import json

from .constants import APP_DIR, CONFIG_PATH, DEFAULT_CONFIG, LAUNCH_SCRIPT_TMP
from .logging_setup import log


def load_config():
    os.makedirs(APP_DIR, exist_ok=True)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg = DEFAULT_CONFIG.copy()
            cfg.update(data)
            return cfg
        except Exception:
            log.exception("Не удалось прочитать config.json, используем значения по умолчанию")
    return DEFAULT_CONFIG.copy()



def save_config(cfg):
    os.makedirs(APP_DIR, exist_ok=True)
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        log.exception("Не удалось сохранить config.json")


# --------------------------------------------------------------------------
# Работа с папкой ComfyUI portable
# --------------------------------------------------------------------------


def find_run_scripts(root_path):
    """Ищет run_*.bat в корне и в подпапке advanced/."""
    scripts = []
    if not root_path or not os.path.isdir(root_path):
        return scripts
    for entry in sorted(os.listdir(root_path)):
        low = entry.lower()
        if low.startswith("run_") and low.endswith(".bat"):
            scripts.append(entry)
    adv = os.path.join(root_path, "advanced")
    if os.path.isdir(adv):
        for entry in sorted(os.listdir(adv)):
            low = entry.lower()
            if low.startswith("run_") and low.endswith(".bat"):
                scripts.append(os.path.join("advanced", entry))
    return scripts



def validate_portable_root(root_path):
    if not root_path or not os.path.isdir(root_path):
        return False, "Указанная папка не существует"
    py = os.path.join(root_path, "python_embeded", "python.exe")
    main_py = os.path.join(root_path, "ComfyUI", "main.py")
    if not os.path.isfile(py):
        return False, "Не найден python_embeded\\python.exe — это не похоже на ComfyUI portable"
    if not os.path.isfile(main_py):
        return False, "Не найден ComfyUI\\main.py"
    if not find_run_scripts(root_path):
        return False, "В папке не найдено ни одного run_*.bat"
    return True, "OK"



def guess_default_script(scripts):
    for name in scripts:
        if os.path.basename(name).lower() == "run_nvidia_gpu.bat":
            return name
    for name in scripts:
        if os.path.basename(name).lower() == "run_cpu.bat":
            return name
    return scripts[0] if scripts else ""



# Каталог доп. аргументов командной строки ComfyUI (main.py), которые
# можно включить чекбоксом в LaunchArgsDialog (см. ниже) — вместо того,
# чтобы заставлять пользователя редактировать сам .bat вручную. Список не
# исчерпывающий: несколько наиболее часто нужных флагов из документации
# ComfyUI (VRAM-режимы, сетевой доступ, превью, доп. пути к моделям).
# takes_value=True + value_required=False (только --listen) означает, что
# флаг можно включить и без значения (ComfyUI сам возьмёт 0.0.0.0).
LAUNCH_ARG_DEFS = [
    dict(
        id="listen", flag="--listen", takes_value=True, value_required=False,
        placeholder="0.0.0.0",
        desc_ru=(
            "Слушать на всех сетевых интерфейсах — ComfyUI станет доступен "
            "с других устройств в локальной сети. Поле необязательно: IP "
            "для прослушивания (по умолчанию — все интерфейсы)."
        ),
    ),
    dict(
        id="cpu", flag="--cpu", takes_value=False,
        desc_ru="Считать только на CPU, без GPU (медленно, но работает без видеокарты).",
    ),
    dict(
        id="lowvram", flag="--lowvram", takes_value=False,
        desc_ru="Меньше VRAM ценой части скорости — для видеокарт с небольшим объёмом VRAM.",
    ),
    dict(
        id="novram", flag="--novram", takes_value=False,
        desc_ru="Минимум VRAM — для видеокарт с очень малым объёмом VRAM (если --lowvram не помогает).",
    ),
    dict(
        id="highvram", flag="--highvram", takes_value=False,
        desc_ru="Держать модели в VRAM постоянно — для видеокарт с большим запасом VRAM.",
    ),
    dict(
        id="gpu_only", flag="--gpu-only", takes_value=False,
        desc_ru=(
            "Держать вообще всё, включая текстовые энкодеры, в VRAM — для "
            "видеокарт с очень большим запасом VRAM."
        ),
    ),
    dict(
        id="reserve_vram", flag="--reserve-vram", takes_value=True, value_required=True,
        placeholder="2",
        desc_ru="Зарезервировать под другие программы указанный объём VRAM, в гигабайтах.",
    ),
    dict(
        id="disable_xformers", flag="--disable-xformers", takes_value=False,
        desc_ru="Отключить оптимизацию xFormers (если из-за неё возникают ошибки или чёрные изображения).",
    ),
    dict(
        id="preview_method", flag="--preview-method", takes_value=True, value_required=True,
        placeholder="taesd",
        desc_ru="Способ показа превью во время генерации: none, auto, latent2rgb или taesd.",
    ),
    dict(
        id="extra_model_paths_config", flag="--extra-model-paths-config",
        takes_value=True, value_required=True,
        placeholder=r"C:\path\to\extra_model_paths.yaml",
        desc_ru=(
            "Загрузить дополнительный файл extra_model_paths.yaml с путями "
            "к моделям/LoRA и т.п., лежащим вне папки ComfyUI."
        ),
    ),
]


def build_extra_launch_args(cfg):
    """Собирает итоговый список CLI-флагов из cfg["launch_args"]
    (заполняется LaunchArgsDialog) плюс отдельно хранящийся
    disable_auto_launch — единый список строк для prepare_launch_script."""
    args = []
    if cfg.get("disable_auto_launch"):
        args.append("--disable-auto-launch")
    launch_args = cfg.get("launch_args", {})
    for d in LAUNCH_ARG_DEFS:
        entry = launch_args.get(d["id"], {})
        if not entry.get("enabled"):
            continue
        if d.get("takes_value"):
            value = (entry.get("value") or "").strip()
            if value:
                args.append(f"{d['flag']} {value}")
            elif not d.get("value_required"):
                args.append(d["flag"])
            # value_required и пусто — пропускаем молча, чекбокс без
            # значения для такого флага смысла не имеет.
        else:
            args.append(d["flag"])
    return args



def prepare_launch_script(root_path, script_rel_path, extra_args):
    """
    Копирует выбранный .bat во временный файл, добавляя к строке запуска
    python.exe main.py переданные доп. аргументы (см. LAUNCH_ARG_DEFS и
    build_extra_launch_args выше) — чтобы не редактировать сам .bat
    вручную. Исходный .bat в папке ComfyUI не изменяется.
    """
    src_abs = os.path.join(root_path, script_rel_path)
    with open(src_abs, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    if extra_args:
        suffix = " " + " ".join(extra_args)
        new_lines = []
        for line in lines:
            low = line.lower()
            if "python.exe" in low and "main.py" in low:
                line = line.rstrip("\r\n") + suffix + os.linesep
            new_lines.append(line)
        lines = new_lines

    os.makedirs(APP_DIR, exist_ok=True)
    with open(LAUNCH_SCRIPT_TMP, "w", encoding="utf-8", errors="ignore") as f:
        f.writelines(lines)
    return LAUNCH_SCRIPT_TMP

