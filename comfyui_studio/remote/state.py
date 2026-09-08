"""
Параметры запуска ЭТОГО процесса Remote, переданные через CLI (см.
__main__.py) -- аналог того, как comfyui_studio/imagine/backend/
config_store.py сеет comfyui.host/port из переменных окружения при
запуске из Studio (см. imagine/__main__.py, _seed_comfy_target_from_env).

Здесь -- простой модуль-синглтон вместо протаскивания через
`request.app.state` FastAPI везде по цепочке вызовов: значения не
меняются после старта процесса (в отличие от request-scoped данных,
которым как раз место в app.state), и без Studio (самостоятельный
запуск Remote, если он когда-нибудь понадобится отдельно от лаунчера)
comfy_port/imagine_port просто остаются None -- статус-эндпоинт тогда
честно отвечает comfyui_running=False/imagine_running=False, а не
падает.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class RemoteRuntimeState:
    comfy_host: str = "127.0.0.1"
    comfy_port: Optional[int] = None
    imagine_port: Optional[int] = None
    # НОВОЕ (этап 5 дорожной карты, mDNS): адрес/порт, на которых слушает
    # САМ Remote (а не ComfyUI/Imagine, как поля выше) -- нужны mdns.py,
    # чтобы решить, стоит ли вообще объявлять сервис (см. его докстринг:
    # рекламировать 127.0.0.1 бессмысленно, до него не достучаться по
    # сети), и что именно объявлять (порт).
    host: str = "127.0.0.1"
    port: Optional[int] = None


runtime = RemoteRuntimeState()
