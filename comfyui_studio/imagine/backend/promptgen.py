"""
Генератор промптов: по кнопке в Imagine запускает llama-server (llama.cpp)
с GGUF-моделью, отправляет ей запрос, забирает ответ и ВЫКЛЮЧАЕТ модель.

Настройки (путь к llama.cpp, модель, mmproj, префикс запроса) читаются из
общего файла %APPDATA%\\ComfyUIStudio\\prompt_generator.json при КАЖДОМ
запуске задачи (см. shared_promptgen.py) -- их пишет страница «Генератор
промптов» в настройках Studio, перезапускать Imagine после правок не нужно.

Как это работает
----------------
Задача (Job) -- один запуск «start → ответ → stop». Одновременно живёт
максимум одна: модель занимает VRAM, две параллельно нужны редко, а
конкурировать за видеокарту ещё и с ComfyUI -- тем более.

  0. Подготовка VRAM: при необходимости (см. free_comfy_mode) выгружаются
     модели ComfyUI, и ждём, пока драйвер реально освободит память.
  1. Поток задачи запускает `llama-server` на свободном локальном порту
     (cwd = папка llama.cpp, PATH дополнен папками с DLL, см.
     shared_promptgen.dll_dirs()); весь вывод процесса идёт в файл.
  2. Опрашивает GET /health, пока модель не загрузится (503 -- ещё
     грузится, 200 -- готова). Если процесс умер -- отдаёт хвост его
     вывода как текст ошибки.
  3. Шлёт POST /v1/chat/completions со stream=true (картинка, если есть,
     идёт как data:-URL в image_url) и копит ответ -- фронтенд опрашивает
     статус и показывает промежуточный текст. max_tokens = 8192.
  4. Останавливает llama-server (со всем деревом процессов) ПЕРЕД тем, как
     пометить задачу выполненной -- когда интерфейс видит «готово»,
     видеопамять уже свободна.

Почему опрос статуса, а не один долгий запрос: страница Imagine может быть
открыта через reverse-proxy Remote (с телефона), у которого таймаут 60 с
(см. remote/imagine_proxy.py) -- загрузка модели + генерация легко его
превышают.

Логи (%APPDATA%\\ComfyUIStudio\\logs\\)
---------------------------------------
  * promptgen.log -- ход каждой задачи с меткой [id]: настройки, снимки
    VRAM/RAM/процессов llama до и после, решение о выгрузке ComfyUI, pid и
    команда llama-server, тайминги (загрузка / до первого токена /
    генерация, ток/с из самого llama-server), предупреждения (модель
    загружена на GPU не полностью, mmproj на CPU), ошибки с трассировкой.
    Текст запроса и ответа НЕ пишется -- только длины.
  * llama-server/<время>-<id>.log -- полный вывод llama-server каждого
    запуска (последние LLAMA_LOGS_KEEP штук).

Защита от «осиротевшего» llama-server (он держит гигабайты VRAM)
-----------------------------------------------------------------
  * процесс всегда убивается вместе с деревом потомков (psutil; на
    случай отсутствия -- taskkill /T), а не только «головной» pid: обёртка
    llama-server из LM Studio могла бы оставить потомка жить;
  * pid запущенного процесса пишется в promptgen_running.json; при старте
    Imagine и перед каждой задачей уцелевшие с прошлого раза процессы
    (аварийное завершение Imagine) добиваются -- sweep_stale();
  * при остановке Imagine из лаунчера дочерние процессы гасит
    `taskkill /T` (см. ImagineProcess.stop()), плюс shutdown-хук FastAPI и
    atexit;
  * на Windows процесс дополнительно помещается в Job Object с флагом
    KILL_ON_JOB_CLOSE -- ОС убьёт llama-server, даже если Imagine упал
    аварийно (см. _attach_kill_on_close_job()).
"""

from __future__ import annotations

import atexit
import collections
import datetime
import http.client
import json
import logging
import logging.handlers
import os
import re
import shlex
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Callable, Optional

try:
    # Доступно только внутри ComfyUIStudio. Если Imagine запущен как
    # самостоятельный инструмент (без остального комплекта), генератор
    # просто сообщает available=False, и кнопка в интерфейсе не
    # показывается -- как и с shared_theme/shared_language в main.py.
    from comfyui_studio import shared_promptgen
except ImportError:  # pragma: no cover - Imagine запущен отдельно от Studio
    shared_promptgen = None

log = logging.getLogger("imagine.promptgen")

MAX_TOKENS = 8192
# Загрузка большой модели с медленного диска может занять минуты.
LOAD_TIMEOUT_S = 600
# Таймаут одной операции чтения сокета при стриминге ответа. Между
# токенами пауза может быть долгой только пока обрабатывается длинный
# запрос с картинкой -- запас с избытком.
STREAM_TIMEOUT_S = 1800
# data:-URL картинки: 25 МБ бинарных данных ~ 34 МБ в base64. Фронтенд
# уменьшает картинку заранее, так что это лишь защита от заведомо
# неадекватных запросов.
MAX_IMAGE_DATA_URL_LEN = 34 * 1024 * 1024

# Запросы к llama-server идут строго на localhost -- в обход системного
# прокси. urllib по умолчанию берёт прокси из переменных окружения и (на
# Windows) из реестра, а правило «<local>» в реестре НЕ считает локальным
# адрес вида 127.0.0.1 -- с включённым системным прокси (Clash, v2ray и
# т. п.) health-check и запрос ушли бы в прокси и не дошли бы до сервера.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"<think>", re.IGNORECASE)

# 0xC0000135 (STATUS_DLL_NOT_FOUND) -- Windows не нашла нужную DLL.
_STATUS_DLL_NOT_FOUND = 0xC0000135

# Строки вывода llama-server, которые стоит скопировать в promptgen.log
# (полный вывод -- в отдельном файле): сколько слоёв на GPU, на чём
# работает кодировщик изображений, размеры буферов (в т. ч. KV-кэша).
_KEY_LINE_RE = re.compile(
    r"offloaded|CLIP using|buffer size|n_ctx\b|n_gpu_layers|fit|flash.attn|warmup|"
    r"projected|mmproj|mtmd|projector|clip|image_(min|max)_tokens|error|failed|out of memory|"
    r"CUDA|compute capability|device|GPU|layers|MiB",
    re.IGNORECASE,
)
# Метка времени в выводе llama-server: [минуты.секунды.миллисекунды.микросекунды]
# от старта процесса, например «5.56.717.660» = 5 мин 56.7 с.
_TS_RE = re.compile(r"^\s*(\d+)\.(\d{2})\.(\d{3})\.(\d{3})\s")

# Как часто писать в лог ход загрузки модели (с замерами процесса).
_FIRST_REPORT_S = 5.0
_REPORT_EVERY_S = 15.0
_OFFLOAD_RE = re.compile(r"offloaded\s+(\d+)\s*/\s*(\d+)\s+layers\s+to\s+GPU", re.IGNORECASE)
_CLIP_BACKEND_RE = re.compile(r"CLIP using (\S+) backend", re.IGNORECASE)

_MIB = 1024 * 1024


class PromptGenError(Exception):
    """Ожидаемая ошибка с готовым для пользователя текстом и HTTP-кодом
    (используется эндпоинтами в main.py)."""

    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code


class _Cancelled(Exception):
    pass


# ---------------------------------------------------------------------------
# Логирование
# ---------------------------------------------------------------------------

_file_handler: Optional[logging.Handler] = None
_file_handler_path: Optional[str] = None
_console_handler: Optional[logging.Handler] = None


def _ensure_file_logging() -> None:
    """Подключает к логгеру генератора файл promptgen.log (ротация 1 МБ × 3)
    и консоль (только INFO+). Идемпотентно; если путь лога сменился (тесты) --
    переподключает. Файл создаётся лениво, при первой записи.

    propagate=False: иначе DEBUG-записи нашего логгера дошли бы до
    консольного хендлера корневого логгера (basicConfig в main.py)."""
    global _file_handler, _file_handler_path, _console_handler
    if shared_promptgen is None:
        return
    log.setLevel(logging.DEBUG)
    log.propagate = False
    if _console_handler is None:
        _console_handler = logging.StreamHandler()
        _console_handler.setLevel(logging.INFO)
        _console_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(_console_handler)
    path = shared_promptgen.log_file_path()
    if _file_handler_path == path:
        return
    if _file_handler is not None:
        log.removeHandler(_file_handler)
        _file_handler.close()
        _file_handler = None
    _file_handler_path = path
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=1_000_000, backupCount=3, encoding="utf-8", delay=True
        )
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
        log.addHandler(handler)
        _file_handler = handler
    except OSError:
        log.warning("Не удалось открыть файл лога %s", path, exc_info=True)


def _jlog(job: "Job", level: int, msg: str, *args, **kwargs) -> None:
    log.log(level, "[%s] " + msg, job.id, *args, **kwargs)


# ---------------------------------------------------------------------------
# Диагностика: VRAM / RAM / процессы
# ---------------------------------------------------------------------------

def _psutil():
    try:
        import psutil
        return psutil
    except ImportError:
        return None


def gpu_memory() -> Optional[list[dict]]:
    """Список видеокарт с объёмом памяти (МиБ) через pynvml, либо None,
    если pynvml/драйвер недоступны."""
    try:
        import pynvml
    except ImportError:
        return None
    try:
        pynvml.nvmlInit()
    except Exception:
        return None
    try:
        gpus = []
        for i in range(pynvml.nvmlDeviceGetCount()):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8", "replace")
            gpus.append({
                "index": i,
                "name": name,
                "total_mb": int(mem.total // _MIB),
                "used_mb": int(mem.used // _MIB),
                "free_mb": int(mem.free // _MIB),
            })
        return gpus
    except Exception:
        return None
    finally:
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass


def _max_free_mb(gpus: Optional[list[dict]]) -> Optional[int]:
    return max(g["free_mb"] for g in gpus) if gpus else None


def _fmt_gpus(gpus: Optional[list[dict]]) -> str:
    if gpus is None:
        return "GPU: недоступно (нет pynvml/драйвера NVIDIA)"
    if not gpus:
        return "GPU: не найдено"
    return "GPU: " + "; ".join(
        f"#{g['index']} {g['name']}: занято {g['used_mb']}/{g['total_mb']} МиБ, свободно {g['free_mb']}"
        for g in gpus
    )


def _system_snapshot(gpus: Optional[list[dict]] = None) -> str:
    """Одна строка для лога: VRAM, RAM и все процессы с «llama» в имени (в
    т. ч. чужие -- например, LM Studio держит свою модель в той же
    видеопамяти)."""
    parts = [_fmt_gpus(gpus if gpus is not None else gpu_memory())]
    psutil = _psutil()
    if psutil is not None:
        try:
            vm = psutil.virtual_memory()
            parts.append(f"RAM: свободно {vm.available // _MIB}/{vm.total // _MIB} МиБ")
        except Exception:
            pass
        try:
            found = []
            for proc in psutil.process_iter(["pid", "name", "memory_info"]):
                name = proc.info.get("name") or ""
                if "llama" in name.lower():
                    rss = proc.info["memory_info"].rss // _MIB if proc.info.get("memory_info") else 0
                    found.append(f"{proc.info['pid']}:{name} {rss} МиБ")
            parts.append("процессы llama: " + (", ".join(found) if found else "нет"))
        except Exception:
            pass
    return " | ".join(parts)


def estimate_vram_mb(cfg: dict) -> int:
    """Грубая оценка видеопамяти под запуск: размеры файлов модели и mmproj
    (+5 %), запас на служебные буферы и ~0.06 МиБ на токен контекста (KV-кэш
    типичной 7–8B модели; у больших/иных моделей отличается). Нужна только
    для решения «выгружать ли ComfyUI» -- ошибка в 20–30 % не критична."""
    total = 0
    for key in ("model_path", "mmproj_path"):
        path = (cfg.get(key) or "").strip()
        if path:
            try:
                total += os.path.getsize(path)
            except OSError:
                pass
    ctx = cfg.get("ctx_size") or 8192
    return int(total / _MIB * 1.05 + 512 + ctx * 0.06)


# ---------------------------------------------------------------------------
# Windows: Job Object с KILL_ON_JOB_CLOSE
# ---------------------------------------------------------------------------

_job_handle = None


def _attach_kill_on_close_job(proc: subprocess.Popen) -> None:
    """Best-effort: ассоциирует процесс с Job Object'ом, который убивает всех
    своих участников, когда закрывается последний дескриптор -- то есть
    когда умирает сам Imagine (даже аварийно). Любая ошибка здесь только
    логируется: это подстраховка, а не необходимое условие работы."""
    global _job_handle
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class BASIC_LIMIT(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class EXTENDED_LIMIT(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC_LIMIT),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

        if _job_handle is None:
            handle = kernel32.CreateJobObjectW(None, None)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            info = EXTENDED_LIMIT()
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(
                handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info), ctypes.sizeof(info),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            _job_handle = handle

        if not kernel32.AssignProcessToJobObject(_job_handle, int(proc._handle)):
            raise ctypes.WinError(ctypes.get_last_error())
    except Exception:
        log.warning("Не удалось привязать llama-server к Job Object", exc_info=True)



# ---------------------------------------------------------------------------
# Диагностика загрузки модели
# ---------------------------------------------------------------------------

def _line_seconds(line: str) -> Optional[float]:
    match = _TS_RE.match(line)
    if not match:
        return None
    minutes, seconds, millis, micros = (int(g) for g in match.groups())
    return minutes * 60 + seconds + millis / 1000 + micros / 1e6


def analyze_load_timeline(text: str, min_gap_s: float = 1.0, top: int = 8) -> list[str]:
    """Самые долгие паузы между строками вывода llama-server (по его же
    меткам времени): показывает, на каком этапе загрузки ушло время --
    что было напечатано перед паузой и что после."""
    prev_ts: Optional[float] = None
    prev_line = ""
    gaps: list[tuple[float, str, str]] = []
    for line in text.splitlines():
        ts = _line_seconds(line)
        if ts is None:
            continue
        if prev_ts is not None and ts - prev_ts >= min_gap_s:
            gaps.append((ts - prev_ts, prev_line.strip(), line.strip()))
        prev_ts, prev_line = ts, line
    gaps.sort(key=lambda g: -g[0])
    return [f"пауза {gap:.1f} с между «{a[:110]}» и «{b[:110]}»" for gap, a, b in gaps[:top]]


def _compute_cache_dir() -> Optional[str]:
    """Папка кэша скомпилированных CUDA-ядер драйвера NVIDIA (JIT из PTX)."""
    custom = os.environ.get("CUDA_CACHE_PATH")
    if custom:
        return custom
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        return os.path.join(appdata, "NVIDIA", "ComputeCache") if appdata else None
    return os.path.join(os.path.expanduser("~"), ".nv", "ComputeCache")


def _dir_size_mb(path: Optional[str]) -> Optional[float]:
    if not path or not os.path.isdir(path):
        return None
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
    except OSError:
        return None
    return total / _MIB


class _LoadProbe:
    """Замеры процесса llama-server во время загрузки модели: по ним видно,
    ЧЕМ он занят -- считает на CPU (JIT-компиляция, обработка на CPU), читает
    диск (или ждёт его из-за нехватки RAM/подкачки) или простаивает."""

    def __init__(self, pid: int):
        self._psutil = _psutil()
        self._ps = None
        self._disk = None
        self._t = time.monotonic()
        if self._psutil is None:
            return
        try:
            self._ps = self._psutil.Process(pid)
            self._ps.cpu_percent(None)   # первый вызов только запускает отсчёт
            self._psutil.cpu_percent(None)
        except Exception:
            self._ps = None
        self._disk = self._disk_read()

    def _disk_read(self) -> Optional[int]:
        try:
            return self._psutil.disk_io_counters().read_bytes
        except Exception:
            return None

    def describe(self) -> str:
        if self._psutil is None or self._ps is None:
            return "замеры процесса недоступны (нет psutil)"
        psutil, ps = self._psutil, self._ps
        parts = []
        try:
            parts.append(f"CPU процесса {ps.cpu_percent(None):.0f}% (100% = одно ядро, всего ядер {psutil.cpu_count()})")
        except Exception:
            pass
        try:
            parts.append(f"CPU системы {psutil.cpu_percent(None):.0f}%")
        except Exception:
            pass
        try:
            parts.append(f"RSS {ps.memory_info().rss // _MIB} МиБ")
        except Exception:
            pass
        try:
            now, disk = time.monotonic(), self._disk_read()
            if disk is not None and self._disk is not None:
                parts.append(f"диск (вся система) прочитано за {now - self._t:.0f} с: {(disk - self._disk) // _MIB} МиБ")
            self._disk, self._t = disk, now
        except Exception:
            pass
        try:
            parts.append(f"RAM свободно {psutil.virtual_memory().available // _MIB} МиБ")
        except Exception:
            pass
        gpus = gpu_memory()
        if gpus:
            parts.append(f"VRAM занято {gpus[0]['used_mb']} МиБ")
        return ", ".join(parts)


# ---------------------------------------------------------------------------
# Процесс llama-server
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _split_extra_args(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    try:
        parts = shlex.split(text, posix=(os.name != "nt"))
    except ValueError as exc:
        raise PromptGenError(
            f"Не удалось разобрать «Доп. аргументы» llama-server: {exc}", 400
        ) from exc
    # при posix=False shlex оставляет кавычки внутри токенов
    cleaned = []
    for part in parts:
        if len(part) >= 2 and part[0] == part[-1] and part[0] in "\"'":
            part = part[1:-1]
        cleaned.append(part)
    return cleaned


def build_command(cfg: dict, port: int) -> list[str]:
    exe = shared_promptgen.find_server_exe(cfg["llama_dir"].strip())
    if exe is None:  # validate() уже проверил, но состояние диска могло измениться
        raise PromptGenError("llama-server не найден в папке llama.cpp.", 400)
    ctx = cfg.get("ctx_size") or 0
    cmd = [
        exe,
        "-m", cfg["model_path"].strip(),
        "--host", "127.0.0.1",
        "--port", str(port),
        # одна «ячейка» обработки: не выделять память под несколько
        # параллельных запросов, нужный ровно один
        "-np", "1",
        # шаблон чата из самой модели + chat_template_kwargs в запросе
        "--jinja",
    ]
    if ctx > 0:
        cmd += ["-c", str(ctx)]
    mmproj = cfg.get("mmproj_path", "").strip()
    if mmproj:
        cmd += ["--mmproj", mmproj]
    gpu_layers = cfg.get("gpu_layers", "").strip()
    if gpu_layers:
        cmd += ["-ngl", gpu_layers]
    cmd += _split_extra_args(cfg.get("extra_args", ""))
    return cmd


def build_env(cfg: dict) -> dict:
    env = os.environ.copy()
    dirs = shared_promptgen.dll_dirs(cfg)
    if dirs:
        env["PATH"] = os.pathsep.join(dirs + [env.get("PATH", "")])
    return env


def _llama_log_path(job_id: str) -> str:
    """Файл для вывода llama-server этого запуска; старые файлы удаляются
    (остаются последние LLAMA_LOGS_KEEP)."""
    directory = shared_promptgen.llama_log_dir()
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        directory = tempfile.gettempdir()
    name = f"{datetime.datetime.now():%Y%m%d-%H%M%S}-{job_id}.log"
    path = os.path.join(directory, name)
    try:
        old = sorted(
            (os.path.join(directory, f) for f in os.listdir(directory) if f.endswith(".log")),
            key=os.path.getmtime,
        )
        for stale in old[: max(0, len(old) - shared_promptgen.LLAMA_LOGS_KEEP + 1)]:
            try:
                os.remove(stale)
            except OSError:
                pass
    except OSError:
        pass
    return path


def _read_log_tail(path: str, lines: int = 15, max_bytes: int = 32768) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read()
    except OSError:
        return ""
    text = data.decode("utf-8", "replace")
    return "\n".join(text.splitlines()[-lines:]).strip()


def _read_log_text(path: str, max_bytes: int = 400_000) -> str:
    try:
        with open(path, "rb") as f:
            data = f.read(max_bytes)
    except OSError:
        return ""
    return data.decode("utf-8", "replace")


def _spawn(cmd: list[str], cwd: str, env: dict, log_path: str):
    """Запускает llama-server, перенаправляя ВЕСЬ его вывод в файл (а не в
    pipe): нет потока-читателя, который мог бы зависнуть на закрытии pipe,
    если у процесса остался живой потомок, и вывод сохраняется целиком для
    разбора. Возвращает (Popen, открытый файл лога)."""
    log_fh = open(log_path, "ab")
    log_fh.write(
        f"# {datetime.datetime.now().isoformat(timespec='seconds')}\n# cmd: {cmd}\n\n".encode("utf-8")
    )
    log_fh.flush()
    kwargs = dict(
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.Popen(cmd, **kwargs)
    except OSError as exc:
        log_fh.close()
        raise PromptGenError(f"Не удалось запустить llama-server: {exc}", 500) from exc
    _attach_kill_on_close_job(proc)
    return proc, log_fh


def _kill_tree(proc: Optional[subprocess.Popen]) -> str:
    """Убивает процесс и ВСЕХ его потомков; ждёт завершения (с таймаутом) и
    возвращает краткий отчёт для лога. Безопасно вызывать повторно и из
    нескольких потоков (cancel() и завершающий код задачи)."""
    if proc is None:
        return "нет процесса"
    pid = proc.pid
    psutil = _psutil()
    report = ""
    if psutil is not None:
        try:
            try:
                parent = psutil.Process(pid)
                victims = parent.children(recursive=True) + [parent]
            except psutil.NoSuchProcess:
                victims = []
            for victim in victims:
                try:
                    victim.terminate()
                except psutil.NoSuchProcess:
                    pass
            _gone, alive = psutil.wait_procs(victims, timeout=5)
            for victim in alive:
                try:
                    victim.kill()
                except psutil.NoSuchProcess:
                    pass
            if alive:
                _gone2, alive = psutil.wait_procs(alive, timeout=5)
            report = f"убито процессов: {len(victims)}"
            if alive:
                report += f", НЕ УДАЛОСЬ убить: {[p.pid for p in alive]}"
        except Exception as exc:
            report = f"ошибка psutil: {exc}"
    else:
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except Exception as exc:
                report = f"ошибка taskkill: {exc}"
        elif proc.poll() is None:
            proc.terminate()
        report = report or "без psutil"
    try:
        proc.wait(timeout=5)  # забираем код выхода, не оставляя зомби
        if proc.poll() is None:
            proc.kill()
    except subprocess.TimeoutExpired:
        proc.kill()
    except Exception:
        pass
    return report


def _stop_process(proc: Optional[subprocess.Popen], log_fh=None) -> Optional[str]:
    """Останавливает llama-server и закрывает файл его лога. Возвращает
    отчёт для лога (None, если останавливать было нечего)."""
    if proc is None:
        return None
    t0 = time.monotonic()
    try:
        report = _kill_tree(proc)
    except Exception as exc:
        log.exception("Не удалось остановить llama-server")
        report = f"ошибка: {exc}"
    finally:
        if log_fh is not None:
            try:
                log_fh.close()
            except Exception:
                pass
    return f"pid {proc.pid}: {report}, {time.monotonic() - t0:.1f} с"


def _exit_message(proc: subprocess.Popen, log_path: str, phase: str = "при запуске") -> str:
    code = proc.returncode
    lines = _read_log_tail(log_path, 15)
    msg = f"llama-server завершился {phase} (код {code})."
    if code is not None and (code & 0xFFFFFFFF) == _STATUS_DLL_NOT_FOUND:
        msg += (
            " Windows не нашла нужную DLL (0xC0000135): для llama-server из "
            "LM Studio нужна папка vendor с CUDA-библиотеками — укажите её в "
            "«Доп. папка с DLL» в настройках Studio, либо используйте обычный "
            "релиз llama.cpp."
        )
    if lines:
        msg += "\n" + lines
    return msg


# ---------------------------------------------------------------------------
# Реестр запущенных процессов (уборка «осиротевших» после аварии Imagine)
# ---------------------------------------------------------------------------

def _registry_path() -> str:
    return os.path.join(shared_promptgen.SHARED_DIR, "promptgen_running.json")


def _registry_read() -> list[dict]:
    try:
        with open(_registry_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return [e for e in data if isinstance(e, dict) and "pid" in e] if isinstance(data, list) else []
    except Exception:
        return []


def _registry_write(entries: list[dict]) -> None:
    try:
        os.makedirs(shared_promptgen.SHARED_DIR, exist_ok=True)
        with open(_registry_path(), "w", encoding="utf-8") as f:
            json.dump(entries, f)
    except OSError:
        pass


def _register_process(proc: subprocess.Popen, job_id: str) -> None:
    psutil = _psutil()
    if psutil is None:
        return
    try:
        created = psutil.Process(proc.pid).create_time()
    except Exception:
        return
    entries = [e for e in _registry_read() if e.get("pid") != proc.pid]
    entries.append({"pid": proc.pid, "create_time": created, "job": job_id})
    _registry_write(entries)


def _unregister_process(pid: int) -> None:
    entries = _registry_read()
    remaining = [e for e in entries if e.get("pid") != pid]
    if len(remaining) != len(entries):
        _registry_write(remaining)


def sweep_stale() -> int:
    """Добивает llama-server'ы, оставшиеся от прошлых запусков Imagine
    (аварийное завершение, где не сработали ни хуки, ни Job Object).
    Убивает ТОЛЬКО процессы из нашего реестра, у которых совпали pid, время
    создания и «llama» в имени -- переиспользованный ОС pid чужого процесса
    не тронет. Возвращает число убитых."""
    psutil = _psutil()
    if psutil is None or shared_promptgen is None:
        return 0
    entries = _registry_read()
    if not entries:
        return 0
    killed = 0
    for entry in entries:
        try:
            proc = psutil.Process(int(entry["pid"]))
            same = abs(proc.create_time() - float(entry.get("create_time", 0))) < 2.0
            if same and "llama" in proc.name().lower():
                victims = proc.children(recursive=True) + [proc]
                for victim in victims:
                    try:
                        victim.kill()
                    except psutil.NoSuchProcess:
                        pass
                psutil.wait_procs(victims, timeout=5)
                killed += 1
                log.warning(
                    "Убит «осиротевший» llama-server pid=%s (задача %s) — остался с прошлого запуска",
                    entry["pid"], entry.get("job"),
                )
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError, KeyError):
            pass
        except Exception:
            log.exception("Ошибка при уборке процесса из реестра: %s", entry)
    _registry_write([])
    return killed


# ---------------------------------------------------------------------------
# Задача
# ---------------------------------------------------------------------------

class Job:
    ACTIVE_STATES = ("loading", "generating")

    def __init__(self):
        self.id = uuid.uuid4().hex[:12]
        self.state = "loading"       # loading | generating | done | error | cancelled
        self.error: Optional[str] = None
        self.tokens = 0
        self.thinking = False
        self.truncated = False
        self.warnings: list[dict] = []
        self.timings: Optional[dict] = None   # блок timings из последнего SSE-чанка llama-server
        self.marks: dict[str, float] = {"created": time.monotonic()}
        self.finished: Optional[float] = None
        self.cancel_event = threading.Event()
        self.proc: Optional[subprocess.Popen] = None
        self.thread: Optional[threading.Thread] = None
        self._raw = ""
        self._final: Optional[str] = None
        self._lock = threading.Lock()

    @property
    def created(self) -> float:
        return self.marks["created"]

    @property
    def active(self) -> bool:
        return self.state in self.ACTIVE_STATES

    # -- изменение состояния ------------------------------------------------

    def mark(self, name: str) -> None:
        self.marks.setdefault(name, time.monotonic())

    def set_state(self, state: str) -> None:
        with self._lock:
            if self.active:
                self.state = state

    def add_warning(self, warning: dict) -> None:
        with self._lock:
            self.warnings.append(warning)

    def add_content(self, piece: str) -> None:
        with self._lock:
            self._raw += piece
            self.tokens += 1
            # «думает», пока последний открывающий тег не закрыт
            low = self._raw.lower()
            self.thinking = low.rfind("<think>") > low.rfind("</think>")

    def note_reasoning(self) -> None:
        with self._lock:
            self.tokens += 1
            self.thinking = True

    def finish_ok(self, text: str) -> None:
        with self._lock:
            self._final = text
            self.state = "done"
            self.thinking = False
            self.finished = time.monotonic()

    def finish_error(self, message: str) -> None:
        with self._lock:
            self.error = message
            self.state = "error"
            self.finished = time.monotonic()

    def finish_cancelled(self) -> None:
        with self._lock:
            self.state = "cancelled"
            self.finished = time.monotonic()

    # -- чтение -------------------------------------------------------------

    def raw_text(self) -> str:
        with self._lock:
            return self._raw

    def snapshot(self) -> dict:
        with self._lock:
            text = self._final if self._final is not None else clean_output(self._raw)
            end = self.finished if self.finished is not None else time.monotonic()
            return {
                "job_id": self.id,
                "state": self.state,
                "text": text,
                "tokens": self.tokens,
                "thinking": self.thinking,
                "truncated": self.truncated,
                "warnings": list(self.warnings),
                "error": self.error,
                "elapsed": round(end - self.created, 1),
            }


def clean_output(raw: str) -> str:
    """Убирает «рассуждения» из ответа: закрытые <think>…</think> блоки, а
    также незакрытый <think>… (обрыв по лимиту токенов) -- всё после
    открывающего тега считается рассуждением, а не ответом."""
    text = _THINK_BLOCK_RE.sub("", raw)
    match = _THINK_OPEN_RE.search(text)
    if match:
        text = text[: match.start()]
    return text.strip()


def _with_log_hint(message: str) -> str:
    if shared_promptgen is None:
        return message
    return f"{message}\n\nПодробности — в логе: {shared_promptgen.log_file_path()}"


# ---------------------------------------------------------------------------
# Генератор
# ---------------------------------------------------------------------------

class PromptGenerator:
    def __init__(self):
        self._lock = threading.Lock()
        self._job: Optional[Job] = None
        self._last_stop: Optional[float] = None  # когда в последний раз гасили llama-server

    # -- настройки ------------------------------------------------------------

    @staticmethod
    def _settings() -> dict:
        if shared_promptgen is None:
            return {}
        return shared_promptgen.read_settings()

    # -- публичный интерфейс -------------------------------------------------

    def startup_sweep(self) -> None:
        """Вызывается при старте Imagine: добивает llama-server'ы от
        аварийно завершившегося прошлого запуска."""
        _ensure_file_logging()
        try:
            killed = sweep_stale()
            if killed:
                log.warning("При старте Imagine убито «осиротевших» llama-server: %d", killed)
        except Exception:
            log.exception("Ошибка уборки осиротевших процессов при старте")

    def status(self) -> dict:
        available = shared_promptgen is not None
        cfg = self._settings()
        configured = available and shared_promptgen.is_configured(cfg)
        problem = shared_promptgen.validate(cfg) if configured else None
        with self._lock:
            job = self._job
        return {
            "available": available,
            "configured": configured,
            "problem": problem,
            "vision": bool(cfg.get("mmproj_path", "").strip()),
            "image_max_side": cfg.get("image_max_side", 0) or 1024,
            "job": job.snapshot() if job else None,
        }

    def get_job(self, job_id: str) -> Optional[dict]:
        with self._lock:
            job = self._job
        if job is None or job.id != job_id:
            return None
        return job.snapshot()

    def start(
        self,
        text: str,
        image: Optional[str] = None,
        pre_start: Optional[Callable[[], None]] = None,
    ) -> str:
        if shared_promptgen is None:
            raise PromptGenError("Генератор промптов доступен только внутри ComfyUI Studio.", 400)
        _ensure_file_logging()
        cfg = self._settings()
        problem = shared_promptgen.validate(cfg)
        if problem:
            log.warning("Запуск отклонён: %s", problem)
            raise PromptGenError(problem, 400)

        text = (text or "").strip()
        image = image or None
        if image:
            if not image.startswith("data:image/") or ";base64," not in image[:100]:
                raise PromptGenError("Изображение должно быть data:image/…;base64 URL.", 400)
            if len(image) > MAX_IMAGE_DATA_URL_LEN:
                raise PromptGenError("Изображение слишком большое.", 400)
            if not cfg.get("mmproj_path", "").strip():
                raise PromptGenError(
                    "Чтобы работать с изображениями, укажите файл mmproj в "
                    "настройках Studio (Генератор промптов).",
                    400,
                )
        if not text and not image:
            raise PromptGenError("Введите текст или прикрепите изображение.", 400)

        with self._lock:
            current = self._job
            if current is not None and current.active:
                thread = current.thread
                if thread is not None and not thread.is_alive():
                    # Страховка от «навсегда занято»: поток задачи умер, не
                    # успев выставить итоговое состояние (не должно
                    # случаться, но именно так выглядела бы зависшая
                    # генерация, лечившаяся только перезапуском Studio).
                    log.error("[%s] задача числилась активной, но её поток завершён — сбрасываю", current.id)
                    current.finish_error("Задача оборвалась неожиданно (см. лог).")
                else:
                    log.info("Запуск отклонён: уже работает задача %s (%s)", current.id, current.state)
                    raise PromptGenError("Генератор промптов уже работает.", 409)
            job = Job()
            self._job = job

        thread = threading.Thread(
            target=self._run,
            args=(job, cfg, text, image, pre_start),
            daemon=True,
            name=f"promptgen-{job.id}",
        )
        thread.start()
        job.thread = thread
        return job.id

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._job
        if job is None or job.id != job_id or not job.active:
            return False
        _jlog(job, logging.INFO, "запрошена отмена")
        job.cancel_event.set()
        _kill_tree(job.proc)  # прерывает и ожидание загрузки, и стриминг
        return True

    def shutdown(self) -> None:
        with self._lock:
            job = self._job
        if job is not None and job.active:
            _jlog(job, logging.INFO, "остановка Imagine — прерываю задачу")
            job.cancel_event.set()
        if job is not None:
            _kill_tree(job.proc)

    # -- рабочий поток --------------------------------------------------------

    def _run(self, job: Job, cfg: dict, text: str, image: Optional[str], pre_start) -> None:
        proc = None
        log_fh = None
        llama_log = ""
        try:
            _jlog(
                job, logging.INFO,
                "старт: текст %d симв., изображение: %s",
                len(text), f"{len(image) * 3 // 4 // 1024} КиБ" if image else "нет",
            )
            _jlog(
                job, logging.INFO,
                "настройки: llama_dir=%s model=%s (%d МиБ) mmproj=%s ctx=%s ngl=%r extra_args=%r "
                "free_comfy=%s image_max_side=%s",
                cfg["llama_dir"], cfg["model_path"], _file_mb(cfg["model_path"]),
                cfg["mmproj_path"] or "—", cfg["ctx_size"], cfg["gpu_layers"], cfg["extra_args"],
                cfg["free_comfy_mode"], cfg["image_max_side"],
            )
            swept = sweep_stale()
            if swept:
                _jlog(job, logging.WARNING, "перед запуском убито осиротевших llama-server: %d", swept)

            self._prepare_vram(job, cfg, pre_start)
            if job.cancel_event.is_set():
                raise _Cancelled()

            port = _free_port()
            cmd = build_command(cfg, port)
            llama_log = _llama_log_path(job.id)
            cache_dir = _compute_cache_dir()
            cache_before = _dir_size_mb(cache_dir)
            if cache_before is not None:
                _jlog(job, logging.INFO, "кэш CUDA-ядер драйвера NVIDIA (%s): %.0f МиБ", cache_dir, cache_before)
            job.mark("spawned")
            proc, log_fh = _spawn(cmd, cfg["llama_dir"].strip(), build_env(cfg), llama_log)
            job.proc = proc
            _register_process(proc, job.id)
            _jlog(
                job, logging.INFO, "llama-server запущен: pid=%s порт=%s\n  лог: %s\n  команда: %s",
                proc.pid, port, llama_log, cmd,
            )
            if job.cancel_event.is_set():  # cancel() мог прийти до присвоения job.proc
                raise _Cancelled()

            self._wait_ready(job, proc, llama_log, port)
            job.mark("ready")
            _jlog(job, logging.INFO, "модель загружена за %.1f с", job.marks["ready"] - job.marks["spawned"])
            cache_after = _dir_size_mb(cache_dir)
            if cache_before is not None and cache_after is not None:
                grown = cache_after - cache_before
                if grown >= 5:
                    _jlog(
                        job, logging.INFO,
                        "кэш CUDA-ядер вырос на %.0f МиБ за время загрузки — на этом запуске драйвер "
                        "компилировал CUDA-ядра (JIT из PTX); при повторных запусках это должно быть быстрее",
                        grown,
                    )
                else:
                    _jlog(job, logging.INFO, "кэш CUDA-ядер за время загрузки не изменился (%+.1f МиБ) — JIT-компиляции не было", grown)
            self._inspect_llama_log(job, cfg, llama_log)
            _jlog(job, logging.INFO, "после загрузки: %s", _system_snapshot())
            job.set_state("generating")

            prompt = shared_promptgen.compose_prompt(cfg["prefix"], text, has_image=bool(image))
            _jlog(job, logging.DEBUG, "запрос к модели: %d симв. (префикс + текст)", len(prompt))
            self._stream_completion(job, port, prompt, image, proc, llama_log)
            job.mark("generated")

            result = clean_output(job.raw_text())
            if not result:
                hint = (
                    " Модель, похоже, ушла в «рассуждения» и не выдала ответ — "
                    "отключите thinking (например, «Доп. аргументы»: --reasoning off)."
                    if job.tokens else ""
                )
                raise PromptGenError("Модель вернула пустой ответ." + hint)
            self._log_stats(job, len(result))

            # Сначала выключаем модель (освобождаем VRAM), потом сообщаем
            # «готово».
            report = _stop_process(proc, log_fh)
            proc = log_fh = None
            _jlog(job, logging.INFO, "llama-server остановлен: %s", report)
            self._last_stop = time.monotonic()
            _jlog(job, logging.INFO, "готово за %.1f с", time.monotonic() - job.created)
            job.finish_ok(result)
        except _Cancelled:
            _jlog(job, logging.INFO, "отменено")
            job.finish_cancelled()
        except PromptGenError as exc:
            if job.cancel_event.is_set():
                _jlog(job, logging.INFO, "отменено")
                job.finish_cancelled()
            else:
                _jlog(job, logging.WARNING, "ошибка: %s", exc)
                job.finish_error(_with_log_hint(str(exc)))
        except Exception as exc:
            if job.cancel_event.is_set():
                _jlog(job, logging.INFO, "отменено")
                job.finish_cancelled()
            else:
                _jlog(job, logging.ERROR, "внутренняя ошибка", exc_info=True)
                job.finish_error(_with_log_hint(f"Внутренняя ошибка: {exc}"))
        finally:
            if proc is not None:
                report = _stop_process(proc, log_fh)
                _jlog(job, logging.INFO, "llama-server остановлен (аварийно/по отмене): %s", report)
                self._last_stop = time.monotonic()
            elif log_fh is not None:
                log_fh.close()
            started_pid = job.proc.pid if job.proc is not None else None
            if started_pid is not None:
                _unregister_process(started_pid)
                try:
                    # драйвер освобождает память не мгновенно -- даём мгновение,
                    # чтобы в логе было видно реальное состояние VRAM
                    time.sleep(0.5)
                    _jlog(job, logging.INFO, "после остановки: %s", _system_snapshot())
                except Exception:
                    pass

    # -- этапы ---------------------------------------------------------------

    def _wait_vram(self, job: Job, need_mb: Optional[int], max_s: float) -> Optional[list[dict]]:
        """Ждёт, пока освободится/стабилизируется видеопамять: выходит, когда
        свободно ≥ need_mb или два подряд замера почти не отличаются (память
        перестала освобождаться), либо по таймауту. None -- GPU не измеряется."""
        deadline = time.monotonic() + max_s
        previous: Optional[int] = None
        gpus = gpu_memory()
        while gpus is not None and time.monotonic() < deadline:
            free = _max_free_mb(gpus)
            if need_mb is not None and free is not None and free >= need_mb:
                return gpus
            if previous is not None and free is not None and abs(free - previous) < 32:
                return gpus
            previous = free
            if job.cancel_event.wait(0.5):
                raise _Cancelled()
            gpus = gpu_memory()
        return gpus

    def _prepare_vram(self, job: Job, cfg: dict, pre_start) -> None:
        """Готовит видеопамять к запуску: ждёт, пока драйвер освободит память
        после предыдущей остановки llama-server (если она была недавно), и
        по режиму free_comfy_mode решает, выгружать ли модели ComfyUI --
        видеопамять, занятая ComfyUI, заставляет llama-server (--fit)
        молча оставить часть слоёв на CPU, и генерация становится в разы
        медленнее."""
        mode = cfg.get("free_comfy_mode", "auto")
        need = estimate_vram_mb(cfg)
        gpus = gpu_memory()

        if gpus is not None and self._last_stop is not None and time.monotonic() - self._last_stop < 30:
            _jlog(
                job, logging.INFO,
                "предыдущая модель остановлена %.0f с назад — жду, пока освободится VRAM",
                time.monotonic() - self._last_stop,
            )
            gpus = self._wait_vram(job, None, 6.0) or gpus

        _jlog(job, logging.INFO, "перед запуском: %s; нужно ≈ %d МиБ", _system_snapshot(gpus), need)
        free = _max_free_mb(gpus)

        if mode == "never":
            _jlog(job, logging.INFO, "выгрузка моделей ComfyUI отключена настройкой")
            return
        if mode == "auto":
            if gpus is None:
                _jlog(job, logging.INFO, "auto: VRAM не измеряется — ComfyUI не трогаю")
                return
            if free is not None and free >= need:
                _jlog(job, logging.INFO, "auto: свободно %d МиБ ≥ нужно ≈%d — выгрузка ComfyUI не требуется", free, need)
                return
            _jlog(job, logging.INFO, "auto: свободно %s МиБ < нужно ≈%d — выгружаю модели ComfyUI", free, need)
        else:
            _jlog(job, logging.INFO, "выгружаю модели ComfyUI (режим «всегда»)")

        if pre_start is None:
            return
        try:
            pre_start()
        except Exception:
            _jlog(job, logging.WARNING, "не удалось выгрузить модели ComfyUI", exc_info=True)
            return
        if gpus is not None:
            if job.cancel_event.wait(1.0):  # ComfyUI обрабатывает /free асинхронно
                raise _Cancelled()
            after = self._wait_vram(job, need, 10.0)
            _jlog(job, logging.INFO, "после выгрузки ComfyUI: %s", _fmt_gpus(after))

    def _wait_ready(self, job: Job, proc: subprocess.Popen, log_path: str, port: int) -> None:
        url = f"http://127.0.0.1:{port}/health"
        started = time.monotonic()
        deadline = started + LOAD_TIMEOUT_S
        next_report = started + _FIRST_REPORT_S
        probe = _LoadProbe(proc.pid)
        while True:
            if job.cancel_event.is_set():
                raise _Cancelled()
            if proc.poll() is not None:
                # даём файловой системе секунду сбросить остаток вывода
                time.sleep(0.3)
                raise PromptGenError(_exit_message(proc, log_path))
            try:
                with _opener.open(url, timeout=2) as resp:
                    if resp.status == 200:
                        return
            except urllib.error.HTTPError:
                pass  # 503 -- модель ещё загружается
            except (urllib.error.URLError, OSError):
                pass  # порт ещё не слушается
            now = time.monotonic()
            if now > deadline:
                raise PromptGenError(
                    f"Модель не загрузилась за {LOAD_TIMEOUT_S // 60} мин — "
                    "проверьте размер модели и доступную память."
                )
            if now > next_report:
                _jlog(job, logging.INFO, "…модель загружается (%d с): %s", now - started, probe.describe())
                next_report = now + _REPORT_EVERY_S
            time.sleep(0.5)

    def _inspect_llama_log(self, job: Job, cfg: dict, log_path: str) -> None:
        """Достаёт из вывода llama-server то, что объясняет медленную работу:
        сколько слоёв реально на GPU и на чём работает кодировщик изображений."""
        text = _read_log_text(log_path)
        all_lines = text.splitlines()
        lines = [ln.strip()[:220] for ln in all_lines if _KEY_LINE_RE.search(ln) and not ln.startswith("# cmd")]
        _jlog(job, logging.INFO, "вывод llama-server при загрузке: %d строк", len(all_lines))
        if lines:
            _jlog(job, logging.INFO, "ключевые строки llama-server:\n  %s", "\n  ".join(lines[:40]))
        gaps = analyze_load_timeline(text)
        if gaps:
            _jlog(job, logging.INFO, "самые долгие паузы в выводе llama-server при загрузке:\n  %s", "\n  ".join(gaps))

        offloads = _OFFLOAD_RE.findall(text)
        if offloads:
            loaded, total = int(offloads[-1][0]), int(offloads[-1][1])
            if loaded < total:
                _jlog(
                    job, logging.WARNING,
                    "модель на GPU лишь частично: %d из %d слоёв — остальное считается на CPU "
                    "(не хватило видеопамяти) → генерация будет медленной", loaded, total,
                )
                job.add_warning({"code": "gpu_partial", "loaded": loaded, "total": total})
            else:
                _jlog(job, logging.INFO, "слои на GPU: %d из %d", loaded, total)
        else:
            _jlog(
                job, logging.INFO,
                "в выводе llama-server не найдена строка про слои на GPU (формат вывода этой сборки может "
                "отличаться) — смотрите файл лога llama-server",
            )

        if cfg.get("mmproj_path", "").strip():
            backends = _CLIP_BACKEND_RE.findall(text)
            if backends:
                backend = backends[-1]
                _jlog(job, logging.INFO, "кодировщик изображений (mmproj): %s", backend)
                if "cpu" in backend.lower():
                    _jlog(job, logging.WARNING, "кодировщик изображений работает на CPU → картинки будут обрабатываться медленно")
                    job.add_warning({"code": "clip_cpu"})

    def _log_stats(self, job: Job, result_len: int) -> None:
        m = job.marks
        load = m["ready"] - m["spawned"]
        ttft = (m["first_token"] - m["ready"]) if "first_token" in m else None
        gen = (m["generated"] - m["first_token"]) if "first_token" in m else None
        parts = [f"загрузка {load:.1f} с"]
        if ttft is not None:
            parts.append(f"до первого токена {ttft:.1f} с (обработка запроса и картинки)")
        if gen is not None:
            rate = f", {job.tokens / gen:.1f} ток/с" if gen > 0 else ""
            parts.append(f"генерация {gen:.1f} с ({job.tokens} ток.{rate})")
        _jlog(job, logging.INFO, "тайминги: %s; ответ %d симв.", "; ".join(parts), result_len)
        t = job.timings
        if t:
            _jlog(
                job, logging.INFO,
                "llama-server timings: запрос %s ток. за %.0f мс (%.1f ток/с, из кэша %s); "
                "ответ %s ток. за %.0f мс (%.1f ток/с)",
                t.get("prompt_n"), t.get("prompt_ms") or 0, t.get("prompt_per_second") or 0, t.get("cache_n", 0),
                t.get("predicted_n"), t.get("predicted_ms") or 0, t.get("predicted_per_second") or 0,
            )
        if job.truncated:
            _jlog(job, logging.WARNING, "ответ обрезан лимитом max_tokens=%d", MAX_TOKENS)

    def _stream_completion(
        self, job: Job, port: int, prompt: str, image: Optional[str], proc, log_path: str
    ) -> None:
        if image:
            content = [
                {"type": "image_url", "image_url": {"url": image}},
                {"type": "text", "text": prompt},
            ]
        else:
            content = prompt
        body = {
            "model": "local",
            "messages": [{"role": "user", "content": content}],
            "max_tokens": MAX_TOKENS,
            "stream": True,
            # Qwen3 и похожие: не «думать» перед ответом. Модели без
            # такого параметра шаблона его игнорируют.
            "chat_template_kwargs": {"enable_thinking": False},
        }
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            method="POST",
        )
        complete = False  # получен ли штатный конец потока ([DONE] / finish_reason)
        try:
            with _opener.open(req, timeout=STREAM_TIMEOUT_S) as resp:
                for raw_line in resp:
                    if job.cancel_event.is_set():
                        raise _Cancelled()
                    line = raw_line.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        complete = True
                        break
                    try:
                        obj = json.loads(payload)
                    except ValueError:
                        continue
                    if isinstance(obj, dict) and obj.get("error"):
                        raise PromptGenError(f"llama-server: {_error_text(obj['error'])}", 502)
                    if isinstance(obj.get("timings"), dict):
                        job.timings = obj["timings"]
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        job.mark("first_token")
                        job.add_content(delta["content"])
                    elif delta.get("reasoning_content"):
                        job.mark("first_token")
                        job.note_reasoning()
                    if choice.get("finish_reason"):
                        complete = True
                        if choice["finish_reason"] == "length":
                            job.truncated = True
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = _error_text(json.loads(exc.read().decode("utf-8", "replace")).get("error"))
            except Exception:
                pass
            raise PromptGenError(
                f"llama-server вернул ошибку {exc.code}" + (f": {detail}" if detail else "") + ".",
                502,
            ) from exc
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as exc:
            if job.cancel_event.is_set():
                raise _Cancelled() from exc
            raise PromptGenError(
                self._lost_connection_message(proc, log_path, f"{exc}"), 502
            ) from exc

        # Поток мог закончиться "тихо": отмена (cancel/shutdown убили
        # процесс) либо llama-server упал на середине (например, нехватка
        # VRAM) -- в обоих случаях обрубок текста НЕ должен выдаваться за
        # готовый результат.
        if job.cancel_event.is_set():
            raise _Cancelled()
        if not complete:
            raise PromptGenError(self._lost_connection_message(proc, log_path, "поток ответа оборвался"), 502)

    @staticmethod
    def _lost_connection_message(proc, log_path: str, reason: str) -> str:
        # даём процессу мгновение завершиться, чтобы увидеть код выхода и
        # последние строки его вывода (там обычно и написана причина)
        for _ in range(10):
            if proc.poll() is not None:
                break
            time.sleep(0.1)
        if proc.poll() is not None:
            time.sleep(0.2)  # дочитать вывод
            return _exit_message(proc, log_path, "во время генерации")
        return f"Соединение с llama-server потеряно: {reason}"


def _file_mb(path: str) -> int:
    try:
        return os.path.getsize(path) // _MIB
    except (OSError, TypeError):
        return 0


def _error_text(err) -> str:
    if isinstance(err, dict):
        return str(err.get("message") or err)
    return str(err)


generator = PromptGenerator()
atexit.register(generator.shutdown)
