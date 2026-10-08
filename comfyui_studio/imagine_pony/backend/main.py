"""
Бэкенд Imagine Pony (FastAPI). Устроен как imagine/backend/main.py, но:
  * без дев-режима, стилей/категорий Imagine и генератора промптов;
  * данные персонажей и конфиг билдера не хранит -- берёт у ComfyUI через
    маршруты расширения character_search_ui (/character_search/*,
    /prompt_builder/*) и пробрасывает фронтенду как есть: вся логика
    (сборка тегов, рандом, валидация) остаётся одна, в расширении;
  * состояние интерфейса хранится на сервере (GET/PUT /api/state).

Запуск:  python -m comfyui_studio.imagine_pony [--port 7861 --comfy-port 8188]
"""

import logging
import sys
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from comfyui_studio.imagine.backend.comfy_client import ComfyClient, ComfyUIError
from comfyui_studio.imagine.backend.workflow import find_output_images

from . import config_store, workflow

try:
    from comfyui_studio import shared_language, shared_theme
except ImportError:  # pragma: no cover - запущено отдельно от Studio
    shared_theme = None
    shared_language = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("imagine_pony")

if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    _STATIC_DIR = Path(sys._MEIPASS) / "comfyui_studio" / "imagine_pony" / "static"
else:
    _STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="Imagine Pony — генератор PonyXL на ComfyUI")

# Узлы, без которых приложение не работает (см. /api/nodes).
REQUIRED_NODES = ("CharacterSearchUI", "PromptBuilderNode", "MultiLoraLoader")


class NoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        return response


app.add_middleware(NoCacheMiddleware)


def get_client(timeout: float = 10.0) -> ComfyClient:
    c = config_store.load_config()["comfyui"]
    return ComfyClient(host=c["host"], port=c["port"], timeout=timeout)


# Расширение читает characters.json с диска на КАЖДЫЙ запрос (и печатает
# валидацию в консоль ComfyUI) -- базу целиком кэшируем на короткое время.
_db_cache = {"at": 0.0, "data": None}
_DB_TTL = 30.0


def _extension_error(exc: Exception) -> HTTPException:
    text = str(exc)
    if "HTTP 404" in text:
        return HTTPException(
            502,
            "ComfyUI не знает маршрут расширения character_search_ui — "
            "проверьте, что оно установлено в custom_nodes и ComfyUI перезапущен",
        )
    return HTTPException(502, f"ComfyUI недоступен или вернул ошибку: {text}")


def _proxy_get(path: str):
    try:
        return get_client(30.0)._get(path)
    except ComfyUIError as exc:
        raise _extension_error(exc) from exc


def _proxy_post(path: str, payload: dict):
    try:
        return get_client(30.0)._post(path, payload)
    except ComfyUIError as exc:
        raise _extension_error(exc) from exc


def _combo(client: ComfyClient, node_class: str, input_name: str) -> list:
    """Значения COMBO-входа узла (required ИЛИ optional -- у
    MultiLoraLoader слоты lora_N лежат в optional, а
    ComfyClient.get_combo_options смотрит только required)."""
    try:
        info = client._node_info(node_class)
    except ComfyUIError:
        return []
    if not isinstance(info, dict):
        return []
    for section in ("required", "optional"):
        spec = (info.get("input", {}).get(section) or {}).get(input_name)
        if not spec:
            continue
        first = spec[0]
        if isinstance(first, list):
            options = first
        elif isinstance(first, str) and len(spec) > 1 and isinstance(spec[1], dict):
            options = spec[1].get("options", [])
        else:
            options = []
        return [o for o in options if isinstance(o, str)]
    return []


# ---------------------------------------------------------------------------
# Тема / язык / конфиг / состояние
# ---------------------------------------------------------------------------

@app.get("/api/ui-prefs")
def get_ui_prefs():
    theme = shared_theme.read_shared_theme() if shared_theme else None
    language = shared_language.read_shared_language() if shared_language else None
    return {"theme": theme, "language": language}


@app.put("/api/ui-prefs")
def put_ui_prefs(prefs: dict):
    if prefs.get("theme") and shared_theme:
        shared_theme.write_shared_theme(prefs["theme"])
    if prefs.get("language") and shared_language:
        shared_language.write_shared_language(prefs["language"])
    return {"ok": True}


@app.get("/api/config")
def get_config():
    cfg = config_store.load_config()
    return {"generation": cfg["generation"]}


@app.get("/api/state")
def get_state():
    return config_store.load_state()


@app.put("/api/state")
def put_state(state: dict):
    config_store.save_state(state)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Статус ComfyUI и живые списки значений
# ---------------------------------------------------------------------------

@app.get("/api/comfyui/status")
def comfyui_status():
    return {"alive": get_client().is_alive()}


@app.post("/api/comfyui/free-memory")
def comfyui_free_memory():
    try:
        get_client().free_memory()
    except ComfyUIError as exc:
        raise HTTPException(502, f"ComfyUI недоступен: {exc}") from exc
    return {"ok": True}


@app.get("/api/nodes")
def nodes_status():
    """Какие из нужных узлов реально есть в ComfyUI."""
    client = get_client()
    result = {}
    for name in REQUIRED_NODES:
        try:
            info = client.get_object_info(name)
            result[name] = isinstance(info, dict) and name in info
        except ComfyUIError:
            result[name] = False
    return result


@app.get("/api/loras")
def available_loras():
    files = [f for f in _combo(get_client(), "MultiLoraLoader", "lora_1") if f != "none"]
    return {"files": files}


@app.get("/api/checkpoints")
def available_checkpoints():
    return {"options": _combo(get_client(), "CheckpointLoaderWithName", "ckpt_name")}


@app.get("/api/samplers")
def available_samplers():
    options = _combo(get_client(), "KSamplerSelect", "sampler_name")
    if options:
        return {"options": options}
    return {"options": config_store.load_config()["generation"]["fallback_samplers"]}


@app.get("/api/aspect-ratios")
def aspect_ratios():
    options = _combo(get_client(), "ResolutionSelector", "aspect_ratio")
    if options:
        return {"options": options}
    return {"options": config_store.load_config()["generation"]["fallback_aspect_ratios"]}


# ---------------------------------------------------------------------------
# Персонажи (CharacterSearchUI) -- проброс к маршрутам расширения
# ---------------------------------------------------------------------------

@app.get("/api/characters/db")
def characters_db(refresh: bool = False):
    now = time.time()
    if not refresh and _db_cache["data"] is not None and now - _db_cache["at"] < _DB_TTL:
        return _db_cache["data"]
    data = _proxy_get("/character_search/db")
    _db_cache.update(at=now, data=data)
    return data


@app.post("/api/characters/preview")
def characters_preview(body: dict):
    return _proxy_post("/character_search/preview", body)


@app.post("/api/characters/random")
def characters_random(body: dict):
    return _proxy_post("/character_search/random", body)


# ---------------------------------------------------------------------------
# Билдер (PromptBuilderNode)
# ---------------------------------------------------------------------------

@app.get("/api/builder/config")
def builder_config():
    return _proxy_get("/prompt_builder/config")


@app.get("/api/builder/random-cats")
def builder_random_cats():
    return _proxy_get("/prompt_builder/random_cats")


@app.post("/api/builder/preview")
def builder_preview(body: dict):
    return _proxy_post("/prompt_builder/preview", body)


@app.post("/api/builder/randomize")
def builder_randomize(body: dict):
    return _proxy_post("/prompt_builder/randomize", body)


# ---------------------------------------------------------------------------
# Генерация
# ---------------------------------------------------------------------------

class CharactersSelection(BaseModel):
    selected: list[str] = []
    excluded: list[str] = []


class BuilderSelection(BaseModel):
    state: dict = {}
    negative_preset: str = "Standard"
    extra_negative: str = ""
    excluded_negative: list[str] = []


class LoraSelection(BaseModel):
    file: str
    strength: float = 1.0  # отрицательные допустимы


class GenerationRequest(BaseModel):
    characters: CharactersSelection = CharactersSelection()
    builder: BuilderSelection = BuilderSelection()
    loras: list[LoraSelection] = []
    checkpoint: Optional[str] = None
    aspect_ratio: str
    megapixels: Optional[float] = None
    batch_size: int = Field(gt=0, le=16)
    steps_min: int = Field(gt=0, le=150)
    steps_max: int = Field(gt=0, le=150)
    cfg: Optional[float] = None
    sampler_name: Optional[str] = None
    seed: Optional[int] = None
    randomize_seed: bool = True


@app.post("/api/generate")
def generate(req: GenerationRequest):
    client = get_client()
    if not client.is_alive():
        raise HTTPException(503, "ComfyUI недоступен по указанному адресу")

    params = req.model_dump()

    # Узел PromptBuilderNode при выполнении НЕ применяет исключённые теги
    # негатива (build_negative вызывается без `excluded`, тогда как
    # маршрут /prompt_builder/preview их учитывает) -- без этой подмены
    # в генерацию ушёл бы негатив, отличающийся от того, что пользователь
    # видит в предпросмотре. Поэтому, когда что-то исключено, берём
    # готовую строку у самого preview и подставляем её в Text Multiline
    # негатива (узел 129) вместо ссылки на выход билдера.
    negative_override = None
    if req.builder.excluded_negative:
        state = dict(req.builder.state)
        state["negative_preset"] = req.builder.negative_preset
        state["extra_negative"] = req.builder.extra_negative
        try:
            preview = client._post("/prompt_builder/preview", {
                "state": state,
                "char_tags": "",
                "excluded_negative": req.builder.excluded_negative,
            })
        except ComfyUIError as exc:
            raise _extension_error(exc) from exc
        negative_override = preview.get("negative_text", "")

    try:
        graph, seed = workflow.build_graph(workflow.load_template(), params, negative_override)
    except workflow.WorkflowValidationError as exc:
        raise HTTPException(500, str(exc)) from exc

    try:
        prompt_id = client.queue_prompt(graph)
    except ComfyUIError as exc:
        raise HTTPException(502, f"ComfyUI отклонил задание: {exc}") from exc
    return {"prompt_id": prompt_id, "seed": seed}


def _image_urls(images: list) -> list:
    # ОТНОСИТЕЛЬНЫЕ пути (без ведущего "/") -- страница может открываться
    # и напрямую, и через reverse-proxy Remote (/apps/<name>/), см. то же
    # объяснение в imagine/backend/main.py.
    return [
        {"url": f"api/image?filename={i['filename']}&subfolder={i['subfolder']}&type={i['type']}"}
        for i in images
    ]


@app.get("/api/generate/recent")
def recent_generations(limit: int = 20):
    try:
        entries = get_client().get_recent_history(limit)
    except ComfyUIError as exc:
        raise HTTPException(502, f"ComfyUI недоступен: {exc}") from exc
    items = []
    for prompt_id, entry in entries:
        images = find_output_images(entry)
        if images:
            items.append({"prompt_id": prompt_id, "images": _image_urls(images)})
    return {"items": items}


@app.get("/api/generate/{prompt_id}/status")
def generation_status(prompt_id: str):
    client = get_client()
    try:
        entry = client.get_history(prompt_id)
    except ComfyUIError as exc:
        raise HTTPException(502, f"ComfyUI недоступен: {exc}") from exc

    if entry is not None:
        images = find_output_images(entry)
        status = entry.get("status", {})
        return {
            "state": "done" if images else ("error" if status.get("completed") is False else "done"),
            "images": _image_urls(images),
        }

    try:
        running, position = client.get_queue_position(prompt_id)
    except ComfyUIError as exc:
        raise HTTPException(502, f"ComfyUI недоступен: {exc}") from exc
    if running:
        return {"state": "running", "queue_position": 0}
    if position is not None:
        return {"state": "pending", "queue_position": position}
    return {"state": "unknown"}


@app.post("/api/generate/{prompt_id}/interrupt")
def interrupt(prompt_id: str):
    # prompt_id не используется: ComfyUI /interrupt останавливает то, что
    # выполняется сейчас (так же устроено в Imagine).
    get_client().interrupt()
    return {"ok": True}


@app.get("/api/image")
def get_image(filename: str, subfolder: str = "", type: str = "output"):
    try:
        data, content_type = get_client().fetch_image_bytes(filename, subfolder, type)
    except ComfyUIError as exc:
        raise HTTPException(502, str(exc)) from exc
    return Response(content=data, media_type=content_type)


# ---------------------------------------------------------------------------
# Статика
# ---------------------------------------------------------------------------

config_store.DATA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
