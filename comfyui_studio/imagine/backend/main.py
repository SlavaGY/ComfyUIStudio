"""
Бэкенд инструмента генерации изображений (рабочее имя: Imagine).

Запуск:  run.bat          -- обычный режим (без дев-меню)
         run.bat dev       -- дев-режим (доступна настройка каталога стилей,
                               папки LoRA, подключения)

Дев-режим определяется ТОЛЬКО аргументом запуска (переменная окружения
IMAGINE_DEV_MODE, которую выставляет run.bat) -- в интерфейсе нет
переключателя "включить дев-режим на лету", это осознанно: см. запрос
пользователя "в обычном режиме не показывать эту менюшку". Бэкенд
дополнительно блокирует пишущие дев-эндпоинты (PUT /api/config, загрузка
картинок), даже если кто-то дёрнет их напрямую при обычном запуске --
не ради серьёзной защиты (инструмент локальный, однопользовательский),
а чтобы поведение фронтенда и бэкенда не расходилось.
"""

import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from . import config_store, launcher, lora_scan, workflow
from .comfy_client import ComfyClient, ComfyUIError

try:
    # Необязательно: доступно только внутри ComfyUIStudio (см.
    # shared_theme.py/shared_language.py) -- если Imagine запущен как
    # самостоятельный инструмент (venv без остального комплекта), эти
    # эндпоинты просто отдают {"theme": null, "language": null}, а
    # переключатели темы/языка в интерфейсе остаются чисто локальными
    # (localStorage), см. GET/PUT /api/ui-prefs ниже.
    from comfyui_studio import shared_language, shared_theme
except ImportError:  # pragma: no cover - инструмент запущен отдельно от Studio
    shared_theme = None
    shared_language = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("imagine")

DEV_MODE = os.environ.get("IMAGINE_DEV_MODE") == "1"

# ИЗМЕНЕНО при встраивании в ComfyUIStudio: раньше статика раздавалась
# по относительному пути "static" (см. app.mount ниже), что работало
# только потому, что run.bat всегда делал cd в папку самого инструмента
# перед запуском uvicorn. Запущенный из ComfyUIStudio (`python -m
# comfyui_studio.imagine`, cwd = корень проекта Studio, как и остальные
# инструменты комплекта -- см. core/comfy_process.py) с тем же
# относительным путём Imagine отдавал бы 404 на всё. Резолвим от
# расположения самого пакета, с тем же приёмом на sys._MEIPASS для
# собранного варианта, что и у TEMPLATE_PATH в workflow.py.
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    _STATIC_DIR = Path(sys._MEIPASS) / "comfyui_studio" / "imagine" / "static"
else:
    _STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="Imagine — локальный генератор на ComfyUI")


class NoCacheMiddleware(BaseHTTPMiddleware):
    """Инструмент локальный, файлы меняются между запусками dev-сессии
    чаще, чем у любого обычного сайта -- кэш браузера здесь только
    вредит (см. как рассинхронизировались index.html и app.js после
    обновления: браузер получил 304 и продолжил использовать старый
    app.js). Отключаем кэш статики целиком, а не разбираемся с
    ETag/Last-Modified вручную."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        return response


app.add_middleware(NoCacheMiddleware)


# ИЗМЕНЕНО при встраивании в ComfyUIStudio: когда Imagine запущен как
# вариант запуска ComfyUI из самой Studio (см. launcher/core/
# imagine_process.py), Studio уже точно знает, на каком host/port она
# подняла ComfyUI (cfg["root_path"]/cfg["port"] в ui/settings/
# comfyui_page.py) -- это надёжнее, чем то, что мог сохранить в своём
# config.json сам Imagine с прошлого раза (порт ComfyUI в Studio можно
# сменить в любой момент). Если заданы IMAGINE_COMFY_HOST/
# IMAGINE_COMFY_PORT (их выставляет __main__.py по CLI-аргументам от
# imagine_process.py), перезаписываем comfyui.host/port в конфиге сразу
# при старте -- один раз, до первого запроса, а не на каждый /api/config.
def _seed_comfy_target_from_env():
    host = os.environ.get("IMAGINE_COMFY_HOST")
    port = os.environ.get("IMAGINE_COMFY_PORT")
    if not host and not port:
        return
    cfg = config_store.load_config()
    if host:
        cfg["comfyui"]["host"] = host
    if port:
        try:
            cfg["comfyui"]["port"] = int(port)
        except ValueError:
            log.warning("IMAGINE_COMFY_PORT=%r не число, игнорирую", port)
    config_store.save_config(cfg)
    log.info(
        "ComfyUI host/port переданы Studio: %s:%s",
        cfg["comfyui"]["host"], cfg["comfyui"]["port"],
    )


_seed_comfy_target_from_env()


def get_client() -> ComfyClient:
    cfg = config_store.load_config()
    comfy_cfg = cfg["comfyui"]
    return ComfyClient(host=comfy_cfg["host"], port=comfy_cfg["port"])


def require_dev_mode():
    if not DEV_MODE:
        raise HTTPException(403, "Дев-режим выключен (запустите run.bat dev)")


# ---------------------------------------------------------------------------
# Режим
# ---------------------------------------------------------------------------

@app.get("/api/mode")
def get_mode():
    return {"dev": DEV_MODE}


# ---------------------------------------------------------------------------
# Тема / язык — общие с остальным комплектом ComfyUIStudio
# ---------------------------------------------------------------------------
#
# По образцу shared_theme.py/shared_language.py, но без Qt-части (веб-
# странице не нужен QFileSystemWatcher -- см. initThemeAndLanguage() в
# static/js/i18n.js, который опрашивает этот эндпоинт коротким поллингом
# вместо файлового watcher'а, недоступного из браузера). Тема/язык,
# выбранные здесь, попадают в тот же %APPDATA%\ComfyUIStudio\*.json,
# так что подхватываются лаунчером, Prompt Config Editor и PromptVault
# так же, как если бы их поменяли в любом из них.

@app.get("/api/ui-prefs")
def get_ui_prefs():
    theme = shared_theme.read_shared_theme() if shared_theme else None
    language = shared_language.read_shared_language() if shared_language else None
    return {"theme": theme, "language": language}


@app.put("/api/ui-prefs")
def put_ui_prefs(prefs: dict):
    theme = prefs.get("theme")
    language = prefs.get("language")
    if theme and shared_theme:
        shared_theme.write_shared_theme(theme)
    if language and shared_language:
        shared_language.write_shared_language(language)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------

@app.get("/api/config")
def get_config():
    return config_store.load_config()


@app.put("/api/config")
def put_config(config: dict):
    require_dev_mode()
    required = ("comfyui", "generation", "styles", "boxes", "always_loras")
    if any(k not in config for k in required):
        raise HTTPException(400, "Конфиг должен содержать comfyui, generation, styles, boxes и always_loras")
    config_store.save_config(config)
    return {"ok": True}


@app.get("/api/available-loras")
def available_loras():
    """Источник истины -- живой COMBO ComfyUI (`lora_1` узла
    MultiLoraLoader): строки оттуда гарантированно пройдут валидацию
    (правильный регистр, правильный разделитель пути -- на Windows
    ComfyUI ждёт '\\', а наше сканирование папки всегда отдаёт '/' через
    Path.as_posix(), что и вызывало value_not_in_list при генерации).
    Скан папки на диске (тот же механизм, что в
    ComfyUIStudio/prompt_builder/lora_combo.py) остаётся только
    запасным путём на случай, если ComfyUI недоступен или узел не
    поддерживает точечный /object_info/<class>."""
    try:
        combo_files = get_client().get_combo_options("MultiLoraLoader", "lora_1")
    except ComfyUIError:
        combo_files = []
    if combo_files:
        return {"folder": "", "files": combo_files, "source": "comfyui"}

    cfg = config_store.load_config()
    folder = cfg["comfyui"].get("lora_folder") or lora_scan.guess_lora_folder(
        cfg["comfyui"].get("launch_bat_path", "")
    )
    files = lora_scan.scan_lora_files(folder)
    return {"folder": folder, "files": files, "source": "folder_scan"}


@app.post("/api/upload-image")
async def upload_image(file: UploadFile):
    require_dev_mode()
    ext = Path(file.filename or "").suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        raise HTTPException(400, "Поддерживаются только png/jpg/jpeg/webp/gif")
    name = f"{uuid.uuid4().hex}{ext}"
    dest = config_store.UPLOADS_DIR / name
    config_store.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    data = await file.read()
    dest.write_bytes(data)
    return {"url": f"/media/{name}"}


@app.get("/api/aspect-ratios")
def aspect_ratios():
    """Живой список значений aspect_ratio, которые реально примет узел
    ResolutionSelector -- см. баг: захардкоженный в конфиге список не
    совпадал с тем, что узел на самом деле принимает (COMBO поменялся
    вместе с версией узла), и ComfyUI отклонял задание с
    value_not_in_list. Если ComfyUI недоступен -- отдаём АКТУАЛЬНЫЙ
    дефолт из кода (DEFAULT_CONFIG), а не то, что сохранено в
    config.json на диске: этот список не редактируется через дев-режим,
    это чисто технический фолбэк, и старый config.json, оставшийся с
    прошлой версии приложения, не должен "протухать" его навсегда --
    ровно так и произошло, когда фолбэк брался из уже сохранённого файла."""
    try:
        options = get_client().get_combo_options("ResolutionSelector", "aspect_ratio")
    except ComfyUIError:
        options = []
    if options:
        return {"options": options, "source": "comfyui"}
    return {"options": config_store.DEFAULT_CONFIG["generation"]["aspect_ratios"], "source": "fallback"}




# ---------------------------------------------------------------------------
# Статус / запуск / память ComfyUI
# ---------------------------------------------------------------------------

@app.get("/api/comfyui/status")
def comfyui_status():
    alive = get_client().is_alive()
    return {"alive": alive, "launcher_running": launcher.is_running()}


@app.post("/api/comfyui/start")
def comfyui_start():
    cfg = config_store.load_config()
    bat_path = cfg["comfyui"].get("launch_bat_path")
    if not bat_path:
        raise HTTPException(
            400,
            "Путь к .bat запуска ComfyUI не задан в конфиге (comfyui.launch_bat_path)",
        )
    try:
        launcher.start(bat_path)
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@app.post("/api/comfyui/stop")
def comfyui_stop():
    launcher.stop()
    return {"ok": True}


@app.post("/api/comfyui/free-memory")
def comfyui_free_memory():
    """Выгружает модели из VRAM/RAM без перезапуска ComfyUI (POST
    /free). Доступно в обычном режиме -- это утилита на каждый день, а
    не настройка."""
    try:
        get_client().free_memory()
    except ComfyUIError as exc:
        raise HTTPException(502, f"ComfyUI недоступен: {exc}") from exc
    return {"ok": True}


# ---------------------------------------------------------------------------
# Генерация
# ---------------------------------------------------------------------------

class LoraSelection(BaseModel):
    file: str
    strength: float = 1.0  # допускает отрицательные значения


class GenerationRequest(BaseModel):
    positive_prompt: str
    negative_prompt: str = ""
    aspect_ratio: str
    megapixels: Optional[float] = None
    steps: int = Field(gt=0, le=150)
    scheduler: Optional[str] = None
    batch_size: int = Field(gt=0, le=16)
    seed: Optional[int] = None
    randomize_seed: bool = True
    cfg: Optional[float] = None
    sampler_name: Optional[str] = None
    loras: list[LoraSelection] = []


@app.post("/api/generate")
def generate(req: GenerationRequest):
    client = get_client()
    if not client.is_alive():
        raise HTTPException(503, "ComfyUI недоступен по указанному адресу")

    template = workflow.load_template()
    params = req.model_dump()
    try:
        graph, seed = workflow.build_graph(template, params)
    except workflow.WorkflowValidationError as exc:
        raise HTTPException(500, str(exc)) from exc

    try:
        prompt_id = client.queue_prompt(graph)
    except ComfyUIError as exc:
        raise HTTPException(502, f"ComfyUI отклонил задание: {exc}") from exc

    return {"prompt_id": prompt_id, "seed": seed}


@app.get("/api/generate/{prompt_id}/status")
def generation_status(prompt_id: str):
    client = get_client()
    try:
        entry = client.get_history(prompt_id)
    except ComfyUIError as exc:
        raise HTTPException(502, f"ComfyUI недоступен: {exc}") from exc

    if entry is not None:
        images = workflow.find_output_images(entry)
        status = entry.get("status", {})
        return {
            "state": "done" if images else ("error" if status.get("completed") is False else "done"),
            "images": [
                {
                    "url": f"/api/image?filename={img['filename']}"
                    f"&subfolder={img['subfolder']}&type={img['type']}"
                }
                for img in images
            ],
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
    get_client().interrupt()
    return {"ok": True}


@app.get("/api/image")
def get_image(filename: str, subfolder: str = "", type: str = "output"):
    client = get_client()
    try:
        data, content_type = client.fetch_image_bytes(filename, subfolder, type)
    except ComfyUIError as exc:
        raise HTTPException(502, str(exc)) from exc
    return Response(content=data, media_type=content_type)


# ---------------------------------------------------------------------------
# Статика
# ---------------------------------------------------------------------------

config_store.DATA_DIR.mkdir(parents=True, exist_ok=True)
config_store.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/media", StaticFiles(directory=str(config_store.UPLOADS_DIR)), name="media")
app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
