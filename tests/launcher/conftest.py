"""
РћР±С‰РёРµ fixtures РґР»СЏ tests/launcher/ -- СЃРµР№С‡Р°СЃ С‚РѕР»СЊРєРѕ Р»РѕРєР°Р»СЊРЅС‹Р№
WebSocket-СЃРµСЂРІРµСЂ (test_comfy_ws.py, test_system_monitor_ws.py, СЌС‚Р°Рї 7
РґРѕСЂРѕР¶РЅРѕР№ РєР°СЂС‚С‹), РІС‹РЅРµСЃРµРЅ СЃСЋРґР° РёР· test_comfy_ws.py, С‡С‚РѕР±С‹ РЅРµ РґСѓР±Р»РёСЂРѕРІР°С‚СЊ
РјРµР¶РґСѓ С„Р°Р№Р»Р°РјРё С‚РµСЃС‚РѕРІ.
"""

import asyncio
import json
import socket
import threading

import pytest

# websockets РќР• РёРјРїРѕСЂС‚РёСЂСѓРµС‚СЃСЏ РЅР° СѓСЂРѕРІРЅРµ РјРѕРґСѓР»СЏ: Skipped РЅР°СЃР»РµРґСѓРµС‚
# BaseException, Р° pytest РїСЂРё РёРјРїРѕСЂС‚Рµ conftest.py Р»РѕРІРёС‚ С‚РѕР»СЊРєРѕ Exception --
# РѕС‚СЃСѓС‚СЃС‚РІРёРµ РѕРґРЅРѕР№ РѕРїС†РёРѕРЅР°Р»СЊРЅРѕР№ Р±РёР±Р»РёРѕС‚РµРєРё Р±Р»РѕРєРёСЂРѕРІР°Р»Рѕ Р±С‹ РІСЃСЋ РґРёСЂРµРєС‚РѕСЂРёСЋ
# tests/launcher. РўРµРїРµСЂСЊ РїСЂРѕРїСѓСЃРє Р»РѕРєР°Р»РµРЅ Рё РїСЂРѕРёСЃС…РѕРґРёС‚ РІ С‚РµС… С‚РµСЃС‚Р°С…/
# С„РёРєСЃС‚СѓСЂР°С…, РєРѕС‚РѕСЂС‹Рј СЃРµСЂРІРµСЂ РґРµР№СЃС‚РІРёС‚РµР»СЊРЅРѕ РЅСѓР¶РµРЅ (СЃРј. LocalWsServer._run).


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class LocalWsServer:
    """РњРёРЅРёРјР°Р»СЊРЅС‹Р№ СЃРµСЂРІРµСЂ /ws РІ РѕС‚РґРµР»СЊРЅРѕРј РїРѕС‚РѕРєРµ СЃРѕ СЃРІРѕРёРј event loop'РѕРј
    (asyncio Сѓ websockets Рё Qt-event-loop РІ РіР»Р°РІРЅРѕРј РїРѕС‚РѕРєРµ С‚РµСЃС‚Р° вЂ”
    СЂР°Р·РЅС‹Рµ event loop'С‹, РїРѕСЌС‚РѕРјСѓ СЃРµСЂРІРµСЂ Р¶РёРІС‘С‚ РІ СЃРІРѕС‘Рј РїРѕС‚РѕРєРµ, Р° РЅРµ
    РїС‹С‚Р°РµС‚СЃСЏ РґРµР»РёС‚СЊ event loop СЃ Qt)."""

    def __init__(self):
        self.port = _free_port()
        self._loop = None
        self._server = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._ready = threading.Event()
        self.connections = []
        self.paths = []  # request.path (СЃ query-СЃС‚СЂРѕРєРѕР№) РґР»СЏ РєР°Р¶РґРѕРіРѕ РїРѕРґРєР»СЋС‡РµРЅРёСЏ --
        # СЃРј. test_comfy_ws.py::test_client_connects_with_client_id_query_param

    def _run(self):
        import websockets

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        async def _handler(ws):
            self.connections.append(ws)
            self.paths.append(getattr(getattr(ws, "request", None), "path", None) or getattr(ws, "path", None))
            try:
                async for _ in ws:
                    pass  # СЃРµСЂРІРµСЂ С‚РѕР»СЊРєРѕ С€Р»С‘С‚, РІС…РѕРґСЏС‰РёРµ СЃРѕРѕР±С‰РµРЅРёСЏ РєР»РёРµРЅС‚Р° РЅРµ РЅСѓР¶РЅС‹
            except websockets.exceptions.ConnectionClosed:
                pass

        async def _start():
            self._server = await websockets.serve(_handler, "127.0.0.1", self.port)
            self._ready.set()
            await self._server.wait_closed()

        self._loop.run_until_complete(_start())

    def start(self):
        self._thread.start()
        assert self._ready.wait(timeout=5), "Р»РѕРєР°Р»СЊРЅС‹Р№ WS-СЃРµСЂРІРµСЂ РЅРµ РїРѕРґРЅСЏР»СЃСЏ"

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
        # РќРµ Р¶РґС‘Рј .result() Р·РґРµСЃСЊ: СЃРµСЂРІРµСЂРЅС‹Р№ ws.close() Р¶РґС‘С‚ closing
        # handshake РѕС‚ РєР»РёРµРЅС‚Р° (RFC 6455), Р° РєР»РёРµРЅС‚ (QWebSocket) РјРѕР¶РµС‚
        # РѕС‚РІРµС‚РёС‚СЊ С‚РѕР»СЊРєРѕ РїРѕРєР° РєСЂСѓС‚РёС‚СЃСЏ Qt event loop. Р•СЃР»Рё СЃРёРЅС…СЂРѕРЅРЅРѕ
        # Р·Р°Р±Р»РѕРєРёСЂРѕРІР°С‚СЊ Р“Р›РђР’РќР«Р™ (Qt) РїРѕС‚РѕРє С‚РµСЃС‚Р° РЅР° СЌС‚РѕРј .result(), Qt
        # event loop РЅРµ Р±СѓРґРµС‚ РєР°С‡Р°С‚СЊ СЃРµС‚СЊ, РєР»РёРµРЅС‚ РЅРµ СЃРјРѕР¶РµС‚ РѕС‚РїСЂР°РІРёС‚СЊ
        # close-ack, Рё СЌС‚Рѕ Р±СѓРґСѓС‰РµРµ РЅРёРєРѕРіРґР° РЅРµ Р·Р°РІРµСЂС€РёС‚СЃСЏ -- РґРµРґР»РѕРє
        # СЂРѕРІРЅРѕ РёР·-Р·Р° С‚Р°РєРѕРіРѕ Р±Р»РѕРєРёСЂСѓСЋС‰РµРіРѕ РѕР¶РёРґР°РЅРёСЏ. РџРѕСЌС‚РѕРјСѓ РїСЂРѕСЃС‚Рѕ
        # РїР»Р°РЅРёСЂСѓРµРј РєРѕСЂСѓС‚РёРЅСѓ РЅР° event loop СЃРµСЂРІРµСЂР° Рё СЃСЂР°Р·Сѓ РІРѕР·РІСЂР°С‰Р°РµРјСЃСЏ
        # -- РґР°Р»СЊС€Рµ РµС‘ РґРѕР»Р¶РµРЅ РґРѕРєСЂСѓС‚РёС‚СЊ qtbot.waitSignal(...), РєРѕС‚РѕСЂС‹Р№
        # (РІ РѕС‚Р»РёС‡РёРµ РѕС‚ РїСЂСЏРјРѕРіРѕ .result()) РґРµР№СЃС‚РІРёС‚РµР»СЊРЅРѕ РєСЂСѓС‚РёС‚ Qt
        # event loop, РїРѕРєР° Р¶РґС‘С‚ СЃРёРіРЅР°Р».
        asyncio.run_coroutine_threadsafe(ws.close(), self._loop)

    def wait_for_connection(self, timeout=5):
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.connections:
                return self.connections[-1]
            time.sleep(0.02)
        raise AssertionError("РєР»РёРµРЅС‚ РЅРµ РїРѕРґРєР»СЋС‡РёР»СЃСЏ Рє Р»РѕРєР°Р»СЊРЅРѕРјСѓ WS-СЃРµСЂРІРµСЂСѓ РІРѕРІСЂРµРјСЏ")


@pytest.fixture
def ws_server():
    pytest.importorskip("websockets")
    server = LocalWsServer()
    server.start()
    yield server
    server.stop()
