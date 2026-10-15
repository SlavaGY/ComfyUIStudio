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

import asyncio
import logging
import os
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from comfyui_studio.web_common import (
    NoCacheMiddleware,
    mount_common_static,
    register_ui_prefs,
    static_dir,
)

from . import config_store, launcher, lora_scan, progress_forwarder, promptgen, workflow
from .comfy_client import ComfyClient, ComfyUIError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("imagine")

DEV_MODE = os.environ.get("IMAGINE_DEV_MODE") == "1"

# Каталог статики резолвится от расположения пакета (и от sys._MEIPASS в
# собранном exe), а не от cwd: Studio запускает Imagine из корня проекта
# (`python -m comfyui_studio.imagine`, см. core/comfy_process.py), и
# относительный путь "static" отдавал бы 404 на всё. См. web_common.static_dir.
_STATIC_DIR = static_dir("imagine")

app = FastAPI(title="Imagine — локальный генератор на ComfyUI")


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


# НОВОЕ (Remote, этап 3 дорожной карты): порт Remote, куда
# progress_forwarder.py пересылает generation.progress -- IMAGINE_REMOTE_PORT
# выставляется __main__.py по CLI-аргументу --remote-port (см. его
# докстринг), тем же приёмом, что и IMAGINE_COMFY_HOST/PORT выше. Не
# обязателен -- если не задан, progress_forwarder просто не пересылает
# события никуда (см. его докстринг про best-effort).
def _seed_remote_port_from_env():
    port = os.environ.get("IMAGINE_REMOTE_PORT")
    if not port:
        return
    try:
        progress_forwarder.remote_port = int(port)
    except ValueError:
        log.warning("IMAGINE_REMOTE_PORT=%r не число, игнорирую", port)


_seed_remote_port_from_env()


_progress_forwarder_task = None


@app.on_event("startup")
async def _start_progress_forwarder():
    # on_event("startup") вместо lifespan= -- этот модуль не заводил
    # lifespan раньше (ни для чего другого он здесь не нужен), а
    # заводить его только ради одной фоновой задачи, когда есть более
    # точечный @app.on_event, было бы неоправданным расширением области
    # изменений этого этапа дорожной карты. (Да, on_event помечен
    # deprecated в новых версиях FastAPI -- если это когда-нибудь будет
    # мешать, тогда и стоит переезжать на lifespan= целиком.)
    global _progress_forwarder_task
    _progress_forwarder_task = asyncio.create_task(progress_forwarder.run_forever())


@app.on_event("shutdown")
async def _stop_progress_forwarder():
    if _progress_forwarder_task is not None:
        _progress_forwarder_task.cancel()
        with suppress(asyncio.CancelledError):
            await _progress_forwarder_task


@app.on_event("startup")
def _promptgen_startup_sweep():
    """При старте Imagine добиваем llama-server'ы, оставшиеся от
    аварийно завершившегося прошлого запуска (см. promptgen.sweep_stale)."""
    promptgen.generator.startup_sweep()


@app.on_event("shutdown")
def _stop_prompt_generator():
    """Штатное завершение Imagine: не оставляем llama-server висеть в VRAM
    (см. promptgen.py -- там же atexit и Job Object на Windows)."""
    promptgen.generator.shutdown()


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

register_ui_prefs(app)


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
    # ОТНОСИТЕЛЬНЫЙ путь (без ведущего "/") -- НЕ "/media/{name}" (см.
    # тот же приём и то же объяснение у "url" в /api/generate/{id}/status
    # ниже): страница Imagine теперь может открываться и по своему
    # собственному адресу (http://127.0.0.1:<imagine_port>/), и через
    # reverse-proxy Remote (http://127.0.0.1:<remote_port>/apps/imagine/,
    # см. remote/imagine_proxy.py, этап 4 дорожной карты Remote) -- с
    # ведущим "/" все ссылки на статику/API резолвились бы браузером
    # относительно КОРНЯ ТЕКУЩЕГО ORIGIN, а не текущей страницы, и при
    # заходе через прокси били бы мимо (в /media/... на порту Remote,
    # где ничего нет, вместо /apps/imagine/media/...). Без ведущего "/"
    # браузер резолвит путь относительно URL текущей страницы -- корректно
    # в обоих случаях.
    return {"url": f"media/{name}"}


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
# Генератор промптов (llama.cpp) -- кнопка под полем промпта
# ---------------------------------------------------------------------------
#
# Настройки берутся из общего файла Studio (см. shared_promptgen.py), а не
# из config.json Imagine -- поэтому, в отличие от /api/config, эти
# эндпоинты не требуют дев-режима: пользователь настраивает генератор в
# Studio, а здесь им только пользуется. Долгая работа (загрузка модели +
# генерация) вынесена в фоновую задачу с опросом статуса -- см. докстринг
# promptgen.py про таймаут reverse-proxy Remote.

class PromptGenStartRequest(BaseModel):
    text: str = ""
    image: Optional[str] = None  # data:image/...;base64,...
    image_name: Optional[str] = None  # имя исходного файла -- только для истории запросов


@app.get("/api/promptgen/status")
def promptgen_status():
    return promptgen.generator.status()


@app.post("/api/promptgen/start")
def promptgen_start(req: PromptGenStartRequest):
    def _free_comfy_memory():
        try:
            get_client().free_memory()
        except ComfyUIError:
            pass  # ComfyUI не запущен -- выгружать нечего

    try:
        job_id = promptgen.generator.start(
            req.text, req.image, pre_start=_free_comfy_memory, image_name=req.image_name
        )
    except promptgen.PromptGenError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return {"job_id": job_id}


@app.get("/api/promptgen/job/{job_id}")
def promptgen_job(job_id: str):
    snapshot = promptgen.generator.get_job(job_id)
    if snapshot is None:
        raise HTTPException(404, "Задача генерации промпта не найдена")
    return snapshot


@app.post("/api/promptgen/job/{job_id}/cancel")
def promptgen_cancel(job_id: str):
    return {"ok": promptgen.generator.cancel(job_id)}


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


@app.get("/api/generate/recent")
def recent_generations(limit: int = 20):
    """НОВОЕ (§Этап 6.5 дорожной карты, живой отчёт: несколько генераций
    в очереди -> несколько push-уведомлений -> каждый переход открывал
    изолированную сессию с одной картинкой вместо общей галереи). Не
    завязано на конкретный prompt_id -- отдаёт последние `limit`
    завершённых генераций разом (см. ComfyClient.get_recent_history),
    от новых к старым. `initDeepLinkedGeneration()` в app.js вызывает
    это вместо (а не только вместе с) одиночного опроса одного
    prompt_id, когда страница открыта по deep-link'у из push -- так все
    недавние генерации видны сразу, независимо от того, по какому
    именно уведомлению был переход."""
    client = get_client()
    try:
        entries = client.get_recent_history(limit)
    except ComfyUIError as exc:
        raise HTTPException(502, f"ComfyUI недоступен: {exc}") from exc

    items = []
    for prompt_id, entry in entries:
        images = workflow.find_output_images(entry)
        if not images:
            # Запись есть в истории, но без выходных изображений (напр.
            # само задание завершилось ошибкой) -- не показываем её в
            # этом списке; ошибку конкретной генерации, если это была
            # именно та, что упомянута в уведомлении, по-прежнему видно
            # через обычный /status (см. app.js -- используется как
            # запасной путь, если prompt_id из URL не нашёлся здесь).
            continue
        items.append({
            "prompt_id": prompt_id,
            # Тот же относительный путь и та же причина, что и в
            # /api/generate/{id}/status выше -- см. комментарий там.
            "images": [
                {
                    "url": f"api/image?filename={img['filename']}"
                    f"&subfolder={img['subfolder']}&type={img['type']}"
                }
                for img in images
            ],
        })
    return {"items": items}


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
                    # ОТНОСИТЕЛЬНЫЙ путь -- см. подробное объяснение у
                    # /api/upload-image выше (тот же самый баг, только
                    # здесь он реально всплыл на этапе 4 дорожной карты
                    # Remote: при открытии Imagine через
                    # http://<pc>:<remote_port>/apps/imagine/ браузер
                    # запрашивал картинки с АБСОЛЮТНОГО "/api/image?..."
                    # -- то есть с http://<pc>:<remote_port>/api/image?...,
                    # мимо прокси, 404).
                    "url": f"api/image?filename={img['filename']}"
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
mount_common_static(app)  # /common -- общий JS движок переводов; ДО корневого "/"
app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
