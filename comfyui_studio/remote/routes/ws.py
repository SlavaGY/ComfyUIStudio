"""GET /api/v1/remote/ws?token=... (§2 дорожной карты, "Realtime (грубый
статус) + WS-инфраструктура для проксирования").

Токен -- query-параметром, а не заголовком Authorization: WebSocket API
браузеров и Android WebView не даёт произвольно проставлять заголовки
при установке соединения (см. §0.3 дорожной карты: токен "вставлен в
страницу" и используется JS-фронтендом Imagine напрямую при открытии
сокета) -- то же самое ограничение, из-за которого протокол WS вообще
не поддерживает кастомные заголовки handshake на уровне JS-API, отсюда
и query-параметр вместо Bearer.

Сам эндпоинт ничего не решает о ФОРМАТЕ событий -- он только проверяет
токен, регистрирует соединение в ConnectionHub (ws_hub.py) и держит его
открытым до отключения клиента. Наполняет хаб событиями фоновая задача
GenerationWatcher (см. generation_watcher.py и app.py, где она
запускается в lifespan)."""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..auth import resolve_device_from_token
from ..ws_hub import hub

router = APIRouter()

# 1008 -- стандартный WS close code "Policy Violation" (RFC 6455) --
# используется вместо кастомного кода, чтобы любой WS-клиент (включая
# будущий Android WebView) мог опереться на стандартную семантику, а не
# на код, придуманный только для этого проекта.
_POLICY_VIOLATION = 1008


@router.websocket("/ws")
async def remote_ws(websocket: WebSocket, token: str | None = None) -> None:
    # Принимаем handshake ДО проверки токена и закрываем сразу после,
    # если токен неверный -- см. докстринг о том, что WS API браузеров/
    # WebView не даёт вернуть произвольный HTTP-статус на этапе
    # handshake так же гибко, как для обычного HTTP-запроса; закрытие
    # сразу после accept() с кодом 1008 -- самый переносимый вариант
    # между версиями ASGI-серверов.
    await websocket.accept()
    device_id = resolve_device_from_token(token)
    if device_id is None:
        await websocket.close(code=_POLICY_VIOLATION)
        return

    await hub.connect(websocket)
    try:
        while True:
            # Remote ничего не ждёт ОТ клиента -- этот WS только
            # рассылает события (см. докстринг модуля). receive_text()
            # здесь исключительно чтобы поймать WebSocketDisconnect,
            # когда клиент (или сеть) закрывает соединение -- без
            # какого-то await здесь у сервера не было бы способа
            # узнать об отключении.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(websocket)
