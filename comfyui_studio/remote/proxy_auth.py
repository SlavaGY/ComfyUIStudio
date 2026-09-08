"""
Единая проверка токена устройства для "терминальных" маршрутов
(домашняя страница `routes/home.py` + reverse-proxy `imagine_proxy.py`)
-- этап 4 дорожной карты, §0.3 ("модель терминала").

В отличие от auth.py (`require_device` -- только заголовок
`Authorization: Bearer`, рассчитан на REST-клиента, который умеет сам
проставлять заголовки) и `routes/ws.py` (только query-параметр `?token=`,
потому что браузерный WebSocket API вообще не даёт задавать заголовки
на хендшейке) -- маршруты этапа 4 обязаны работать для ОБЫЧНОГО
браузера БЕЗ будущего Android-приложения: сам браузер запрашивает
статические ресурсы страницы (JS/CSS/картинки, `<script src>`/`<link
href>`/`<img src>`) сам, без единого кастомного заголовка или
query-параметра -- прицепить к каждому такому запросу `?token=...`
вручную невозможно, а вот cookie того же origin браузер приложит
всегда сам.

Поэтому здесь -- ТРИ источника токена, в порядке приоритета:

    1. `Authorization: Bearer <token>` -- будущий Android
       (`WebViewClient.shouldInterceptRequest` добавляет этот заголовок
       ко всем запросам страницы, см. §0.3, вариант 1).
    2. Cookie `remote_token=<token>` -- то, что реально позволяет
       ОБЫЧНОМУ браузеру САМОСТОЯТЕЛЬНО подгружать статику страницы,
       без дописывания `?token=` к каждому URL вручную.
    3. `?token=<token>` query-параметр -- только чтобы ПОЛУЧИТЬ эту
       куку в первый раз (см. критерий готовности §4: токен "руками
       подставленный в URL/куки" -- формулировка явно допускает оба
       способа). Если токен пришёл этим путём, вызывающая сторона
       (routes/home.py, imagine_proxy.py) обязана закрепить его в куке
       через set_token_cookie() -- см. их код.

`secure=False` у куки -- намеренно: Remote на этом этапе дорожной карты
обслуживает только голый HTTP в пределах LAN (см. §0/§9, HTTPS/VPN --
последующие этапы), а `Secure`-кука браузером через `http://` вообще не
отправляется -- пришлось бы тогда переизобретать этот же токен ещё и
как query-параметр на КАЖДОМ запросе, что и была задача убрать.
"""

from __future__ import annotations

from typing import Optional

from fastapi import Request, Response

from .device_store import find_device_by_token, touch_last_seen

COOKIE_NAME = "remote_token"
# ~1 год -- устройство и так можно отозвать в любой момент через Studio
# UI (см. routes/devices.py), поэтому долгий срок жизни куки не создаёт
# отдельного риска сверх того, что уже даёт сам access_token.
COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365


def resolve_device_and_token(request: Request) -> Optional[tuple[str, str]]:
    """Возвращает (device_id, token) при успехе, иначе None. Не бросает
    исключение сама -- это браузерные маршруты, а не REST API, поэтому
    оформление отказа (какую страницу/статус показать) остаётся на
    усмотрение вызывающей стороны, а не единообразный JSON 401, как у
    auth.require_device."""
    token = None
    authorization = request.headers.get("authorization")
    if authorization and authorization.startswith("Bearer "):
        token = authorization[len("Bearer "):].strip()
    if not token:
        token = request.cookies.get(COOKIE_NAME)
    if not token:
        token = request.query_params.get("token")
    if not token:
        return None

    device = find_device_by_token(token)
    if device is None or device.revoked:
        return None
    touch_last_seen(device.device_id)
    return device.device_id, token


def set_token_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
    )
