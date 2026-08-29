"""
Патчинг шаблона графа (data/workflow_template.json, формат API-графа
ComfyUI, экспортированный из workflow "flux1_krea_dev1") под параметры,
пришедшие из формы генерации.

Соответствие полей формы узлам графа (см. сам workflow_template.json и
"""

import copy
import json
import random
import sys
from pathlib import Path

# ИЗМЕНЕНО при встраивании в ComfyUIStudio: workflow_template.json —
# бандловый, доступный только для чтения ассет (экспорт workflow'а, не
# пользовательские данные), поэтому лежит в imagine/assets/, а не рядом
# с пользовательским config.json/uploads в APPDATA (см. config_store.py)
# -- та же логика разделения, что и у THEMES_DIR в
# comfyui_studio/themes/theme_manager.py: sys._MEIPASS в собранном
# PyInstaller-варианте, папка исходников иначе.
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    _ASSETS_DIR = Path(sys._MEIPASS) / "comfyui_studio" / "imagine" / "assets"
else:
    _ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

TEMPLATE_PATH = _ASSETS_DIR / "workflow_template.json"

# ID узлов в шаблоне -- зафиксированы по конкретному workflow'у. Если
# пользователь заменит workflow_template.json на другой граф с другими
# id, эти константы придётся поправить вручную (никакой автоматической
# привязки "по имени класса" здесь нет -- намеренно, чтобы не гадать,
# какой из нескольких одинаковых по классу узлов имеется в виду).
NODE_POSITIVE_TEXT = "220"
NODE_NEGATIVE_TEXT = "224"
NODE_RESOLUTION = "221"
NODE_SCHEDULER = "214"
NODE_LATENT = "205"
NODE_SAMPLER = "209"
NODE_SAMPLER_SELECT = "210"
NODE_MULTI_LORA = "222"
NODE_SAVE = "213"

MAX_LORA_SLOTS = 5


def load_template() -> dict:
    return json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))


class WorkflowValidationError(Exception):
    pass


def build_graph(template: dict, params: dict) -> dict:
    """Возвращает НОВЫЙ граф (глубокая копия шаблона) с подставленными
    значениями. params -- уже провалидированный словарь из
    GenerationRequest (см. main.py)."""
    graph = copy.deepcopy(template)

    def node(node_id):
        n = graph.get(node_id)
        if n is None:
            raise WorkflowValidationError(
                f"В шаблоне графа отсутствует узел {node_id} -- "
                f"workflow_template.json не совпадает с ожидаемой структурой"
            )
        return n["inputs"]

    node(NODE_POSITIVE_TEXT)["text"] = params["positive_prompt"]
    node(NODE_NEGATIVE_TEXT)["text"] = params.get("negative_prompt", "")

    node(NODE_RESOLUTION)["aspect_ratio"] = params["aspect_ratio"]
    if params.get("megapixels") is not None:
        node(NODE_RESOLUTION)["megapixels"] = params["megapixels"]

    node(NODE_SCHEDULER)["steps"] = params["steps"]
    if params.get("scheduler"):
        node(NODE_SCHEDULER)["scheduler"] = params["scheduler"]

    node(NODE_LATENT)["batch_size"] = params["batch_size"]

    seed = params.get("seed")
    if seed is None or params.get("randomize_seed"):
        seed = random.randint(0, 2**32 - 1)
    node(NODE_SAMPLER)["noise_seed"] = seed
    if params.get("cfg") is not None:
        node(NODE_SAMPLER)["cfg"] = params["cfg"]

    if params.get("sampler_name"):
        node(NODE_SAMPLER_SELECT)["sampler_name"] = params["sampler_name"]

    _apply_loras(node(NODE_MULTI_LORA), params.get("loras", []))

    return graph, seed


def _apply_loras(lora_inputs: dict, loras: list) -> None:
    """loras -- список {"file": str, "strength": float}, максимум
    MAX_LORA_SLOTS штук (лишние отбрасываются, а не ошибка -- дев-режим
    и так ограничивает выбор через max_loras в конфиге). Незанятые
    слоты явно сбрасываются в "none"/1, чтобы предыдущее выполнение не
    "просочилось" через переиспользованный шаблон-словарь по ошибке
    вызывающего кода (сама build_graph всегда работает с глубокой
    копией, но эта защита дешёвая и не помешает).

    Разделитель пути принудительно приводится к '\\': весь этот проект
    целиком заточен под Windows (портативный ComfyUI, .bat-запуск), и
    некоторые lora_file могли осесть в конфиге с '/' -- их туда клал
    старый механизм сканирования папки (Path.as_posix() всегда отдаёт
    '/'), тогда как реальный COMBO ComfyUI на Windows ждёт '\\' и
    отклонял задание с value_not_in_list. Нормализация здесь чинит уже
    сохранённые в config.json старые записи без необходимости заново
    руками переоткрывать их в дев-режиме."""
    selected = loras[:MAX_LORA_SLOTS]
    for i in range(1, MAX_LORA_SLOTS + 1):
        if i <= len(selected):
            entry = selected[i - 1]
            lora_inputs[f"lora_{i}"] = entry["file"].replace("/", "\\")
            lora_inputs[f"strength_{i}"] = entry.get("strength", 1)
        else:
            lora_inputs[f"lora_{i}"] = "none"
            lora_inputs[f"strength_{i}"] = 1


def find_output_images(history_entry: dict) -> list:
    """[{"filename", "subfolder", "type"}] из outputs записи /history --
    ищет по ВСЕМ узлам, а не только NODE_SAVE, т.к. SaveWithPrompt --
    кастомный узел и не гарантированно называет свой выход "images" в
    точности как встроенный SaveImage; на практике их совместимость с
    менеджером изображений ComfyUI как раз и держится на этом
    соглашении по имени поля, так что берём его отовсюду, где
    встретится, а не жёстко привязываемся к NODE_SAVE."""
    images = []
    outputs = history_entry.get("outputs", {})
    for node_output in outputs.values():
        for img in node_output.get("images", []):
            images.append(
                {
                    "filename": img.get("filename"),
                    "subfolder": img.get("subfolder", ""),
                    "type": img.get("type", "output"),
                }
            )
    return images
