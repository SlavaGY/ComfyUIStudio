"""
Хранилище Imagine Pony.

Два файла в %APPDATA%\\ComfyUIStudio\\imagine_pony\\:

  config.json -- настройки приложения (адрес ComfyUI, значения по умолчанию
                 для формы). Без дев-режима: правится руками или
                 приходит от Studio через аргументы запуска.
  state.json  -- состояние интерфейса между запусками: выбранные
                 персонажи/исключённые теги, состояние билдера (в том же
                 формате, что state_json узла PromptBuilderNode), слоты LoRA
                 и параметры кадра. Хранится на сервере, а не в
                 localStorage, чтобы десктоп и телефон (через Remote) видели
                 одно и то же.
"""

import json
import os
import threading
from pathlib import Path

from comfyui_studio import app_paths

DATA_ENV = "IMAGINE_PONY_DATA_DIR"

DATA_DIR = Path(os.environ.get(DATA_ENV) or os.path.join(app_paths.studio_data_dir(), "imagine_pony"))
CONFIG_PATH = DATA_DIR / "config.json"
STATE_PATH = DATA_DIR / "state.json"

_lock = threading.Lock()

DEFAULT_CONFIG = {
    "comfyui": {"host": "127.0.0.1", "port": 8188},
    "generation": {
        "megapixel_options": [0.5, 0.75, 1.0, 1.25, 1.5, 2.0],
        "default_megapixels": 1.0,
        "default_batch_size": 4,
        "default_steps_min": 20,
        "default_steps_max": 40,
        "default_cfg": 7,
        "default_sampler": "euler_ancestral",
        "default_aspect_ratio": "9:16 (Portrait Widescreen)",
        # Узлы шаблона: список на случай, когда ComfyUI недоступен и
        # живые COMBO получить не удалось (см. /api/aspect-ratios и т.п.).
        "fallback_aspect_ratios": [
            "1:1 (Square)",
            "2:3 (Portrait Photo)",
            "3:2 (Photo)",
            "3:4 (Portrait Standard)",
            "4:3 (Standard)",
            "9:16 (Portrait Widescreen)",
            "16:9 (Widescreen)",
            "21:9 (Ultrawide)",
        ],
        "fallback_samplers": ["euler_ancestral", "euler", "dpmpp_2m", "dpmpp_sde", "ddim"],
    },
}


def _deep_merge(base: dict, extra: dict) -> dict:
    out = json.loads(json.dumps(base))
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config() -> dict:
    """config.json поверх умолчаний; IMAGINE_PONY_COMFY_HOST/PORT (их
    выставляет __main__.py по CLI-аргументам Studio) -- поверх всего, но
    НЕ записываются в файл."""
    with _lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not CONFIG_PATH.exists():
            CONFIG_PATH.write_text(
                json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            raw = {}
    cfg = _deep_merge(DEFAULT_CONFIG, raw if isinstance(raw, dict) else {})
    host = os.environ.get("IMAGINE_PONY_COMFY_HOST")
    port = os.environ.get("IMAGINE_PONY_COMFY_PORT")
    if host:
        cfg["comfyui"]["host"] = host
    if port:
        try:
            cfg["comfyui"]["port"] = int(port)
        except ValueError:
            pass
    return cfg


def load_state() -> dict:
    with _lock:
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}


def save_state(state: dict) -> None:
    with _lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(STATE_PATH)
