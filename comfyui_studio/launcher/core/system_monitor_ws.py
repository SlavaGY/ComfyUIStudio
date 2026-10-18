"""Жизненный цикл WebSocket и обновление прогресса ResourceMonitor."""

import time

from .logging_setup import log
from .system_monitor_base import ResourceMonitorBase


class WebSocketMixin(ResourceMonitorBase):
    def _ensure_ws_client(self, port):
        """Создаёт и запускает ComfyWebSocketClient для порта, если он
        ещё не создан именно для этого порта. Вызывается из _poll на
        каждом тике, пока port известен -- дёшево: при совпадении порта
        это просто ранний return, реальная работа (создание клиента,
        запуск переподключения) происходит только при первом
        обнаружении порта или при его смене (например, порт поменяли в
        настройках между перезапусками ComfyUI)."""
        if self._ws_client is not None and self._ws_port == port:
            return
        self._teardown_ws_client()
        self._ws_port = port
        client = self._api.subscribe_websocket(port=port)
        if client is None:  # не должно случиться, раз port не None, но на всякий случай
            return
        client.connected.connect(self._on_ws_connected)
        client.disconnected.connect(self._on_ws_disconnected)
        client.progress_received.connect(self._on_ws_progress)
        self._ws_client = client
        client.start()


    def _teardown_ws_client(self):
        if self._ws_client is not None:
            self._ws_client.stop()
            self._ws_client.deleteLater()
        self._ws_client = None
        self._ws_port = None
        self._ws_connected = False
        self._ws_progress_prev = None


    def _on_ws_connected(self):
        self._ws_connected = True
        log.info(
            "ResourceMonitor: WebSocket-канал ComfyUI подключён (добавляется "
            "как дополнительный источник прогресса поверх tqdm-разбора "
            "stdout, а не вместо него -- см. feed_log_line)"
        )


    def _on_ws_disconnected(self):
        if self._ws_connected:
            log.info(
                "ResourceMonitor: WebSocket-канал ComfyUI отключился -- ETA "
                "продолжает вестись по разбору stdout, как и раньше (см. "
                "feed_log_line)"
            )
        self._ws_connected = False
        self._ws_progress_prev = None


    def _on_ws_progress(self, data):
        """Обновляет прогресс по value/max/prompt_id, проверяя типы входных данных.

        Скорость шага вычисляется по разнице времени и шагов двух последовательных
        сообщений одного задания. Неизвестные дополнительные поля игнорируются."""
        value = data.get("value")
        total = data.get("max")
        prompt_id = data.get("prompt_id")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return
        if not isinstance(total, (int, float)) or isinstance(total, bool) or total <= 0:
            return

        now = time.monotonic()
        prev = self._ws_progress_prev
        if prev is not None and prev[0] == prompt_id and value > prev[1]:
            dt = now - prev[2]
            d_steps = value - prev[1]
            if dt > 0 and d_steps > 0:
                self._avg_sec_per_step = dt / d_steps
        self._ws_progress_prev = (prompt_id, value, now)

        self._current_progress = {"done": int(value), "total": int(total)}
        if prompt_id:
            # Без prompt_id идентификатор задания определяет _poll
            # по running_ids; неполное WS-сообщение его не сбрасывает.
            self._progress_for_id = prompt_id
