"""Adapt the tested Klein 9B API graph without touching its model settings.

The bundled graph is taken from the successful open workflow on 2026-11-13.
For two references, chain ReferenceLatent on both positive and zeroed
conditioning, as in the second subgraph of that same workflow.
"""

import copy
import json
import re
import secrets
import sys
from pathlib import Path

_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3]))
TEMPLATE_PATH = _ROOT / "comfyui_studio" / "ieditor" / "assets" / "workflow_template.json"
MAX_SEED = 2**53 - 1
UPLOAD_REF = re.compile(r"ieditor/ieditor_[0-9a-f]{32}\.(?:png|jpg|webp|gif)\Z")


class WorkflowValidationError(ValueError):
    pass


def load_template():
    return json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))


def defaults():
    graph = load_template()
    return {
        "model": graph["75:70"]["inputs"]["unet_name"],
        "text_encoder": graph["75:71"]["inputs"]["clip_name"],
        "vae": graph["75:72"]["inputs"]["vae_name"],
        "steps": graph["75:62"]["inputs"]["steps"],
        "cfg": graph["75:63"]["inputs"]["cfg"],
        "sampler_name": graph["75:61"]["inputs"]["sampler_name"],
        "megapixels": graph["75:80"]["inputs"]["megapixels"],
        "seed": 0,
        "randomize_seed": True,
    }


def validate_images(images):
    if not 1 <= len(images) <= 2:
        raise WorkflowValidationError("Прикрепите одно или два изображения.")
    if any(not isinstance(image, str) or not UPLOAD_REF.fullmatch(image) for image in images):
        raise WorkflowValidationError("Загрузите изображения через IEditor повторно.")


def build_graph(template, params):
    images = params.get("images", [])
    validate_images(images)
    prompt = str(params.get("prompt", "")).strip()
    if not prompt:
        raise WorkflowValidationError("Опишите, что нужно изменить.")
    graph = copy.deepcopy(template)
    # The original export has a disconnected second LoadImage. Removing it
    # avoids validating a missing example image in single-reference mode.
    graph.pop("81", None)
    graph["76"]["inputs"]["image"] = images[0]
    graph["75:74"]["inputs"]["text"] = prompt
    graph["9"]["inputs"]["filename_prefix"] = "IEditor/IEditor"
    seed = params.get("seed", 0)
    if seed is None or params.get("randomize_seed", True):
        seed = secrets.randbelow(MAX_SEED + 1)
    graph["75:73"]["inputs"]["noise_seed"] = seed
    for node, field, key in (
        ("75:62", "steps", "steps"),
        ("75:63", "cfg", "cfg"),
        ("75:61", "sampler_name", "sampler_name"),
        ("75:80", "megapixels", "megapixels"),
    ):
        if params.get(key) is not None:
            graph[node]["inputs"][field] = params[key]

    if len(images) == 2:
        graph["81"] = {"class_type": "LoadImage", "inputs": {"image": images[1]}}
        graph["ieditor:scale2"] = copy.deepcopy(graph["75:80"])
        graph["ieditor:scale2"]["inputs"]["image"] = ["81", 0]
        graph["ieditor:encode2"] = {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["ieditor:scale2", 0], "vae": ["75:72", 0]},
        }
        for name, first in (("positive", "75:123"), ("negative", "75:121")):
            node_id = f"ieditor:{name}2"
            graph[node_id] = {
                "class_type": "ReferenceLatent",
                "inputs": {"conditioning": [first, 0], "latent": ["ieditor:encode2", 0]},
            }
            graph["75:63"]["inputs"][name] = [node_id, 0]
    return graph, seed
