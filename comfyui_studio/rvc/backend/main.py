"""
FastAPI-приложение «Ultimate RVC» для Studio (по образцу imagine/backend/main.py).

Запускается ПИТОНОМ ИЗ venv Ultimate RVC (`python -m comfyui_studio.rvc`, см.
launcher/core/rvc_process.py): torch и ultimate_rvc живут там, а не в
процессе Studio. Поэтому здесь только stdlib + fastapi/uvicorn (они есть в
venv RVC как зависимости gradio) + сам ultimate_rvc через engine.py. Никаких
импортов Qt и остального кода Studio, кроме необязательного shared_theme/
shared_language (общая тема/язык комплекта; нет модуля -- работаем без них).

Загрузка файлов -- сырым телом запроса (PUT), без multipart: так приложению не
нужен python-multipart.

Пути в index.html/JS -- ОТНОСИТЕЛЬНЫЕ (без ведущего «/»), как у Imagine: когда
понадобится открыть приложение через Remote-прокси, страница не должна
промахиваться мимо своего префикса.
"""

from __future__ import annotations

import mimetypes
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import params as P
from .engine import ERROR, READY, Engine
from .jobs import FINISHED, JobManager

try:  # общая тема/язык комплекта -- необязательны
    from comfyui_studio import shared_language, shared_theme
except ImportError:
    shared_theme = None
    shared_language = None

# __main__.py выставляет переменную ДО импорта этого модуля.
DATA_DIR = Path(os.environ.get("STUDIO_RVC_DATA_DIR") or (Path.home() / ".studio_rvc"))
UPLOAD_DIR = DATA_DIR / "uploads"
TEXT_DIR = DATA_DIR / "texts"
MAX_UPLOAD_BYTES = 2 * 1024 ** 3
UPLOAD_TTL_SECONDS = 7 * 24 * 3600
_UPLOAD_ID = re.compile(r"^[0-9a-f]{12}$")

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

engine = Engine(DATA_DIR)


def _runner(job, report):
    if job.kind == "song_cover":
        return engine.run_song_cover(job.params, report)
    if job.kind == "speech":
        return engine.run_speech(job.params, report)
    raise RuntimeError(f"Неизвестный тип задания: {job.kind}")


jobs = JobManager(_runner)


def _cleanup_old_files():
    """Загрузки и тексты старше недели удаляются при старте -- иначе папка
    данных росла бы с каждой песней."""
    cutoff = time.time() - UPLOAD_TTL_SECONDS
    for folder in (UPLOAD_DIR, TEXT_DIR):
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass


@asynccontextmanager
async def lifespan(_app):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup_old_files()
    engine.start_warmup()
    yield
    jobs.shutdown()


app = FastAPI(title="Studio · Ultimate RVC", lifespan=lifespan)


@app.middleware("http")
async def _no_store(request, call_next):
    # Страницы/JS/API приложения не кэшируются: по тому же адресу может позже
    # открыться другой сервер (оригинальный Gradio), и устаревший index.html
    # из кэша браузера показывал бы «наше» приложение поверх чужого сервера.
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(P.ParamError)
async def _param_error(_request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(Exception)
async def _any_error(_request, exc):
    # Ошибки самого ultimate_rvc (модель не найдена, плохая ссылка, нет сети)
    # приходят со своими понятными текстами -- показываем их как есть.
    return JSONResponse({"detail": str(exc) or type(exc).__name__}, status_code=500)


def _require_ready():
    if engine.state == ERROR:
        raise HTTPException(503, engine.error)
    if engine.state != READY:
        raise HTTPException(503, "Движок RVC ещё загружается — подождите несколько секунд")


# ---------------------------------------------------------------------------
# Состояние и справочники
# ---------------------------------------------------------------------------


@app.get("/api/health")
def health():
    return {
        "state": engine.state,
        "error": engine.error,
        "jobs_active": sum(1 for j in jobs.list() if j.status not in FINISHED),
    }


@app.get("/api/options")
def options():
    _require_ready()
    return engine.options()


@app.get("/api/models")
def list_models():
    _require_ready()
    return {"models": engine.voice_models()}


@app.get("/api/edge-voices")
def edge_voices():
    _require_ready()
    return {"voices": engine.edge_voices()}


@app.get("/api/models/public")
def public_models():
    _require_ready()
    return engine.public_models()


# ---------------------------------------------------------------------------
# Загрузка файлов
# ---------------------------------------------------------------------------


@app.put("/api/upload")
async def upload(request: Request, name: str = "file"):
    """Сырое тело запроса -> файл в папке загрузок. Возвращает id для
    последующих запросов (кавер / импорт модели)."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    upload_id = uuid.uuid4().hex[:12]
    filename = P.safe_filename(name)
    path = UPLOAD_DIR / f"{upload_id}__{filename}"
    size = 0
    try:
        with open(path, "wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "Файл слишком большой (лимит 2 ГБ)")
                out.write(chunk)
    except HTTPException:
        path.unlink(missing_ok=True)
        raise
    if size == 0:
        path.unlink(missing_ok=True)
        raise HTTPException(400, "Пустой файл")
    return {"id": upload_id, "name": filename, "size": size}


def _upload_path(upload_id):
    if not _UPLOAD_ID.match(str(upload_id or "")):
        raise HTTPException(400, "Некорректный идентификатор загрузки")
    found = list(UPLOAD_DIR.glob(f"{upload_id}__*"))
    if not found:
        raise HTTPException(404, "Загруженный файл не найден — загрузите его заново")
    return found[0]


# ---------------------------------------------------------------------------
# Модели
# ---------------------------------------------------------------------------


@app.post("/api/models/download")
def download_model(body: dict):
    _require_ready()
    url = str(body.get("url") or "").strip()
    if not P.is_url(url):
        raise P.ParamError("Укажите ссылку на zip-архив модели (http/https)")
    engine.download_model(url, body.get("name"))
    return {"ok": True, "models": engine.voice_models()}


@app.post("/api/models/import")
def import_model(body: dict):
    _require_ready()
    ids = body.get("upload_ids") or []
    if not ids:
        raise P.ParamError("Выберите файлы модели (.pth и .index или один .zip)")
    paths = [_upload_path(i) for i in ids]
    engine.import_model(body.get("name"), paths)
    return {"ok": True, "models": engine.voice_models()}


@app.delete("/api/models/{name}")
def delete_model(name: str):
    _require_ready()
    if name in jobs.busy_models():
        raise HTTPException(409, "Модель используется заданием в очереди — дождитесь его окончания")
    engine.delete_model(name)
    return {"ok": True, "models": engine.voice_models()}


# ---------------------------------------------------------------------------
# Задания
# ---------------------------------------------------------------------------


def _check_model(body):
    name = str(body.get("model_name") or "").strip()
    if not name:
        raise P.ParamError("Выберите голосовую модель")
    if name not in engine.voice_models():
        raise P.ParamError(f"Модель «{name}» не найдена")
    return name


@app.post("/api/jobs/song-cover")
def submit_song_cover(body: dict):
    _require_ready()
    model = _check_model(body)
    if body.get("upload_id"):
        path = _upload_path(body["upload_id"])
        source, label = str(path), path.name.split("__", 1)[-1]
    else:
        source = str(body.get("source") or "").strip()
        if not source:
            raise P.ParamError("Укажите ссылку YouTube или выберите аудиофайл")
        if not P.is_url(source) and not Path(source).exists():
            raise P.ParamError(f"Файл не найден: {source}")
        label = source
    params = {**body, "source": source, "model_name": model}
    P.song_cover_kwargs(params)  # проверка параметров ДО постановки в очередь
    job = jobs.submit("song_cover", f"{model} · {label[:60]}", params)
    return job.snapshot()


@app.post("/api/jobs/speech")
def submit_speech(body: dict):
    _require_ready()
    model = _check_model(body)
    text = str(body.get("text") or "").strip()
    if not text:
        raise P.ParamError("Введите текст для озвучки")
    # Текст отдаётся пайплайну ФАЙЛОМ: источник у него -- «строка или путь», и
    # длинный текст с переводами строк он мог бы принять за путь к файлу.
    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    text_path = TEXT_DIR / f"{uuid.uuid4().hex[:12]}.txt"
    text_path.write_text(text, encoding="utf-8")
    params = {**body, "source": str(text_path), "model_name": model}
    P.speech_kwargs(params)
    job = jobs.submit("speech", f"{model} · {text[:50]}", params)
    return job.snapshot()


@app.get("/api/jobs")
def list_jobs():
    return {"jobs": [j.snapshot() for j in jobs.list()]}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Задание не найдено")
    return job.snapshot()


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: int):
    if not jobs.cancel(job_id):
        raise HTTPException(404, "Задание не найдено или уже завершено")
    return {"ok": True}


@app.get("/api/jobs/{job_id}/files/{index}")
def job_file(job_id: int, index: int, download: int = 0):
    job = jobs.get(job_id)
    if job is None or not 0 <= index < len(job.files):
        raise HTTPException(404, "Файл не найден")
    entry = job.files[index]
    path = Path(entry["path"])
    if not path.is_file():
        raise HTTPException(404, "Файл удалён с диска")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    # Без filename браузер играет файл прямо в <audio>; с ним -- скачивает.
    return FileResponse(path, media_type=media_type, filename=path.name if download else None)


# ---------------------------------------------------------------------------
# Тема / язык -- общие с остальным комплектом (как у Imagine)
# ---------------------------------------------------------------------------


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


app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
