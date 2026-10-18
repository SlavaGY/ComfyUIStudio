"""Общее состояние и сигнал ResourceMonitor.

Атрибуты инициализируются в ResourceMonitor.__init__. Аппаратные метрики,
WebSocket и ETA используют это состояние; опрос очереди находится
в system_monitor."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from .background import BackgroundTask
from .comfy_api import ComfyAPIClient


class ResourceMonitorBase(QObject):
    """Сигнал stats_updated и состояние, доступное всем миксинам."""

    stats_updated = Signal(dict)

    # Аннотации общего состояния, чтобы mypy видел эти атрибуты в миксинах.
    _get_running_port: Callable[[], int | None]
    _fetch: BackgroundTask
    _last_queue_stats: dict
    _api: ComfyAPIClient
    _nvml_ok: bool
    _gpu_handle: Any
    _warned_psutil: bool
    _warned_nvml: bool
    _session_seen_history_ids: set | None
    _session_done_ids: set
    _current_progress: dict | None
    _progress_for_id: str | None
    _avg_sec_per_step: float | None
    _ws_client: Any
    _ws_port: int | None
    _ws_connected: bool
    _ws_progress_prev: tuple | None
    _logged_switch_for_ids: set
    _logged_progress_for_ids: set
    _stall_polls: int
    _logged_stall_warning_for_ids: set
    _timer: QTimer
