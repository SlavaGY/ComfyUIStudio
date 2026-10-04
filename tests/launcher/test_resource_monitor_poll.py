"""
Характеризационные тесты ResourceMonitor._poll / feed_log_line /
_compute_eta_seconds (этап R0 плана рефакторинга, раунд 2) -- фиксируют
ТЕКУЩЕЕ поведение перед разбиением ResourceMonitor на части (этап R6).
После R6 ожидания не менялись; изменился только Harness: сеть опроса идёт
через spawn (синхронный в тестах). Асинхронность — tests/launcher/
test_background_polling.py.

Не дублирует tests/launcher/test_system_monitor_ws.py: тот проверяет
WebSocket-канал и его сосуществование с feed_log_line; здесь -- HTTP-
ветка _poll (очередь, \"готово за сессию\", смена задания, сброс при
остановке ComfyUI) и набор ключей словаря stats_updated (контракт для
R6, см. docs/baseline_R0.md).

Железо и сеть подменены:
  * psutil / pynvml -- фейками, чтобы тест не зависел от машины и не
    дёргал реальный NVML (на машине с NVIDIA ResourceMonitor иначе
    инициализировал бы его в __init__);
  * ComfyAPIClient -- фейком с заранее заданными ответами;
  * _ensure_ws_client -- заглушкой (WS здесь не проверяется).
"""

import types

import pytest

from comfyui_studio.launcher.core import system_monitor as sm
from comfyui_studio.launcher.core.comfy_api import QueueState
from comfyui_studio.launcher.core.system_monitor import (
    ResourceMonitor,
    format_eta_seconds,
    format_stats_tooltip,
    level_color,
)

_GB = 1024 ** 3


class FakePsutil:
    @staticmethod
    def cpu_percent(interval=None):
        return 12.5

    @staticmethod
    def virtual_memory():
        return types.SimpleNamespace(percent=50.0, used=8 * _GB, total=16 * _GB)


class FakeNvml:
    NVML_TEMPERATURE_GPU = 0

    @staticmethod
    def nvmlInit():
        pass

    @staticmethod
    def nvmlDeviceGetHandleByIndex(i):
        return object()

    @staticmethod
    def nvmlDeviceGetUtilizationRates(h):
        return types.SimpleNamespace(gpu=37)

    @staticmethod
    def nvmlDeviceGetMemoryInfo(h):
        return types.SimpleNamespace(used=3 * _GB, total=12 * _GB)

    @staticmethod
    def nvmlDeviceGetTemperature(h, kind):
        return 61

    @staticmethod
    def nvmlDeviceGetName(h):
        return b"Fake GPU"


class FakeAPI:
    """Заранее заданные ответы вместо HTTP. None -- \"опрос не удался\"."""

    def __init__(self):
        self.queue = None
        self.history_ids = None

    def get_queue(self, port=None, timeout=None):
        return self.queue

    def get_history_ids(self, port=None, timeout=None):
        return self.history_ids


def _queue(running_ids=(), pending=0, step_totals=None):
    running_ids = set(running_ids)
    return QueueState(
        running=len(running_ids),
        pending=pending,
        running_ids=running_ids,
        step_totals=dict(step_totals or {}),
    )


class Harness:
    def __init__(self, monkeypatch, nvml=False):
        monkeypatch.setattr(sm, "psutil", FakePsutil)
        monkeypatch.setattr(sm, "pynvml", FakeNvml if nvml else None)
        self.port = None
        # R6: сетевая часть опроса в проде идёт в рабочем потоке; тесты
        # этого файла проверяют ЛОГИКУ, поэтому запуск синхронный.
        self.monitor = ResourceMonitor(lambda: self.port, spawn=lambda fn: fn())
        self.monitor._api = FakeAPI()
        self.monitor._ensure_ws_client = lambda port: None
        self.received = []
        self.monitor.stats_updated.connect(lambda s: self.received.append(s))

    @property
    def api(self):
        return self.monitor._api

    def poll(self):
        self.monitor._poll()
        return self.received[-1]


@pytest.fixture
def h(monkeypatch):
    return Harness(monkeypatch)


# -- ключи словаря stats_updated (контракт для R6) -------------------------


def test_poll_without_comfy_has_only_hardware_keys(h):
    stats = h.poll()
    assert set(stats) == {
        "cpu_percent", "ram_percent", "ram_used_gb", "ram_total_gb", "gpu_available",
    }
    assert stats["gpu_available"] is False
    assert stats["cpu_percent"] == 12.5
    assert stats["ram_percent"] == 50.0
    assert stats["ram_used_gb"] == 8.0
    assert stats["ram_total_gb"] == 16.0


def test_poll_emits_exactly_once_per_call(h):
    h.poll()
    h.poll()
    assert len(h.received) == 2


def test_poll_with_nvml_adds_gpu_keys(monkeypatch):
    stats = Harness(monkeypatch, nvml=True).poll()
    assert stats["gpu_available"] is True
    assert stats["gpu_name"] == "Fake GPU"  # bytes -> str
    assert stats["gpu_util"] == 37
    assert stats["gpu_temp"] == 61
    assert stats["gpu_mem_used_gb"] == 3.0
    assert stats["gpu_mem_total_gb"] == 12.0


def test_poll_without_psutil_has_no_cpu_ram_keys(monkeypatch):
    monkeypatch.setattr(sm, "psutil", None)
    monkeypatch.setattr(sm, "pynvml", None)
    m = ResourceMonitor(lambda: None)
    got = []
    m.stats_updated.connect(lambda s: got.append(s))
    m._poll()
    assert got[-1] == {"gpu_available": False}


def test_poll_with_running_comfy_adds_queue_keys(h):
    h.port = 8188
    h.api.queue = _queue()
    h.api.history_ids = set()
    stats = h.poll()
    assert {
        "queue_running", "queue_pending", "queue_completed_session", "queue_eta_seconds",
    } <= set(stats)
    assert stats["queue_running"] == 0
    assert stats["queue_pending"] == 0
    assert stats["queue_completed_session"] == 0
    assert stats["queue_eta_seconds"] == 0.0  # в очереди ничего нет -> 0.0, не None


def test_poll_http_failure_keeps_port_known_but_omits_queue_keys(h):
    h.port = 8188
    h.api.queue = None  # ComfyUI запущен, но /queue не ответил
    stats = h.poll()
    assert "queue_pending" not in stats
    assert "queue_eta_seconds" not in stats


# -- \"готово за сессию\" ----------------------------------------------------


def test_session_counter_ignores_history_that_existed_before_session(h):
    h.port = 8188
    h.api.queue = _queue()
    h.api.history_ids = {"old1", "old2"}
    assert h.poll()["queue_completed_session"] == 0


def test_session_counter_counts_new_history_ids_once(h):
    h.port = 8188
    h.api.queue = _queue()
    h.api.history_ids = {"old"}
    h.poll()

    h.api.history_ids = {"old", "new1"}
    assert h.poll()["queue_completed_session"] == 1
    assert h.poll()["queue_completed_session"] == 1  # повторный опрос не удваивает

    h.api.history_ids = {"old", "new1", "new2"}
    assert h.poll()["queue_completed_session"] == 2


def test_session_counter_skips_ids_still_in_running(h):
    """/queue и /history -- два неатомарных запроса: id уже мог попасть в
    историю, но ещё числиться в running -- такой id не считается готовым,
    пока не выйдет из running_ids."""
    h.port = 8188
    h.api.queue = _queue()
    h.api.history_ids = {"old"}
    h.poll()

    h.api.queue = _queue(running_ids=["x"])
    h.api.history_ids = {"old", "x"}
    assert h.poll()["queue_completed_session"] == 0

    h.api.queue = _queue()
    assert h.poll()["queue_completed_session"] == 1


def test_session_counter_still_reported_when_history_unavailable(h):
    h.port = 8188
    h.api.queue = _queue()
    h.api.history_ids = None
    assert h.poll()["queue_completed_session"] == 0


def test_http_failure_does_not_reset_session(h):
    h.port = 8188
    h.api.queue = _queue()
    h.api.history_ids = {"old"}
    h.poll()
    h.api.history_ids = {"old", "n"}
    h.poll()

    h.api.queue = None  # временный сбой опроса
    h.poll()

    h.api.queue = _queue()
    assert h.poll()["queue_completed_session"] == 1


# -- смена задания и сброс прогресса ---------------------------------------


def test_job_switch_clears_stale_progress(h):
    h.port = 8188
    h.api.history_ids = set()
    h.monitor._current_progress = {"done": 39, "total": 39}
    h.monitor._progress_for_id = "prev"

    h.api.queue = _queue(running_ids=["next"], step_totals={"next": 20})
    stats = h.poll()

    assert h.monitor._current_progress is None
    assert h.monitor._progress_for_id == "next"
    # скорость шага ещё не замерена -> \"не знаем\", а не ложные \"< 1 с\"
    assert stats["queue_eta_seconds"] is None


def test_same_job_keeps_progress_and_computes_eta(h):
    h.port = 8188
    h.api.history_ids = set()
    h.monitor._current_progress = {"done": 5, "total": 10}
    h.monitor._progress_for_id = "a"
    h.monitor._avg_sec_per_step = 2.0

    h.api.queue = _queue(running_ids=["a"], step_totals={"a": 10})
    stats = h.poll()

    assert h.monitor._current_progress == {"done": 5, "total": 10}
    assert stats["queue_eta_seconds"] == 10.0  # (10 - 5) * 2.0


def test_stall_counter_grows_while_running_without_progress(h):
    h.port = 8188
    h.api.history_ids = set()
    h.api.queue = _queue(running_ids=["a"], step_totals={"a": 10})
    h.poll()
    h.poll()
    assert h.monitor._stall_polls == 2


def test_stall_counter_resets_when_queue_goes_idle(h):
    h.port = 8188
    h.api.history_ids = set()
    h.api.queue = _queue(running_ids=["a"], step_totals={"a": 10})
    h.poll()
    h.api.queue = _queue()
    h.poll()
    assert h.monitor._stall_polls == 0


def test_poll_after_comfy_stops_resets_all_session_state(h):
    h.port = 8188
    h.api.queue = _queue(running_ids=["a"], step_totals={"a": 10})
    h.api.history_ids = {"old"}
    h.poll()
    h.monitor._current_progress = {"done": 1, "total": 10}
    h.monitor._avg_sec_per_step = 1.0
    h.monitor._session_done_ids = {"zzz"}

    h.port = None  # ComfyUI остановили
    stats = h.poll()

    assert "queue_pending" not in stats
    assert h.monitor._session_seen_history_ids is None
    assert h.monitor._session_done_ids == set()
    assert h.monitor._current_progress is None
    assert h.monitor._progress_for_id is None
    assert h.monitor._avg_sec_per_step is None
    assert h.monitor._stall_polls == 0
    assert h.monitor._logged_switch_for_ids == set()
    assert h.monitor._logged_progress_for_ids == set()


# -- _compute_eta_seconds --------------------------------------------------


def test_eta_empty_queue_is_zero(h):
    assert h.monitor._compute_eta_seconds({}, set()) == 0.0


def test_eta_unknown_step_total_without_any_reference_is_none(h):
    h.monitor._avg_sec_per_step = 1.0
    assert h.monitor._compute_eta_seconds({"a": 0}, {"a"}) is None


def test_eta_unknown_total_uses_average_of_known_ones(h):
    h.monitor._avg_sec_per_step = 1.0
    # у \"b\" объём неизвестен -> подставляется средний по остальным (20)
    eta = h.monitor._compute_eta_seconds({"a": 20, "b": 0}, set())
    assert eta == 40.0


def test_eta_none_until_step_speed_is_measured(h):
    assert h.monitor._avg_sec_per_step is None
    assert h.monitor._compute_eta_seconds({"a": 10}, set()) is None


def test_eta_uses_tqdm_total_when_larger_than_graph_heuristic(h):
    h.monitor._avg_sec_per_step = 1.0
    h.monitor._current_progress = {"done": 0, "total": 30}
    assert h.monitor._compute_eta_seconds({"a": 10}, {"a"}) == 30.0


def test_eta_progress_attributed_only_when_exactly_one_running(h):
    h.monitor._avg_sec_per_step = 1.0
    h.monitor._current_progress = {"done": 9, "total": 10}
    # два running -> чужой прогресс не относим ни к одному
    assert h.monitor._compute_eta_seconds({"a": 10, "b": 10}, {"a", "b"}) == 20.0


# -- feed_log_line (разбор tqdm) -------------------------------------------


def test_feed_log_line_parses_it_per_second(h):
    h.monitor.feed_log_line("74%|███████▍  | 26/35 [00:24<00:07, 1.25it/s]")
    assert h.monitor._current_progress == {"done": 26, "total": 35}
    assert h.monitor._avg_sec_per_step == pytest.approx(0.8)


def test_feed_log_line_parses_seconds_per_iteration(h):
    h.monitor.feed_log_line("10%|█  | 2/20 [00:06<00:54, 3.00s/it]")
    assert h.monitor._current_progress == {"done": 2, "total": 20}
    assert h.monitor._avg_sec_per_step == 3.0


def test_feed_log_line_strips_ansi_escape_codes(h):
    h.monitor.feed_log_line("\x1b[32m 5/10 [00:01<00:01, 2.00it/s]\x1b[0m")
    assert h.monitor._current_progress == {"done": 5, "total": 10}


def test_feed_log_line_ignores_unrelated_and_degenerate_lines(h):
    h.monitor.feed_log_line("Prompt executed in 3.2 seconds")
    h.monitor.feed_log_line("0/0 [00:00<00:00, 1.00it/s]")   # total == 0
    h.monitor.feed_log_line("1/10 [00:00<00:09, 0.00it/s]")  # rate == 0
    assert h.monitor._current_progress is None
    assert h.monitor._avg_sec_per_step is None


# -- чистые форматтеры -----------------------------------------------------


@pytest.mark.parametrize(
    "seconds, expected",
    [
        (None, "оценка..."),
        (0.0, "< 1 с"),
        (0.4, "< 1 с"),
        (45, "~45 с"),
        (150, "~2 мин 30 с"),
        (60, "~1 мин 0 с"),
    ],
)
def test_format_eta_seconds(seconds, expected):
    assert format_eta_seconds(seconds) == expected


def test_format_eta_seconds_uses_translator():
    assert format_eta_seconds(None, tr=lambda t: t.upper()) == "ОЦЕНКА..."


def test_format_stats_tooltip_without_queue_says_comfy_not_running():
    text = format_stats_tooltip({"cpu_percent": 10.0})
    assert text.splitlines() == ["CPU 10%", "ComfyUI не запущен"]


def test_format_stats_tooltip_full_queue_line():
    stats = {
        "queue_running": 1, "queue_pending": 2,
        "queue_completed_session": 3, "queue_eta_seconds": 45,
    }
    assert format_stats_tooltip(stats) == "Очередь 1/2 · Готово 3 · ETA ~45 с"


def test_format_stats_tooltip_hides_eta_for_idle_queue():
    stats = {
        "queue_running": 0, "queue_pending": 0,
        "queue_completed_session": 3, "queue_eta_seconds": 0.0,
    }
    assert "ETA" not in format_stats_tooltip(stats)


def test_format_stats_tooltip_truncates_to_120_chars():
    stats = {"gpu_available": True, "gpu_util": 1, "gpu_temp": 1,
             "gpu_mem_used_gb": 1.0, "gpu_mem_total_gb": 1.0,
             "cpu_percent": 1.0, "ram_percent": 1.0, "ram_used_gb": 1.0, "ram_total_gb": 1.0,
             "queue_running": 100000, "queue_pending": 100000,
             "queue_completed_session": 100000, "queue_eta_seconds": 99999}
    text = format_stats_tooltip(stats)
    assert len(text) <= 120
    if len(text) == 120:
        assert text.endswith("...")


@pytest.mark.parametrize(
    "value, expected",
    [(None, "#5b6472"), (10, "#3fae4f"), (60, "#d98c2b"), (85, "#d9534f")],
)
def test_level_color(value, expected):
    assert level_color(value) == expected
