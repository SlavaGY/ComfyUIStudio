"""
Хранилище конфигурации приложения (data/config.json). Дев-режим
редактирует, обычный режим только читает через GET /api/config.

Схема — ДВЕ независимые, сосуществующие системы выбора стиля:

  styles: [                       # плоский каталог кликабельных плашек
    {
      id, label,
      image,          # относительный URL картинки-превью (/media/...) или ""
      target,         # "positive" | "negative" -- куда добавляется prompt_text
      prompt_text,    # текст, добавляемый в target при выборе бокса (может быть "")
      lora_file,      # имя файла LoRA относительно lora_folder, или "" (бокс без LoRA)
      lora_strength,  # float, допускает отрицательные значения
      trigger_words,  # текст, всегда добавляется в ПОЗИТИВНЫЙ промпт при выборе LoRA-бокса
    },
    ...
  ]

  boxes: [                         # дерево категорий-плашек, ПРОИЗВОЛЬНОЙ глубины
    {
      id, label, type: "category", # плашка с выпадающим списком вариантов
      options: [
        {
          id, label,
          positive_text, negative_text,   # добавляются напрямую, без trigger_words
          lora_file, lora_strength,       # тоже допускает отрицательные значения
        },
        ...
      ],
    },
    {
      id, label, type: "group",    # "категория категорий" -- просто контейнер,
      children: [ <узел boxes любого типа, рекурсивно> ... ],
    },
    ...
  ]

"boxes" -- дерево: узел типа "category" -- лист с выпадающим списком
(как раньше), узел типа "group" -- контейнер без собственного выбора,
может содержать любые другие узлы (в т.ч. другие группы) сколь угодно
глубоко. На странице группа выглядит как обычная плашка без списка;
клик разворачивает её в рамку с подписанным названием и вложенными
плашками внутри.

Ключ назывался "style_boxes" до этой версии -- переименован в "boxes"
(см. _migrate_legacy: переносит значение под новым именем, ничего не
теряя).

  always_loras: [                  # LoRA, подгружаемые ВСЕГДА, независимо от
    {                               # выбора в "Стилях"/"Категориях"
      id, label,
      lora_file, lora_strength,    # тоже допускает отрицательные значения
      trigger_words,                # всегда добавляется в позитивный промпт
    },
    ...
  ]

Это две ортогональные системы: "styles" -- одиночные плашки-переключатели
(клик = вкл/выкл), "boxes" -- категории/группы категорий. Обе
показываются на странице одновременно и не заменяют друг друга.
"always_loras" не показывается на странице вообще -- это чисто
дев-режимный список, применяется молча к каждой генерации сверху того,
что выбрано в "Стилях"/"Категориях" (в пределах общего лимита
generation.max_loras -- см. app.js: эффективный лимит для
пользовательского выбора = max_loras минус количество always_loras).
"""

import json
import os
import threading
from pathlib import Path

# ИЗМЕНЕНО при встраивании в ComfyUIStudio: раньше DATA_DIR лежала прямо
# рядом с исходниками (data/ на уровень выше backend/), что подходило,
# только пока инструмент жил отдельным репозиторием с прямым запуском
# из исходников. Внутри comfyui_studio/imagine/ это уже папка пакета —
# писать в неё пользовательские config.json/uploads/ означало бы либо
# захламлять исходники комплекта, либо падать в собранном PyInstaller-
# варианте (папка распаковки временная и/или доступна только на чтение).
# Пишем в общую папку комплекта, по образцу shared_theme.py/
# shared_language.py и launcher/core/constants.py (APP_DIR) —
# %APPDATA%\ComfyUIStudio\imagine\. IMAGINE_DATA_DIR остаётся отдушиной
# для тестов/отладки вне Windows (где APPDATA не задан).
DATA_DIR = Path(
    os.environ.get(
        "IMAGINE_DATA_DIR",
        os.path.join(
            os.environ.get("APPDATA", os.path.expanduser("~")),
            "ComfyUIStudio",
            "imagine",
        ),
    )
)
CONFIG_PATH = DATA_DIR / "config.json"
UPLOADS_DIR = DATA_DIR / "uploads"

_lock = threading.Lock()

DEFAULT_CONFIG = {
    "comfyui": {
        "host": "127.0.0.1",
        "port": 8188,
        "launch_bat_path": "",
        "lora_folder": "",
    },
    "generation": {
        "default_steps": 6,
        "default_cfg": 1,
        "default_sampler": "euler_ancestral",
        "default_batch_size": 1,
        # Список для тех случаев, когда ComfyUI недоступен при
        # открытии страницы (см. GET /api/aspect-ratios -- в обычной
        # работе список берётся оттуда, живьём из
        # /object_info/ResolutionSelector, а не отсюда). Эти строки --
        # то, что реально прислал узел на момент правки; если он
        # поменяется в очередной раз, значения тут снова могут разойтись
        # с ComfyUI, но пострадает только офлайн-фолбэк, не сама генерация.
        "aspect_ratios": [
            "1:1 (Square)",
            "2:3 (Portrait Photo)",
            "3:2 (Photo)",
            "3:4 (Portrait Standard)",
            "4:3 (Standard)",
            "9:16 (Portrait Widescreen)",
            "16:9 (Widescreen)",
            "21:9 (Ultrawide)",
        ],
        "megapixel_options": [0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 1.5, 2.0],
        "default_megapixels": 0.2,
        "max_loras": 5,
    },
    "styles": [
        {
            "id": "winnifier-enterprise",
            "label": "Winnifier ENTERPRISE",
            "image": "",
            "target": "positive",
            "prompt_text": "",
            "lora_file": "flux\\Challenge_Winnifier_ENTERPRISE_EDITION_epoch_5.safetensors",
            "lora_strength": 0.8,
            "trigger_words": "",
        },
        {
            "id": "photoreal",
            "label": "Photoreal",
            "image": "",
            "target": "positive",
            "prompt_text": "raw photo, ultra realistic, natural skin texture, cinematic lighting",
            "lora_file": "",
            "lora_strength": 1.0,
            "trigger_words": "",
        },
        {
            "id": "std-negative",
            "label": "Стандартный негатив",
            "image": "",
            "target": "negative",
            "prompt_text": "lowres, bad anatomy, bad hands, extra fingers, blurry, watermark, text",
            "lora_file": "",
            "lora_strength": 1.0,
            "trigger_words": "",
        },
    ],
    "boxes": [
        {
            "id": "lighting",
            "label": "Освещение",
            "type": "category",
            "options": [
                {
                    "id": "natural",
                    "label": "Естественное",
                    "positive_text": "natural window light, soft shadows",
                    "negative_text": "",
                    "lora_file": "",
                    "lora_strength": 1.0,
                },
                {
                    "id": "studio",
                    "label": "Студийное",
                    "positive_text": "studio lighting, softbox, rim light",
                    "negative_text": "",
                    "lora_file": "",
                    "lora_strength": 1.0,
                },
            ],
        },
    ],
    "always_loras": [],
}


def _legacy_loras_presets_to_styles(config: dict) -> list:
    """Миграция с самой старой схемы (раздельные "loras" + "presets",
    до появления единого плоского каталога "styles")."""
    styles = []
    for lora in config.get("loras", []):
        styles.append(
            {
                "id": lora.get("id", ""),
                "label": lora.get("label", ""),
                "image": "",
                "target": "positive",
                "prompt_text": "",
                "lora_file": lora.get("file", ""),
                "lora_strength": lora.get("default_strength", 1.0),
                "trigger_words": lora.get("trigger_words", ""),
            }
        )
    for preset in config.get("presets", []):
        styles.append(
            {
                "id": preset.get("id", ""),
                "label": preset.get("label", ""),
                "image": "",
                "target": preset.get("target", "positive"),
                "prompt_text": preset.get("text", ""),
                "lora_file": "",
                "lora_strength": 1.0,
                "trigger_words": "",
            }
        )
    return styles


def _ensure_box_types(nodes: list) -> list:
    """Старые записи "boxes"/"style_boxes" не знали про "type" (все были
    листовыми категориями) -- проставляет "type": "category" там, где
    его нет, рекурсивно (на случай, если кто-то уже сохранил дерево с
    группами до того, как этот проход появился)."""
    for node in nodes:
        node.setdefault("type", "category")
        if node["type"] == "group":
            node.setdefault("children", [])
            _ensure_box_types(node["children"])
    return nodes


def _migrate_legacy(config: dict) -> dict:
    """Не разрушает то, что уже реально настроено на диске -- если
    "styles" уже есть в текущей форме, не трогаем; если есть только
    "boxes"/"style_boxes" (более ранняя версия), оставляем как есть
    (там могут быть реальные настройки пользователя) и досеиваем
    свежий дефолтный плоский "styles" рядом, а не поверх."""
    if "loras" in config or "presets" in config:
        config["styles"] = _legacy_loras_presets_to_styles(config)
        config.pop("loras", None)
        config.pop("presets", None)
    elif "styles" not in config:
        config["styles"] = json.loads(json.dumps(DEFAULT_CONFIG["styles"]))

    if "boxes" not in config:
        # "style_boxes" -- имя ключа до переименования; переносим
        # значение под новым именем, если оно есть, вместо того чтобы
        # тихо подменить его дефолтом.
        if "style_boxes" in config:
            config["boxes"] = config.pop("style_boxes")
        else:
            config["boxes"] = json.loads(json.dumps(DEFAULT_CONFIG["boxes"]))
    config.pop("style_boxes", None)
    config["boxes"] = _ensure_box_types(config["boxes"])

    config.setdefault("always_loras", [])

    config.setdefault("comfyui", {}).setdefault("lora_folder", "")
    config.setdefault("generation", {}).setdefault(
        "megapixel_options", DEFAULT_CONFIG["generation"]["megapixel_options"]
    )
    config.setdefault("generation", {}).setdefault(
        "default_megapixels", DEFAULT_CONFIG["generation"]["default_megapixels"]
    )
    return config


def _ensure_file():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(
            json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def load_config() -> dict:
    with _lock:
        _ensure_file()
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return json.loads(json.dumps(DEFAULT_CONFIG))
        return _migrate_legacy(raw)


def save_config(config: dict) -> None:
    with _lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
