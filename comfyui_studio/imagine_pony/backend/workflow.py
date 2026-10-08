"""
Патчинг шаблона графа (assets/workflow_template.json -- экспорт API-графа
PonyXL с узлами расширения character_search_ui) параметрами формы.

Что куда подставляется (id узлов зафиксированы по шаблону, как и в
imagine/backend/workflow.py -- привязки «по имени класса» нет намеренно):

  120 CharacterSearchUI   selected_names / excluded_tags  (JSON-строки)
  121 PromptBuilderNode   state_json / negative_preset / extra_negative
  122 CheckpointLoader    ckpt_name
  123 MultiLoraLoader     lora_1..5 / strength_1..5  (ручные слоты; вход
                           lora_list из билдера остаётся привязан)
  88  SamplerCustomWithSeed noise_seed / cfg
  135 EmptyLatentImage    batch_size
  136 ResolutionSelector  aspect_ratio / megapixels
  137 AlignYourSteps      steps (число) -- либо ссылка на узел 141
  141 Random Number       minimum / maximum / seed  (случайные шаги)
  138 KSamplerSelect      sampler_name
  129 Text Multiline      text -- заменяется строкой, если в билдере
                           исключены теги негатива (см. build_graph)
"""

import copy
import json
import random
import sys
from pathlib import Path

if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    _ASSETS_DIR = Path(sys._MEIPASS) / "comfyui_studio" / "imagine_pony" / "assets"
else:
    _ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

TEMPLATE_PATH = _ASSETS_DIR / "workflow_template.json"

NODE_CHARACTERS = "120"
NODE_BUILDER = "121"
NODE_CHECKPOINT = "122"
NODE_LORAS = "123"
NODE_NEGATIVE_TEXT = "129"
NODE_SAMPLER = "88"
NODE_LATENT = "135"
NODE_RESOLUTION = "136"
NODE_SCHEDULER = "137"
NODE_SAMPLER_SELECT = "138"
NODE_STEPS_RANDOM = "141"

MAX_LORA_SLOTS = 5
MAX_SEED = 2**32 - 1


class WorkflowValidationError(Exception):
    pass


def load_template() -> dict:
    return json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))


def build_graph(template: dict, params: dict, negative_override: str = None):
    """Возвращает (новый граф, seed). params -- model_dump() запроса
    генерации (см. main.py). negative_override -- готовый текст
    негатива с учётом исключённых тегов (см. комментарий в main.generate)."""
    graph = copy.deepcopy(template)

    def inputs(node_id):
        node = graph.get(node_id)
        if node is None:
            raise WorkflowValidationError(
                f"В шаблоне графа нет узла {node_id} -- workflow_template.json "
                f"не совпадает с ожидаемой структурой"
            )
        return node["inputs"]

    # -- персонажи ----------------------------------------------------------
    chars = params.get("characters") or {}
    inputs(NODE_CHARACTERS)["selected_names"] = json.dumps(chars.get("selected", []), ensure_ascii=False)
    inputs(NODE_CHARACTERS)["excluded_tags"] = json.dumps(chars.get("excluded", []), ensure_ascii=False)

    # -- билдер ---------------------------------------------------------------
    builder = params.get("builder") or {}
    state = dict(builder.get("state") or {})
    state["negative_preset"] = builder.get("negative_preset", state.get("negative_preset", "Standard"))
    state["extra_negative"] = builder.get("extra_negative", state.get("extra_negative", ""))
    excluded_neg = list(builder.get("excluded_negative") or [])
    state["__excluded_negative__"] = json.dumps(excluded_neg, ensure_ascii=False)
    b = inputs(NODE_BUILDER)
    b["state_json"] = json.dumps(state, ensure_ascii=False)
    b["negative_preset"] = state["negative_preset"]
    b["extra_negative"] = state["extra_negative"]

    if negative_override is not None:
        inputs(NODE_NEGATIVE_TEXT)["text"] = negative_override

    # -- модель ---------------------------------------------------------------
    if params.get("checkpoint"):
        inputs(NODE_CHECKPOINT)["ckpt_name"] = params["checkpoint"]

    # -- LoRA (ручные слоты) -------------------------------------------------
    _apply_loras(inputs(NODE_LORAS), params.get("loras", []))

    # -- кадр ----------------------------------------------------------------
    inputs(NODE_RESOLUTION)["aspect_ratio"] = params["aspect_ratio"]
    if params.get("megapixels") is not None:
        inputs(NODE_RESOLUTION)["megapixels"] = params["megapixels"]
    inputs(NODE_LATENT)["batch_size"] = params["batch_size"]

    # -- шаги: фиксированные или случайный диапазон (как в шаблоне) -----------
    lo, hi = sorted((int(params["steps_min"]), int(params["steps_max"])))
    if lo == hi:
        inputs(NODE_SCHEDULER)["steps"] = lo
    else:
        r = inputs(NODE_STEPS_RANDOM)
        r["minimum"], r["maximum"] = lo, hi
        # Без смены seed ComfyUI достанет результат Random Number из кэша
        # и шаги не будут меняться от запуска к запуску.
        r["seed"] = random.randint(0, 2**50)

    # -- сэмплер ---------------------------------------------------------------
    seed = params.get("seed")
    if seed is None or params.get("randomize_seed"):
        seed = random.randint(0, MAX_SEED)
    inputs(NODE_SAMPLER)["noise_seed"] = seed
    if params.get("cfg") is not None:
        inputs(NODE_SAMPLER)["cfg"] = params["cfg"]
    if params.get("sampler_name"):
        inputs(NODE_SAMPLER_SELECT)["sampler_name"] = params["sampler_name"]

    return graph, seed


def _apply_loras(lora_inputs: dict, loras: list) -> None:
    """Ручные слоты MultiLoraLoader. Пустые -- явно «none»/1, чтобы
    значения из шаблона (там в lora_1 лежит слайдер со силой 0) не
    просочились в генерацию."""
    selected = [l for l in loras if l.get("file")][:MAX_LORA_SLOTS]
    for i in range(1, MAX_LORA_SLOTS + 1):
        if i <= len(selected):
            lora_inputs[f"lora_{i}"] = selected[i - 1]["file"]
            lora_inputs[f"strength_{i}"] = selected[i - 1].get("strength", 1.0)
        else:
            lora_inputs[f"lora_{i}"] = "none"
            lora_inputs[f"strength_{i}"] = 1
