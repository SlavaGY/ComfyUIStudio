"""
Тесты comfyui_studio.launcher.core.comfy_ws.ComfyWebSocketClient (этап 7
дорожной карты — WebSocket realtime layer).

В отличие от tests/launcher/test_comfy_api.py, здесь сеть НЕ мокается —
поднимается настоящий локальный WebSocket-сервер (библиотека
`websockets`, dev-only зависимость только для этого теста, к рантайму
приложения отношения не имеет, см. conftest.py) на 127.0.0.1, и
ComfyWebSocketClient подключается к нему по-настоящему через QWebSocket.
Это единственный надёжный способ проверить главное, ради чего этот
модуль вообще написан отдельно от остальных этапов: что смешанные
текстовые (JSON) и бинарные (превью-фреймы) сообщения на одном сокете
не роняют клиент — мок на уровне Python-функции (как в
test_comfy_api.py) не задействовал бы настоящий разбор фреймов
QWebSocket и ничего бы не доказал.

Требует PySide6 (QtWebSockets) и запущенный QApplication — даёт его
qtbot из pytest-qt (см. tools/promptvault/tests/conftest.py, тот же
паттерн уже используется для тестов PromptVault с реальными
Qt-виджетами). ws_server -- fixture из conftest.py (общая с
test_system_monitor_ws.py).
"""

import struct

import pytest

pytest.importorskip("websockets")

from comfyui_studio.launcher.core.comfy_ws import ComfyWebSocketClient  # noqa: E402


def test_connects_and_receives_typed_events(qtbot, ws_server):
    client = ComfyWebSocketClient(ws_server.port)

    with qtbot.waitSignal(client.connected, timeout=5000):
        client.start()

    assert client.is_connected is True
    conn = ws_server.wait_for_connection()

    with qtbot.waitSignal(client.progress_received, timeout=5000) as blocker:
        ws_server.send_text_from(
            conn,
            {"type": "progress", "data": {"value": 5, "max": 20, "prompt_id": "pid-1"}},
        )
    assert blocker.args[0] == {"value": 5, "max": 20, "prompt_id": "pid-1"}

    with qtbot.waitSignal(client.executing_received, timeout=5000) as blocker:
        ws_server.send_text_from(
            conn, {"type": "executing", "data": {"node": "3", "prompt_id": "pid-1"}}
        )
    assert blocker.args[0]["node"] == "3"

    with qtbot.waitSignal(client.status_received, timeout=5000):
        ws_server.send_text_from(
            conn, {"type": "status", "data": {"exec_info": {"queue_remaining": 2}}}
        )

    with qtbot.waitSignal(client.executed_received, timeout=5000):
        ws_server.send_text_from(
            conn, {"type": "executed", "data": {"node": "3", "prompt_id": "pid-1", "output": {}}}
        )

    with qtbot.waitSignal(client.execution_error_received, timeout=5000):
        ws_server.send_text_from(
            conn, {"type": "execution_error", "data": {"prompt_id": "pid-1", "node_id": "3"}}
        )

    client.stop()


def test_generic_event_received_fires_for_every_type(qtbot, ws_server):
    client = ComfyWebSocketClient(ws_server.port)
    seen = []
    client.event_received.connect(lambda t, d: seen.append((t, d)))

    with qtbot.waitSignal(client.connected, timeout=5000):
        client.start()
    conn = ws_server.wait_for_connection()

    with qtbot.waitSignal(client.event_received, timeout=5000):
        ws_server.send_text_from(conn, {"type": "status", "data": {"foo": "bar"}})

    assert seen == [("status", {"foo": "bar"})]
    client.stop()


def test_subscribe_websocket_callback_wires_to_event_received(qtbot, ws_server):
    from comfyui_studio.launcher.core.comfy_api import ComfyAPIClient

    seen = []
    api = ComfyAPIClient(port=ws_server.port)
    client = api.subscribe_websocket(callback=lambda t, d: seen.append((t, d)))
    assert client is not None

    with qtbot.waitSignal(client.connected, timeout=5000):
        client.start()
    conn = ws_server.wait_for_connection()

    with qtbot.waitSignal(client.event_received, timeout=5000):
        ws_server.send_text_from(conn, {"type": "progress", "data": {"value": 1, "max": 1}})

    assert seen == [("progress", {"value": 1, "max": 1})]
    client.stop()


def test_subscribe_websocket_none_without_port():
    from comfyui_studio.launcher.core.comfy_api import ComfyAPIClient

    api = ComfyAPIClient()
    assert api.subscribe_websocket() is None


def test_binary_frame_does_not_crash_and_text_still_works_after(qtbot, ws_server):
    """Главный тест этапа 7: бинарный превью-фрейм между двумя текстовыми
    JSON-сообщениями не должен ронять клиент/соединение и не должен
    мешать разбору следующего текстового сообщения."""
    client = ComfyWebSocketClient(ws_server.port)

    with qtbot.waitSignal(client.connected, timeout=5000):
        client.start()
    conn = ws_server.wait_for_connection()

    # 1) обычное текстовое сообщение до бинарного фрейма
    with qtbot.waitSignal(client.progress_received, timeout=5000):
        ws_server.send_text_from(
            conn, {"type": "progress", "data": {"value": 1, "max": 10}}
        )

    # 2) бинарный превью-фрейм в формате ComfyUI: 4 байта big-endian
    # event-type (1 = PREVIEW_IMAGE), 4 байта image-type, дальше "картинка"
    # (тут просто произвольные байты — содержимое неважно, важно, что
    # клиент не пытается json.loads() это).
    fake_preview = struct.pack(">II", 1, 1) + b"\xff\xd8\xff\xe0not-a-real-jpeg"
    with qtbot.waitSignal(client.preview_frame_received, timeout=5000) as blocker:
        ws_server.send_binary_from(conn, fake_preview)
    assert blocker.args[0] == 1  # event_type распознан
    assert blocker.args[1] == fake_preview

    # 3) соединение должно быть ЖИВО и текстовые сообщения должны
    # по-прежнему нормально разбираться после бинарного фрейма
    assert client.is_connected is True
    with qtbot.waitSignal(client.progress_received, timeout=5000) as blocker:
        ws_server.send_text_from(
            conn, {"type": "progress", "data": {"value": 2, "max": 10}}
        )
    assert blocker.args[0]["value"] == 2

    client.stop()


def test_malformed_json_text_is_ignored_not_fatal(qtbot, ws_server):
    client = ComfyWebSocketClient(ws_server.port)

    with qtbot.waitSignal(client.connected, timeout=5000):
        client.start()
    conn = ws_server.wait_for_connection()

    ws_server.send_text_from(conn, "this is not json {{{")

    # Соединение не должно порваться от мусорного сообщения, и
    # следующее нормальное сообщение должно долетать как обычно.
    with qtbot.waitSignal(client.progress_received, timeout=5000):
        ws_server.send_text_from(conn, {"type": "progress", "data": {"value": 1, "max": 1}})

    assert client.is_connected is True
    client.stop()


def test_disconnected_signal_on_server_close(qtbot, ws_server):
    client = ComfyWebSocketClient(ws_server.port)

    with qtbot.waitSignal(client.connected, timeout=5000):
        client.start()
    conn = ws_server.wait_for_connection()

    with qtbot.waitSignal(client.disconnected, timeout=5000):
        ws_server.close_connection(conn)

    assert client.is_connected is False
    client.stop()


def test_stop_before_any_connection_is_safe():
    client = ComfyWebSocketClient(65535)  # заведомо ничего не слушает
    client.stop()  # не должно падать, даже не будучи запущенным
    assert client.is_connected is False


def test_client_connects_with_client_id_query_param(qtbot, ws_server):
    """Регрессионный тест: старая (дочатовая) реализация подключалась с
    ?clientId=<uuid>, эта изначально нет -- см. докстринг модуля про то,
    почему это могло быть причиной того, что ComfyUI не присылал
    progress-события даже при успешном подключении. Фиксируем, что
    query-параметр реально уходит на сервер и совпадает с
    client.client_id."""
    client = ComfyWebSocketClient(ws_server.port)
    with qtbot.waitSignal(client.connected, timeout=5000):
        client.start()

    assert ws_server.paths, "сервер не увидел ни одного подключения"
    path = ws_server.paths[-1]
    assert path is not None
    assert path.startswith("/ws?")
    assert f"clientId={client.client_id}" in path

    client.stop()
