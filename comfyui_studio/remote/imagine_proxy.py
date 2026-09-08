"""
Reverse-proxy к Imagine -- `/apps/imagine/* -> http://127.0.0.1:<imagine_port>/*`
(этап 4 дорожной карты, §0.3 "модель терминала"). Наружу торчит только
порт Remote -- порт Imagine, куда проксируются запросы, остаётся
недоступным напрямую снаружи (см. §0.3, "Что на сервере").

HTML-ответы (сама страница Imagine, `index.html`, отдаваемая
`StaticFiles(html=True)` в imagine/backend/main.py) получают внедрённый
`<script>window.__REMOTE_TOKEN__ = "...";</script>` перед `</head>` --
см. proxy_auth.py и §0.3, вариант 2: браузерный WebSocket API не умеет
добавить `Authorization`-заголовок на хендшейк, поэтому JS-фронтенд
Imagine получает токен через эту глобальную переменную и сам
подставляет его как `?token=...` при подключении к
`/api/v1/remote/ws` (см. routes/ws.py -- эндпоинт и так уже проектировался
принимать токен через query-параметр, см. §2, согласуется само собой).

Все ОСТАЛЬНЫЕ ответы (JS/CSS/картинки/JSON API самого Imagine) отдаются
потоково, без разбора тела -- подмена (чтение всего тела в память,
поиск/замена) была бы лишней тратой памяти на потенциально крупные
файлы картинок и сломала бы `Range`-запросы (частичная загрузка
изображений); имеет смысл ТОЛЬКО для HTML-страницы, которая и так
маленькая и запрашивается не потоково.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from .apps_registry import get_app
from .proxy_auth import resolve_device_and_token, set_token_cookie

router = APIRouter()

# Один httpx.AsyncClient на процесс Remote -- переиспользует TCP-
# соединения к Imagine между запросами (тот же принцип, что и у
# персистентных клиентов в остальном проекте, напр. ComfyAPIClient),
# вместо нового соединения на каждый проксируемый запрос.
_client = httpx.AsyncClient(timeout=60.0)

# Заголовки, которые нельзя слепо копировать между исходным запросом/
# ответом и проксируемым:
#   - "host"/"connection" -- специфичны для TCP-соединения конкретно с
#     Remote, к Imagine их пересылать не нужно (httpx сам выставит
#     корректный Host для upstream-запроса).
#   - "cookie" -- содержит remote_token, предназначенный для Remote
#     (см. proxy_auth.py), Imagine про него ничего не знает и не должен
#     знать.
#   - "content-length"/"transfer-encoding" в ОТВЕТЕ -- становятся
#     неверными после внедрения токена в HTML (тело меняет размер),
#     Starlette/uvicorn сами выставят корректные при отправке ответа.
_HOP_BY_HOP_REQUEST_HEADERS = {"host", "connection", "cookie"}
_HOP_BY_HOP_RESPONSE_HEADERS = {"connection", "content-length", "transfer-encoding"}


@router.api_route(
    "/apps/imagine/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def proxy_imagine(path: str, request: Request):
    resolved = resolve_device_and_token(request)
    if resolved is None:
        raise HTTPException(
            status_code=401,
            detail="Нужен токен устройства -- откройте / с ?token=... из Studio.",
        )
    _device_id, token = resolved

    app = get_app("imagine")
    if app is None:
        raise HTTPException(status_code=404, detail="Imagine не зарегистрирован в Remote.")
    target_base = app.proxy_target()
    if target_base is None:
        raise HTTPException(status_code=503, detail="Imagine сейчас не запущен.")

    upstream_url = f"{target_base}/{path}"
    upstream_headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in _HOP_BY_HOP_REQUEST_HEADERS
    }
    body = await request.body()

    upstream_request = _client.build_request(
        request.method,
        upstream_url,
        params=request.query_params,
        headers=upstream_headers,
        content=body,
    )
    try:
        upstream_response = await _client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Imagine недоступен: {exc}")

    content_type = upstream_response.headers.get("content-type", "")
    response_headers = {
        key: value
        for key, value in upstream_response.headers.items()
        if key.lower() not in _HOP_BY_HOP_RESPONSE_HEADERS
    }

    if content_type.startswith("text/html"):
        raw_body = await upstream_response.aread()
        await upstream_response.aclose()
        injected_body = raw_body.replace(
            b"</head>",
            f'<script>window.__REMOTE_TOKEN__ = {token!r};</script></head>'.encode("utf-8"),
            1,
        )
        response = Response(
            content=injected_body,
            status_code=upstream_response.status_code,
            headers=response_headers,
            media_type=content_type,
        )
    else:
        response = StreamingResponse(
            upstream_response.aiter_raw(),
            status_code=upstream_response.status_code,
            headers=response_headers,
            media_type=content_type,
            background=BackgroundTask(upstream_response.aclose),
        )

    if request.query_params.get("token"):
        # См. тот же приём в routes/home.py -- закрепляем токен в куке,
        # если он пришёл через URL, чтобы дальнейшие запросы статики
        # этой же страницы браузер выполнял сам, без ?token= в каждом.
        set_token_cookie(response, token)
    return response
