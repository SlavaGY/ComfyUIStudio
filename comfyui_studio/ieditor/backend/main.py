"""IEditor API: uploads, one/two-reference editing, and persistent results."""

import logging
import time
import uuid
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator

from comfyui_studio.imagine.backend.comfy_client import ComfyClient, ComfyUIError
from comfyui_studio.imagine.backend.workflow import find_output_images
from comfyui_studio.web_common import (
    CachedStaticFiles,
    CachePolicyMiddleware,
    mount_common_static,
    register_ui_prefs,
    static_dir,
)
from comfyui_studio.web_common.image_stream import stream_image

from . import config_store, workflow

log = logging.getLogger(__name__)
app = FastAPI(title="IEditor — ComfyUI Studio")
app.add_middleware(CachePolicyMiddleware)
register_ui_prefs(app)
MAX_UPLOAD_BYTES = 32 * 1024 * 1024


def get_client(timeout=10.0):
    return ComfyClient(**config_store.connection(), timeout=timeout)


class FormState(BaseModel):
    prompt: str = Field(default="", max_length=20000)
    images: list[str] = Field(default_factory=list, max_length=2)
    steps: int = Field(default=4, ge=1, le=150)
    cfg: float = Field(default=1.0, ge=0, le=30, allow_inf_nan=False)
    sampler_name: str = Field(default="euler_ancestral", max_length=100)
    megapixels: float = Field(default=1.0, ge=0.25, le=2.0, allow_inf_nan=False)
    seed: int = Field(default=0, ge=0, le=workflow.MAX_SEED)
    randomize_seed: bool = True

    @field_validator("images")
    @classmethod
    def valid_images(cls, images):
        if images:
            workflow.validate_images(images)
        return images


@app.get("/api/config")
def config():
    defaults = workflow.defaults()
    client = get_client()
    missing = []
    samplers = [defaults["sampler_name"], "euler", "heun"]
    try:
        for node, field, key in (
            ("UNETLoader", "unet_name", "model"),
            ("CLIPLoader", "clip_name", "text_encoder"),
            ("VAELoader", "vae_name", "vae"),
        ):
            if defaults[key] not in client.get_combo_options(node, field):
                missing.append(defaults[key])
        samplers = client.get_combo_options("KSamplerSelect", "sampler_name") or samplers
    except ComfyUIError:
        return {"defaults": defaults, "samplers": samplers, "alive": False, "missing": []}
    return {"defaults": defaults, "samplers": samplers, "alive": True, "missing": missing}


@app.get("/api/state")
def state():
    return config_store.load_state()


@app.put("/api/state")
def save_state(body: FormState):
    config_store.save_state(body.model_dump())
    return {"ok": True}


@app.get("/api/comfyui/status")
def comfy_status():
    return {"alive": get_client().is_alive()}


@app.post("/api/comfyui/free-memory")
def free_memory():
    try:
        get_client().free_memory()
    except ComfyUIError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"ok": True}


def image_format(header):
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp", "image/webp"
    if header[:6] in (b"GIF87a", b"GIF89a"):
        return "gif", "image/gif"
    raise HTTPException(400, "Поддерживаются изображения PNG, JPEG, WebP и GIF.")


@app.post("/api/upload-image")
def upload_image(file: UploadFile):
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    if not 0 < size <= MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Размер изображения должен быть от 1 байта до 32 МБ.")
    extension, mime = image_format(file.file.read(16))
    file.file.seek(0)
    name = f"ieditor_{uuid.uuid4().hex}.{extension}"
    client = get_client(60.0)
    try:
        with httpx.Client(timeout=60.0, trust_env=False) as http:
            response = http.post(
                f"{client.base_url}/upload/image",
                files={"image": (name, file.file, mime)},
                data={"type": "input", "subfolder": "ieditor", "overwrite": "false"},
            )
            response.raise_for_status()
            uploaded = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, f"Не удалось загрузить изображение в ComfyUI: {exc}") from exc
    ref = f"{uploaded.get('subfolder', '')}/{uploaded.get('name', '')}"
    if not workflow.UPLOAD_REF.fullmatch(ref):
        raise HTTPException(502, "ComfyUI вернул неожиданный путь загруженного изображения.")
    return {"image": ref, "url": image_url(uploaded["name"], "ieditor", "input")}


def image_url(filename, subfolder="", image_type="output"):
    return "api/image?" + urlencode({"filename": filename, "subfolder": subfolder, "type": image_type})


@app.post("/api/generate")
def generate(body: FormState):
    try:
        graph, seed = workflow.build_graph(workflow.load_template(), body.model_dump())
    except workflow.WorkflowValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        prompt_id = get_client(30.0).queue_prompt(graph)
    except ComfyUIError as exc:
        raise HTTPException(502, f"ComfyUI отклонил задание: {exc}") from exc
    job = {
        "prompt_id": prompt_id,
        "seed": seed,
        "created_at": time.time(),
        "image_count": len(body.images),
        "prompt": body.prompt.strip(),
    }
    try:
        config_store.remember_job(job)
    except OSError:
        log.exception("Queued IEditor job %s could not be saved to disk", prompt_id)
    return job


@app.get("/api/jobs")
def jobs():
    return {"jobs": config_store.load_jobs()}


def execution_error(entry):
    for kind, detail in reversed(entry.get("status", {}).get("messages", [])):
        if kind == "execution_error":
            return str(detail.get("exception_message") or detail.get("exception_type") or kind).strip()
        if kind == "execution_interrupted":
            return "Генерация остановлена."
    return "ComfyUI сообщил об ошибке выполнения."


@app.get("/api/generate/{prompt_id}/status")
def generation_status(prompt_id: uuid.UUID):
    target = str(prompt_id)
    saved = next((job for job in config_store.load_jobs() if job.get("prompt_id") == target), {})
    if not saved:
        return {"state": "unknown"}
    if saved.get("result"):
        return saved["result"]
    client = get_client()
    try:
        entry = client.get_history(str(prompt_id))
        if entry is not None:
            status = entry.get("status", {})
            if status.get("status_str") == "error" or status.get("completed") is False:
                interrupted = any(kind == "execution_interrupted" for kind, _detail in status.get("messages", []))
                result = {"state": "cancelled" if interrupted else "error", "error": execution_error(entry), "images": []}
            else:
                images = find_output_images(entry)
                result = {
                    "state": "done" if images else "error",
                    "images": [{**img, "url": image_url(img["filename"], img["subfolder"], img["type"])} for img in images],
                }
                if not images:
                    result["error"] = "ComfyUI завершил задание без изображения."
            config_store.save_result(target, result)
            return result
        running, position = client.get_queue_position(str(prompt_id))
    except ComfyUIError as exc:
        raise HTTPException(502, str(exc)) from exc
    if running:
        return {"state": "running"}
    if position is not None:
        return {"state": "pending", "queue_position": position}
    return {"state": "unknown"}


@app.post("/api/generate/{prompt_id}/interrupt")
def interrupt(prompt_id: uuid.UUID):
    # Do not stop a generation submitted by another Studio app or browser.
    target = str(prompt_id)
    if not any(job.get("prompt_id") == target for job in config_store.load_jobs()):
        raise HTTPException(404, "Задание IEditor не найдено.")
    client = get_client()
    try:
        running, position = client.get_queue_position(target)
        if running:
            client._post("/interrupt", {"prompt_id": target})
            return {"ok": True, "state": "interrupting"}
        elif position is not None:
            client._post("/queue", {"delete": [target]})
            config_store.save_result(target, {"state": "cancelled", "images": []})
            return {"ok": True, "state": "cancelled"}
    except ComfyUIError as exc:
        raise HTTPException(502, str(exc)) from exc
    # It may have finished between the click and the queue check.
    return {"ok": True, **generation_status(prompt_id)}


@app.get("/api/image")
async def image(filename: str, subfolder: str = "", type: Literal["input", "output", "temp"] = "output"):
    for part in (filename, subfolder):
        path = PurePosixPath(part.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or ":" in part:
            raise HTTPException(400, "Недопустимый путь изображения.")
    client = get_client(30.0)
    return await stream_image(client.view_image_url(filename, subfolder, type), client.timeout)


mount_common_static(app)
# Reuse Studio's existing components and six theme palettes verbatim.
app.mount("/studio-style", CachedStaticFiles(directory=static_dir("imagine_pony")), name="studio-style")
app.mount("/", CachedStaticFiles(directory=static_dir("ieditor"), html=True), name="static")
