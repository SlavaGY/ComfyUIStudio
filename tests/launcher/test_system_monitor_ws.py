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

import pytest

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


# -- Этап 8, вторая попытка: JS-мост вместо второго WS-соединения
# (см. подробный разбор в ui/browser_page.py у _EVENT_BRIDGE_JS и в
# comfy_ws.py про то, почему первая попытка -- реальный clientId в
# отдельном ComfyWebSocketClient -- была отменена) --------------------


def test_ws_client_never_uses_external_client_id(qtbot, ws_server):
    """ResourceMonitor больше не принимает никакой get_client_id_fn --
    ComfyWebSocketClient всегда создаётся с собственным uuid4
    (client_id=None), никогда не с чужим id. Это единственное
    надёжное подтверждение того, что вытесняющий страницу сценарий
    (см. comfy_ws.py) больше не может воспроизвестись."""
    monitor = ResourceMonitor(get_running_port_fn=lambda: ws_server.port)
    monitor._poll()
    assert monitor._ws_client is not None
    with qtbot.waitSignal(monitor._ws_client.connected, timeout=5000):
        pass
    path = ws_server.paths[-1]
    assert "clientId=" in path
    # ни один из тестовых client_id из других тестов файла не должен
    # тут всплыть -- сам факт отсутствия параметра get_client_id_fn в
    # сигнатуре ResourceMonitor.__init__ (см. TypeError ниже) -- и есть
    # основная проверка.
    monitor.stop()


def test_resource_monitor_rejects_get_client_id_fn_kwarg():
    """get_client_id_fn АРХИТЕКТУРНО убран из ResourceMonitor (не просто
    игнорируется) -- попытка передать его должна быть явной ошибкой на
    вызове, а не тихо проглатываться, чтобы никто случайно не вернул
    отменённое поведение обратно."""
    with pytest.raises(TypeError):
        ResourceMonitor(get_running_port_fn=lambda: 8188, get_client_id_fn=lambda: "x")


def test_feed_ws_event_progress_updates_state_like_direct_ws(qtbot, ws_server):
    """feed_ws_event("progress", payload) -- вызываемый из
    BrowserPage.comfy_event_received (JS-мост) -- обновляет то же
    состояние (self._current_progress/_avg_sec_per_step), что и раньше
    обновлял бы прямой WS-канал через _on_ws_progress. Формат payload
    идентичен: это то же самое сообщение сервера ComfyUI, просто
    доставленное другим путём."""
    monitor = ResourceMonitor(get_running_port_fn=lambda: ws_server.port)
    monitor.feed_ws_event("progress", {"value": 3, "max": 8, "prompt_id": "pid-1"})
    assert monitor._current_progress == {"done": 3, "total": 8}
    assert monitor._progress_for_id == "pid-1"
    monitor.stop()


def test_feed_ws_event_other_types_do_not_crash(qtbot, ws_server):
    """Типы, для которых пока нет предметного разбора (status/
    progress_state/executed/execution_cached/...), не должны падать --
    просто диагностика (см. self._seen_bridge_event_types). "executing"
    сюда больше не входит -- он обрабатывается предметно, см. тесты
    ниже."""
    monitor = ResourceMonitor(get_running_port_fn=lambda: ws_server.port)
    monitor.feed_ws_event("status", {"sid": "abc"})
    monitor.feed_ws_event("progress_state", {"nodes": {}})
    monitor.feed_ws_event("progress_state", {"nodes": {}})  # тот же тип второй раз
    assert monitor._seen_bridge_event_types == {"status", "progress_state"}
    monitor.stop()


# -- Этап 8, последний пункт: индикатор текущей ноды / баннер ошибки ------


def test_feed_ws_event_executing_emits_node_execution_changed(qtbot, ws_server):
    monitor = ResourceMonitor(get_running_port_fn=lambda: ws_server.port)
    received = []
    monitor.node_execution_changed.connect(received.append)

    monitor.feed_ws_event(
        "executing", {"node": "5", "display_node": "KSampler", "prompt_id": "pid-1"}
    )
    assert received == [{"node": "5", "display_node": "KSampler", "prompt_id": "pid-1"}]

    # node=None -- конец прогона, сигнал с None (не пустой dict).
    monitor.feed_ws_event("executing", {"node": None, "prompt_id": "pid-1"})
    assert received[-1] is None

    monitor.stop()


def test_feed_ws_event_execution_error_emits_payload_and_logs(qtbot, ws_server):
    monitor = ResourceMonitor(get_running_port_fn=lambda: ws_server.port)
    received = []
    monitor.execution_error_occurred.connect(received.append)

    error_payload = {
        "node_id": "7",
        "node_type": "KSampler",
        "exception_message": "boom",
        "exception_type": "RuntimeError",
        "prompt_id": "pid-1",
    }
    monitor.feed_ws_event("execution_error", error_payload)
    assert received == [error_payload]

    monitor.stop()


def test_feed_ws_event_execution_start_clears_error_banner(qtbot, ws_server):
    monitor = ResourceMonitor(get_running_port_fn=lambda: ws_server.port)
    received = []
    monitor.execution_error_occurred.connect(received.append)

    monitor.feed_ws_event("execution_error", {"node_type": "X", "exception_message": "boom"})
    monitor.feed_ws_event("execution_start", {"prompt_id": "pid-2"})
    assert received[-1] is None  # новый прогон стартовал -- баннер очищен

    monitor.stop()


def test_feed_ws_event_execution_interrupted_clears_both(qtbot, ws_server):
    """execution_interrupted -- пользователь сам прервал прогон (см.
    QueueHistoryDialog.interrupt()), это не ошибка -- и индикатор
    ноды, и баннер ошибки должны очиститься."""
    monitor = ResourceMonitor(get_running_port_fn=lambda: ws_server.port)
    node_received = []
    error_received = []
    monitor.node_execution_changed.connect(node_received.append)
    monitor.execution_error_occurred.connect(error_received.append)

    monitor.feed_ws_event("executing", {"node": "5", "prompt_id": "pid-1"})
    monitor.feed_ws_event("execution_error", {"node_type": "X", "exception_message": "boom"})
    monitor.feed_ws_event("execution_interrupted", {"prompt_id": "pid-1"})

    assert node_received[-1] is None
    assert error_received[-1] is None

    monitor.stop()


def test_comfyui_stop_clears_active_node_and_error_indicators(qtbot, ws_server):
    """Когда ComfyUI перестаёт быть запущен (_poll видит port=None), а
    индикатор ноды/баннер ошибки были активны -- их надо явно
    сбросить, иначе UI будет показывать состояние от уже мёртвого
    процесса."""
    port_holder = {"port": ws_server.port}
    monitor = ResourceMonitor(get_running_port_fn=lambda: port_holder["port"])
    node_received = []
    error_received = []
    monitor.node_execution_changed.connect(node_received.append)
    monitor.execution_error_occurred.connect(error_received.append)

    monitor.feed_ws_event("executing", {"node": "5", "prompt_id": "pid-1"})
    monitor.feed_ws_event("execution_error", {"node_type": "X", "exception_message": "boom"})

    port_holder["port"] = None
    monitor._poll()

    assert node_received[-1] is None
    assert error_received[-1] is None

    monitor.stop()

