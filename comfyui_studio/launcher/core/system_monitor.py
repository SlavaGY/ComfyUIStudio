"""Монитор ресурсов: таймер, фоновый HTTP-опрос очереди и состояние сессии.

Аппаратные метрики, WebSocket и ETA вынесены в отдельные миксины.
ResourceMonitor и форматтеры остаются доступны по прежнему пути импорта.
"""

from PySide6.QtCore import QTimer

from .background import BackgroundTask
from .comfy_api import ComfyAPIClient
from .logging_setup import log
from .system_monitor_base import ResourceMonitorBase
from .system_monitor_eta import EtaMixin
from .system_monitor_formatters import (
    NEUTRAL_CHIP_COLOR,
    QUEUE_ACTIVE_COLOR,
    QUEUE_IDLE_COLOR,
    format_eta_seconds,
    format_stats_tooltip,
    level_color,
)
from .system_monitor_hardware import HardwareMixin
from .system_monitor_ws import WebSocketMixin

RESOURCE_POLL_INTERVAL_MS = 2000

__all__ = [
    "NEUTRAL_CHIP_COLOR",
    "QUEUE_ACTIVE_COLOR",
    "QUEUE_IDLE_COLOR",
    "RESOURCE_POLL_INTERVAL_MS",
    "ResourceMonitor",
    "format_eta_seconds",
    "format_stats_tooltip",
    "level_color",
]


class ResourceMonitor(HardwareMixin, WebSocketMixin, EtaMixin, ResourceMonitorBase):
    """Собирает метрики и публикует их через stats_updated(dict)."""

    def __init__(self, get_running_port_fn, parent=None, spawn=None):
        """spawn(fn) позволяет синхронный опрос в тестах; по умолчанию — поток."""
        super().__init__(parent)
        self._get_running_port = get_running_port_fn
        self._fetch = BackgroundTask(self, spawn=spawn)
        # Пока HTTP-запрос выполняется, показываем последний снимок очереди.
        self._last_queue_stats = {}
        # Порт решается снаружи: отсутствие процесса и ошибка HTTP различаются.
        self._api = ComfyAPIClient()
        self._nvml_ok = False
        self._gpu_handle = None
        self._warned_psutil = False
        self._warned_nvml = False
        # Первый успешный /history задаёт базу, не засчитывая старые задания.
        self._session_seen_history_ids = None
        self._session_done_ids = set()
        # stdout и WS обновляют один прогресс; смена задания сбрасывает его.
        self._current_progress = None
        self._progress_for_id = None
        self._avg_sec_per_step = None
        self._ws_client = None
        self._ws_port = None
        self._ws_connected = False
        self._ws_progress_prev = None
        self._logged_switch_for_ids = set()
        self._logged_progress_for_ids = set()
        self._stall_polls = 0
        self._logged_stall_warning_for_ids = set()
        self._init_hardware()
        self._timer = QTimer(self)
        self._timer.setInterval(RESOURCE_POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)

    def start(self):
        self._timer.start()
        self._poll()


    def stop(self):
        self._timer.stop()
        self._fetch.cancel()
        self._teardown_ws_client()


    def _fetch_queue_snapshot(self, port):
        """Получает (QueueState, history_ids) в рабочем потоке, не меняя состояние.

        При ошибке /queue возвращает (None, None); /history запрашивается
        только после успешного /queue."""
        queue_state = self._api.get_queue(port=port)
        if queue_state is None:
            return None, None
        return queue_state, self._api.get_history_ids(port=port)


    def _apply_queue_snapshot(self, stats, queue_state, history_ids):
        """В GUI-потоке дополняет stats состоянием очереди, счётчиком завершённых
        заданий, ETA и диагностикой отсутствующего прогресса."""
        running = queue_state.running
        pending = queue_state.pending
        running_ids = queue_state.running_ids
        step_totals = queue_state.step_totals
        stats["queue_running"] = running
        stats["queue_pending"] = pending

        if history_ids is not None:
            if self._session_seen_history_ids is None:
                # Первый успешный опрос за это включение ComfyUI --
                # запоминаем то, что уже есть в /history, как "не
                # наше", иначе в счётчик попало бы то, что было
                # сделано ДО запуска лаунчера/в прошлые сессии.
                self._session_seen_history_ids = set(history_ids)
            # "Готово за сессию" = то, чего не было в /history на
            # старте сессии, И чего СЕЙЧАС нет в running_ids по
            # данным /queue из ЭТОГО ЖЕ опроса. Второе условие --
            # защита от гонки: /queue и /history это два отдельных
            # HTTP-запроса, не единый снимок состояния, и
            # генерация может успеть попасть в /history в
            # промежутке между ними, оставаясь в это же мгновение
            # ещё "running" по /queue -- без этой проверки она на
            # секунду засчитывалась готовой, хотя прогресс-бар
            # всё ещё показывал её выполняющейся. Просто
            # отложится до следующего опроса (2 сек), когда она
            # уже точно пропадёт из running_ids.
            newly_done = (
                history_ids - self._session_seen_history_ids
            ) - running_ids
            self._session_done_ids |= newly_done
        stats["queue_completed_session"] = len(self._session_done_ids)

        # При смене задания сбрасываем его прогресс: пока загружается
        # новая модель, остаток шагов предыдущего задания не годится для ETA.
        current_single_id = (
            next(iter(running_ids)) if len(running_ids) == 1 else None
        )
        if current_single_id != self._progress_for_id:
            if current_single_id not in self._logged_switch_for_ids:
                log.info(
                    "ETA очереди: активное задание сменилось %s -> %s "
                    "(running_ids=%s), сбрасываю накопленный прогресс",
                    self._progress_for_id, current_single_id, running_ids,
                )
                if current_single_id is not None:
                    self._logged_switch_for_ids.add(current_single_id)
            self._current_progress = None
            self._progress_for_id = current_single_id

        # ETA -- см. _compute_eta_seconds/feed_log_line.
        stats["queue_eta_seconds"] = self._compute_eta_seconds(
            step_totals, running_ids
        )

        if running_ids and self._current_progress is None:
            # Скорость может сохраниться от предыдущего задания;
            # отсутствие прогресса проверяется для текущего задания.
            self._stall_polls += 1
            if (
                self._stall_polls >= 5
                and current_single_id is not None
                and current_single_id not in self._logged_stall_warning_for_ids
            ):
                # ~10 секунд (5 опросов по 2 сек) активной очереди,
                # а от feed_log_line не пришло ни одной строки
                # прогресса ДЛЯ ЭТОГО задания -- значит строки из
                # _LogReaderThread либо не доходят до
                # progress_chunk_received, либо не совпадают с
                # _TQDM_PROGRESS_RE. Дальше искать нужно уже по
                # этому логу, а не гадать.
                log.warning(
                    "ETA очереди: задание %s идёт уже %d опросов "
                    "подряд, но feed_log_line ни разу не распознал "
                    "для него строку прогресса -- ETA останется "
                    "'оценка...' (см. COMFY_LOG_PATH -- доходят ли "
                    "туда вообще строки вида 'N/M [...it/s]')",
                    current_single_id, self._stall_polls,
                )
                self._logged_stall_warning_for_ids.add(current_single_id)
        else:
            self._stall_polls = 0


    def _reset_session(self):
        # ComfyUI не запущен -- сбрасываем сессию и ETA-состояние,
        # чтобы при следующем запуске счёт начался заново с нуля, а не
        # продолжал считать от старого /history (это уже мог быть
        # другой процесс ComfyUI с чистым журналом) и от скорости шага,
        # замеренной в прошлый раз (могла быть другая модель/разрешение).
        self._teardown_ws_client()
        self._session_seen_history_ids = None
        self._session_done_ids = set()
        self._current_progress = None
        self._progress_for_id = None
        self._avg_sec_per_step = None
        self._logged_switch_for_ids = set()
        self._logged_progress_for_ids = set()
        self._stall_polls = 0
        self._logged_stall_warning_for_ids = set()


    def _poll(self):
        stats = self._collect_hardware_stats()

        port = self._get_running_port()
        if not port:
            # ComfyUI не запущен: незавершённый запрос очереди больше не
            # нужен, сессия и ETA-состояние сбрасываются.
            self._fetch.cancel()
            self._reset_session()
            self._last_queue_stats = {}
            self.stats_updated.emit(stats)
            return

        self._ensure_ws_client(port)
        started = self._fetch.run(
            lambda: self._fetch_queue_snapshot(port),
            lambda result, error: self._on_queue_snapshot(stats, port, result, error),
        )
        if not started:
            # Предыдущий запрос очереди ещё идёт (ComfyUI отвечает
            # медленно): железо обновляем сразу, очередь — по последнему
            # известному ответу.
            stats.update(self._last_queue_stats)
            self.stats_updated.emit(stats)


    def _on_queue_snapshot(self, stats, port, result, error):
        if error is not None:
            log.warning("Ошибка опроса очереди ComfyUI: %s", error)
            result = None
        if self._get_running_port() != port:
            # ComfyUI остановили или порт сменился за время запроса —
            # ответ устарел; следующий тик сам сбросит/перестроит сессию.
            return
        queue_state, history_ids = result if result is not None else (None, None)
        if queue_state is not None:
            self._apply_queue_snapshot(stats, queue_state, history_ids)
        self._last_queue_stats = {k: v for k, v in stats.items() if k.startswith("queue_")}
        self.stats_updated.emit(stats)


