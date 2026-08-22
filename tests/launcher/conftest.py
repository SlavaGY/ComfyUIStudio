"""
Общие fixtures для tests/launcher/ -- сейчас только локальный
WebSocket-сервер (test_comfy_ws.py, test_system_monitor_ws.py, этап 7
дорожной карты), вынесен сюда из test_comfy_ws.py, чтобы не дублировать
между файлами тестов.
"""

import asyncio
import json
import socket
import threading

import pytest

websockets = pytest.importorskip("websockets")


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class LocalWsServer:
    """Минимальный сервер /ws в отдельном потоке со своим event loop'ом
    (asyncio у websockets и Qt-event-loop в главном потоке теста —
    разные event loop'ы, поэтому сервер живёт в своём потоке, а не
    пытается делить event loop с Qt)."""

    def __init__(self):
        self.port = _free_port()
        self._loop = None
        self._server = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._ready = threading.Event()
        self.connections = []
        self.paths = []  # request.path (с query-строкой) для каждого подключения --
        # см. test_comfy_ws.py::test_client_connects_with_client_id_query_param

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        async def _handler(ws):
            self.connections.append(ws)
            self.paths.append(getattr(getattr(ws, "request", None), "path", None))
            try:
                async for _ in ws:
                    pass  # сервер только шлёт, входящие сообщения клиента не нужны
            except websockets.exceptions.ConnectionClosed:
                pass

        async def _start():
            self._server = await websockets.serve(_handler, "127.0.0.1", self.port)
            self._ready.set()
            await self._server.wait_closed()

        self._loop.run_until_complete(_start())

    def start(self):
        self._thread.start()
        assert self._ready.wait(timeout=5), "локальный WS-сервер не поднялся"

    def stop(self):
        if self._server is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(self._server.close)
        self._thread.join(timeout=5)

    def send_text_from(self, ws, payload: dict):
        asyncio.run_coroutine_threadsafe(
            ws.send(json.dumps(payload)), self._loop
        ).result(timeout=5)

    def send_binary_from(self, ws, raw: bytes):
        asyncio.run_coroutine_threadsafe(ws.send(raw), self._loop).result(timeout=5)

    def close_connection(self, ws):
        # Не ждём .result() здесь: серверный ws.close() ждёт closing
        # handshake от клиента (RFC 6455), а клиент (QWebSocket) может
        # ответить только пока крутится Qt event loop. Если синхронно
        # заблокировать ГЛАВНЫЙ (Qt) поток теста на этом .result(), Qt
        # event loop не будет качать сеть, клиент не сможет отправить
        # close-ack, и это будущее никогда не завершится -- дедлок
        # ровно из-за такого блокирующего ожидания. Поэтому просто
        # планируем корутину на event loop сервера и сразу возвращаемся
        # -- дальше её должен докрутить qtbot.waitSignal(...), который
        # (в отличие от прямого .result()) действительно крутит Qt
        # event loop, пока ждёт сигнал.
        asyncio.run_coroutine_threadsafe(ws.close(), self._loop)

    def wait_for_connection(self, timeout=5):
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.connections:
                return self.connections[-1]
            time.sleep(0.02)
        raise AssertionError("клиент не подключился к локальному WS-серверу вовремя")


@pytest.fixture
def ws_server():
    server = LocalWsServer()
    server.start()
    yield server
    server.stop()
