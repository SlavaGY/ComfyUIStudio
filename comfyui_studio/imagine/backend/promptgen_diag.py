"""
promptgen_diag.py
Логирование и диагностика генератора промптов: логгер и файл
promptgen.log, замеры VRAM/RAM/процессов, оценка видеопамяти, разбор
вывода llama-server (слои на GPU, паузы при загрузке), замеры процесса
во время загрузки модели, чтение его логов.

Вынесено из promptgen.py на этапе R10 плана рефакторинга без изменения
кода функций. Всё здесь best-effort: сбой диагностики не должен ломать
генерацию, поэтому широкие ``except Exception`` в этом файле --
сознательная граница подсистемы (см. docs/R10_APPLY.md).
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import re
import time
from typing import TYPE_CHECKING, Optional

from comfyui_studio.imagine.backend.promptgen_base import MAX_TOKENS, _MIB
from comfyui_studio.imagine.backend.promptgen_deps import shared_promptgen

if TYPE_CHECKING:  # только для аннотаций: promptgen_job сам ничего отсюда не берёт
    from comfyui_studio.imagine.backend.promptgen_job import Job

log = logging.getLogger("imagine.promptgen")

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
_OFFLOAD_RE = re.compile(r"offloaded\s+(\d+)\s*/\s*(\d+)\s+layers\s+to\s+GPU", re.IGNORECASE)
_CLIP_BACKEND_RE = re.compile(r"CLIP using (\S+) backend", re.IGNORECASE)


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


def _with_log_hint(message: str) -> str:
    if shared_promptgen is None:
        return message
    return f"{message}\n\nПодробности — в логе: {shared_promptgen.log_file_path()}"


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
        # %APPDATA%\NVIDIA\ComputeCache — папка ДРАЙВЕРА NVIDIA, а не данные
        # приложения, поэтому намеренно не через app_paths (R2).
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
# Файлы логов llama-server и разбор их содержимого
# ---------------------------------------------------------------------------

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


def inspect_llama_log(job: "Job", cfg: dict, log_path: str) -> None:
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


def log_job_stats(job: "Job", result_len: int) -> None:
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
