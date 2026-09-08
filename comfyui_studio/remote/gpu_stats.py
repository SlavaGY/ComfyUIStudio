"""
Автономный (без Qt) опрос GPU через pynvml -- для RemoteStatus (§1.1
дорожной карты, поля gpu_name/vram_used_mb/vram_total_mb).

launcher/core/system_monitor.py уже опрашивает GPU той же библиотекой
(nvidia-ml-py/pynvml), но делает это внутри ResourceMonitor(QObject) --
завязан на таймер и сигналы Qt. Remote-процесс сознательно БЕЗ Qt (см.
__init__.py пакета и §0.1 дорожной карты -- отдельный FastAPI/uvicorn-
процесс, а не окно), поэтому логика опроса продублирована здесь в чистом
виде (несколько вызовов pynvml без обёртки в QObject/таймер), а не
переиспользуется напрямую -- тянуть PySide6 в процесс Remote только
ради этой пары вызовов было бы значительно дороже, чем повторить сам
опрос. Опрашивается по требованию, при каждом GET /status, а не
сэмплируется в фоне (в отличие от ResourceMonitor) -- Remote не держит
собственного таймера мониторинга на этом этапе дорожной карты.

Как и в system_monitor.py: только NVIDIA (через NVML). Для AMD/Intel
или систем без pynvml/драйверов -- все три поля просто None, без
ошибки (см. то же ограничение в README, "Известные ограничения").
"""

from __future__ import annotations

from typing import Optional

try:
    import pynvml
except Exception:  # pragma: no cover - библиотека может быть не установлена вовсе
    pynvml = None

_initialized = False
_init_failed = False


def _ensure_init() -> None:
    global _initialized, _init_failed
    if _initialized or _init_failed or pynvml is None:
        return
    try:
        pynvml.nvmlInit()
        _initialized = True
    except Exception:
        # Нет NVIDIA-карты, нет драйверов, или pynvml не смог найти
        # nvml.dll/libnvidia-ml -- то же самое молчаливое отсутствие
        # метрик, что и в system_monitor.py, а не ошибка запуска Remote.
        _init_failed = True


def get_gpu_stats() -> tuple[Optional[str], Optional[int], Optional[int]]:
    """Возвращает (gpu_name, vram_used_mb, vram_total_mb) -- все три
    None, если pynvml недоступен, инициализация не удалась или запрос
    к первой видеокарте не удался по любой другой причине."""
    _ensure_init()
    if not _initialized:
        return None, None, None
    try:
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        name = pynvml.nvmlDeviceGetName(handle)
        if isinstance(name, bytes):
            name = name.decode("utf-8", errors="replace")
        used_mb = int(mem.used // (1024 * 1024))
        total_mb = int(mem.total // (1024 * 1024))
        return name, used_mb, total_mb
    except Exception:
        return None, None, None
