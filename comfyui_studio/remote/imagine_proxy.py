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
from .state import runtime

router = APIRouter()

# Один httpx.AsyncClient на процесс Remote -- переиспользует TCP-
# соединения к Imagine между запросами (тот же принцип, что и у
# персистентных клиентов в остальном проекте, напр. ComfyAPIClient).
#
# НАЙДЕННЫЙ БАГ (живой отчёт: "GeneratedImageSaver: timeout" на второй
# из двух картинок генерации, первая сохранилась нормально): именно
# keep-alive переиспользование соединения здесь и виновато. У httpx по
# умолчанию `keepalive_expiry=5.0` (см. httpx.Limits) -- ровно столько
# же, сколько и таймаут простоя у самого uvicorn Imagine
# (`timeout_keep_alive`, тоже по умолчанию 5с, см. imagine/__main__.py).
# Между скачиванием картинки №1 (телефон получил байты, затем ЕЩЁ
# пишет их в MediaStore -- реальное время на диске) и запросом
# картинки №2 вполне может пройти больше 5с -- Imagine к тому моменту
# уже закрыл соединение как простаивающее, а httpx здесь, возможно,
# ещё не успел заметить это и пытается переиспользовать уже
# мёртвый сокет -- отсюда зависание на неопределённое время (пока не
# сработает таймаут TCP уровня ОС, что и даёт наблюдаемые "минуты"),
# а не быстрая чистая ошибка "соединение разорвано".
#
# Раз это трафик на 127.0.0.1 (Remote и Imagine всегда на одной
# машине, см. §0.3), новое TCP-соединение открывается практически
# бесплатно -- поэтому проще вообще не переиспользовать соединения
# для этого клиента (`max_keepalive_connections=0`), чем гоняться за
# синхронизацией таймаутов простоя между двумя независимыми uvicorn-
# процессами.
_client = httpx.AsyncClient(timeout=60.0, limits=httpx.Limits(max_keepalive_connections=0))

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
    # НАЙДЕННЫЙ БАГ (жалоба "загрузка Imagine иногда занимает минуты"):
    # раньше здесь стоял `app.proxy_target()`, который под капотом
    # (`_imagine_target()` в app.py) дёргает СИНХРОННЫЙ, блокирующий
    # `is_imagine_available()` (`urllib.request.urlopen`, см. её
    # докстринг в launcher/core/imagine_process.py) -- и делал это на
    # КАЖДЫЙ проксируемый запрос, то есть на каждую картинку стиля/JS/
    # CSS отдельно, а не один раз на страницу. Вызов внутри `async def`
    # без `run_in_threadpool`/`asyncio.to_thread` блокирует ВЕСЬ event
    # loop процесса Remote целиком (uvicorn однопоточный) на время
    # каждого такого запроса -- при странице с десятками картинок
    # стилей эти блокировки суммируются и последовательно "съедают"
    # секунды-минуты, плюс тормозят вообще всё остальное в Remote,
    # что крутится в этом же event loop (WebSocket, другие клиенты).
    #
    # Отдельная проверка на самом деле избыточна: строкой ниже мы и так
    # прямо сейчас пытаемся соединиться с Imagine через персистентный
    # `_client` -- если он реально недоступен, `httpx.HTTPError` ниже
    # уже ловится и превращается в понятный 502. Поэтому здесь
    # достаточно знать НАСТРОЕН ли Imagine вообще (просто чтение int из
    # памяти, без сети) -- а не опрашивать его доступность отдельным
    # HTTP-запросом на каждый суб-ресурс.
    if runtime.imagine_port is None:
        raise HTTPException(status_code=503, detail="Imagine сейчас не запущен.")
    target_base = f"http://127.0.0.1:{runtime.imagine_port}"

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
