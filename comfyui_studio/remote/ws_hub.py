"""
Реестр активных WebSocket-подключений Remote (этап 2 дорожной карты,
"Realtime (грубый статус) + WS-инфраструктура для проксирования").

Один процесс Remote -- один экземпляр ConnectionHub на все подключения
(см. синглтон `hub` внизу файла, тот же приём, что и у
`pairing_manager` в pairing.py). Хаб не проверяет токены и не решает,
кому что можно -- это уже сделано в routes/ws.py ДО вызова connect()
(см. его докстринг); хаб только хранит множество живых соединений и
рассылает им события.

asyncio.Lock, а не threading.Lock -- в отличие от device_store.py
(синхронный файловый I/O, вызываемый из sync-обработчиков FastAPI),
WebSocket-эндпоинты в FastAPI/Starlette всегда async, и вся работа с
хабом происходит в одном event loop -- обычный asyncio-примитив
синхронизации здесь уместнее и не блокирует event loop, в отличие от
threading.Lock.
"""

from __future__ import annotations

import asyncio

from fastapi import WebSocket


class ConnectionHub:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)

    async def broadcast(self, event: dict) -> None:
        """Рассылает event всем живым подключениям. Подключение, которое
        не смогло принять сообщение (уже отвалилось, но
        WebSocketDisconnect в его собственном receive-цикле ещё не
        успел сработать) -- тихо отключаем здесь же, а не роняем всю
        рассылку остальным из-за одного мёртвого сокета."""
        async with self._lock:
            targets = list(self._connections)
        for websocket in targets:
            try:
                await websocket.send_json(event)
            except Exception:
                await self.disconnect(websocket)

    def connection_count(self) -> int:
        return len(self._connections)


# Один экземпляр на процесс Remote -- см. докстринг класса.
hub = ConnectionHub()
