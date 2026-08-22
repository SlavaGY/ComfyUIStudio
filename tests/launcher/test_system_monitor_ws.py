"""
Интеграционный тест: ResourceMonitor (core/system_monitor.py)
использует WebSocket-канал ComfyUI (core/comfy_ws.py, этап 7 дорожной
карты) как ДОПОЛНИТЕЛЬНЫЙ источник прогресса поверх разбора stdout
(feed_log_line, этапы 0-6) -- НЕ вместо него.

ИСПРАВЛЕНО после первого прогона этапа 7 на реальной машине (см.
комментарий "ИСПРАВЛЕНО" в feed_log_line/system_monitor.py и в
comfy_ws.py): раньше этот файл проверял, что feed_log_line
ИГНОРИРУЕТСЯ, пока WS подключён -- это была регрессия, WS может
успешно подключиться, но не прислать ни одного "progress" события,
из-за чего ETA зависал на "оценка..." навсегда. Тесты ниже теперь
проверяют обратное: оба источника всегда активны одновременно, и
feed_log_line продолжает нормально работать независимо от состояния
WS-подключения.

HTTP-часть (/queue, /history из этапа 6) здесь не поднимается —
локальный сервер из conftest.py умеет только /ws, поэтому ветка
_poll() с queue_state (session-счётчик, switch-detection,
_compute_eta_seconds) здесь не активируется -- get_queue() просто
вернёт None, как при обычном "HTTP пока недоступен" (уже отдельно
покрыто tests/launcher/test_comfy_api.py и не требует повторной
проверки здесь). Этот файл целенаправленно проверяет ТОЛЬКО путь
WS-канала и его сосуществование с feed_log_line.
"""

from comfyui_studio.launcher.core.system_monitor import ResourceMonitor

_TQDM_LINE = "74%|███████▍         | 26/35 [00:24<00:07, 1.21it/s]"


def test_resource_monitor_applies_ws_progress(qtbot, ws_server):
    monitor = ResourceMonitor(get_running_port_fn=lambda: ws_server.port)

    monitor._poll()  # запускает _ensure_ws_client -> ComfyWebSocketClient.start()
    assert monitor._ws_client is not None
    with qtbot.waitSignal(monitor._ws_client.connected, timeout=5000):
        pass
    assert monitor._ws_connected is True

    conn = ws_server.wait_for_connection()

    with qtbot.waitSignal(monitor._ws_client.progress_received, timeout=5000):
        ws_server.send_text_from(
            conn,
            {"type": "progress", "data": {"value": 5, "max": 20, "prompt_id": "pid-1"}},
        )

    assert monitor._current_progress == {"done": 5, "total": 20}
    assert monitor._progress_for_id == "pid-1"

    monitor.stop()


def test_feed_log_line_still_works_while_ws_connected(qtbot, ws_server):
    """Главный регрессионный тест этой правки: WS подключён (значит,
    self._ws_connected is True), но НИ РАЗУ не прислал progress -- ровно
    сценарий из репорта пользователя. feed_log_line должен как ни в чём
    не бывало обновить ETA сам, а не молчать."""
    monitor = ResourceMonitor(get_running_port_fn=lambda: ws_server.port)
    monitor._poll()
    with qtbot.waitSignal(monitor._ws_client.connected, timeout=5000):
        pass
    assert monitor._ws_connected is True
    assert monitor._current_progress is None  # WS ничего не прислал

    monitor.feed_log_line(_TQDM_LINE)
    assert monitor._current_progress == {"done": 26, "total": 35}

    monitor.stop()


def test_ws_progress_can_update_after_stdout_already_did(qtbot, ws_server):
    """Оба источника пишут в одно и то же состояние -- более позднее
    сообщение от любого из них должно быть видно в self._current_progress,
    независимо от порядка."""
    monitor = ResourceMonitor(get_running_port_fn=lambda: ws_server.port)
    monitor._poll()
    with qtbot.waitSignal(monitor._ws_client.connected, timeout=5000):
        pass
    conn = ws_server.wait_for_connection()

    monitor.feed_log_line(_TQDM_LINE)
    assert monitor._current_progress == {"done": 26, "total": 35}

    with qtbot.waitSignal(monitor._ws_client.progress_received, timeout=5000):
        ws_server.send_text_from(
            conn, {"type": "progress", "data": {"value": 30, "max": 35, "prompt_id": "pid-1"}}
        )
    assert monitor._current_progress == {"done": 30, "total": 35}

    monitor.stop()


def test_resource_monitor_computes_rate_from_successive_ws_progress(qtbot, ws_server):
    monitor = ResourceMonitor(get_running_port_fn=lambda: ws_server.port)
    monitor._poll()
    with qtbot.waitSignal(monitor._ws_client.connected, timeout=5000):
        pass
    conn = ws_server.wait_for_connection()

    with qtbot.waitSignal(monitor._ws_client.progress_received, timeout=5000):
        ws_server.send_text_from(
            conn, {"type": "progress", "data": {"value": 1, "max": 20, "prompt_id": "pid-1"}}
        )
    assert monitor._avg_sec_per_step is None  # ещё нет второй точки для расчёта скорости

    with qtbot.waitSignal(monitor._ws_client.progress_received, timeout=5000):
        ws_server.send_text_from(
            conn, {"type": "progress", "data": {"value": 3, "max": 20, "prompt_id": "pid-1"}}
        )
    assert monitor._avg_sec_per_step is not None
    assert monitor._avg_sec_per_step >= 0

    monitor.stop()


def test_resource_monitor_no_ws_client_when_comfyui_not_running():
    # get_running_port_fn всегда возвращает None -- ComfyUI "не
    # запущен", WS-клиент вообще не должен создаваться.
    monitor = ResourceMonitor(get_running_port_fn=lambda: None)
    monitor._poll()
    assert monitor._ws_client is None
    monitor.stop()


def test_resource_monitor_stop_tears_down_ws_client(qtbot, ws_server):
    monitor = ResourceMonitor(get_running_port_fn=lambda: ws_server.port)
    monitor._poll()
    with qtbot.waitSignal(monitor._ws_client.connected, timeout=5000):
        pass

    monitor.stop()
    assert monitor._ws_client is None
    assert monitor._ws_connected is False
