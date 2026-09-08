"""Сборка FastAPI-приложения верхнего уровня Remote (§0.1 дорожной
карты) -- по одному include_router() на модуль из routes/. Отдельно от
__main__.py, чтобы `app` можно было импортировать напрямую в тестах
(httpx.AsyncClient/TestClient) без поднятия настоящего uvicorn-сервера.

Этап 2 добавил lifespan: фоновая задача GenerationWatcher (см.
generation_watcher.py) должна крутиться всё время, пока жив процесс
Remote, независимо от того, подключён ли прямо сейчас хоть один
WS-клиент (см. её докстринг про "источник данных" и §2 дорожной
карты) -- запускается при старте приложения и корректно отменяется при
остановке, а не оставляется висеть/утекать.

Этап 4 добавил в тот же lifespan регистрацию Imagine в apps_registry.py
(см. её докстринг) -- ЗДЕСЬ, а не где-то ближе к CLI-парсингу в
__main__.py, потому что к моменту запуска lifespan `runtime.imagine_port`
уже точно выставлен (main() в __main__.py делает это до импорта .app,
см. его докстринг), а сама регистрация -- часть инициализации именно
ASGI-приложения, концептуально рядом с GenerationWatcher, а не с
разбором аргументов командной строки.

Этап 5 добавил туда же mDNS-объявление (см. mdns.py) -- тот же принцип:
запускается/останавливается вместе с остальным содержимым lifespan.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from ..launcher.core.imagine_process import is_imagine_available
from . import app_launcher
from . import mdns as mdns_module
from .apps_registry import RemoteApp, register_app
from .generation_watcher import GenerationWatcher
from .imagine_proxy import router as imagine_proxy_router
from .routes import apps, devices, fcm as fcm_routes, internal, pairing_routes, queue, system, ws
from .routes import home as home_routes
from .state import runtime
from .ws_hub import hub

API_PREFIX = "/api/v1/remote"


def _register_known_apps() -> None:
    if runtime.imagine_port is None:
        # Imagine не настроен для этого запуска Remote (--imagine-port
        # не передан, см. __main__.py) -- значит, Studio в принципе не
        # знает, на каком порту его искать, и регистрировать нечего.
        # Это НЕ то же самое, что "Imagine сейчас не запущен" (тот
        # случай покрыт proxy_target() ниже, возвращающим None) --
        # разница в том, знаем ли мы порт вообще, а не только его
        # текущую доступность.
        return

    def _imagine_target():
        if is_imagine_available(runtime.imagine_port):
            return f"http://127.0.0.1:{runtime.imagine_port}"
        return None

    register_app(
        RemoteApp(
            id="imagine",
            name="Imagine",
            path="/apps/imagine/",
            proxy_target=_imagine_target,
            # НОВОЕ (этап "Запуск приложений через Remote"): раньше
            # регистрация ограничивалась одним proxy_target -- телефон
            # мог только проверить, запущен ли Imagine, но не запустить
            # его сам (см. app_launcher.py про то, почему это безопасно
            # переиспользует уже существующий Qt-независимый механизм
            # запуска лаунчера).
            status_fn=app_launcher.imagine_status,
            start_fn=app_launcher.start_imagine,
            error_fn=app_launcher.imagine_last_error,
        )
    )


def _studio_version() -> str:
    from comfyui_studio import __version__
    return __version__


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    _register_known_apps()
    watcher = GenerationWatcher(hub)
    task = asyncio.create_task(watcher.run())
    advertiser = mdns_module.MdnsAdvertiser()
    await advertiser.start(runtime.host, runtime.port, _studio_version())
    try:
        yield
    finally:
        await advertiser.stop()
        watcher.stop()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title="ComfyUI Studio Remote",
    version="0.1.0",
    description=(
        "Remote API ComfyUI Studio -- см. ComfyUIStudio_Remote_Roadmap.md. "
        "Этап 1: pairing, токены устройств, базовые статус/очередь-"
        "эндпоинты. Этап 2: realtime-события через WebSocket "
        "(/api/v1/remote/ws). Этап 3: пошаговый прогресс генераций. "
        "Этап 4: реестр приложений (/apps) и reverse-proxy к Imagine "
        "(/apps/imagine/*), домашняя страница терминала (/). Этап 5: "
        "mDNS-объявление в локальной сети (см. mdns.py). Этот "
        "процесс не предназначен для публичного интернета -- см. §0/§9 "
        "дорожной карты (LAN/VPN)."
    ),
    lifespan=_lifespan,
)

app.include_router(system.router, prefix=API_PREFIX, tags=["system"])
app.include_router(pairing_routes.router, prefix=API_PREFIX, tags=["pairing"])
app.include_router(devices.router, prefix=API_PREFIX, tags=["devices"])
app.include_router(apps.router, prefix=API_PREFIX, tags=["apps"])
app.include_router(queue.router, prefix=API_PREFIX, tags=["queue"])
app.include_router(ws.router, prefix=API_PREFIX, tags=["realtime"])
app.include_router(internal.router, prefix=API_PREFIX, tags=["internal"])
app.include_router(fcm_routes.router, prefix=API_PREFIX, tags=["fcm"])

# Без префикса /api/v1/remote -- это "терминальные" маршруты для
# браузера/WebView, а не REST-контракт для нативного клиента (см. §0.3
# и докстринги routes/home.py, imagine_proxy.py).
app.include_router(home_routes.router, tags=["terminal"])
app.include_router(imagine_proxy_router, tags=["terminal"])
