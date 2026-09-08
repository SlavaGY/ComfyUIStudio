"""
Фоновая задача бэкенда Imagine, реализующая этап 3 дорожной карты
Remote ("Пошаговый прогресс", ComfyUIStudio_Remote_Roadmap.md):
держит одно WS-соединение с ComfyUI (`ws://<comfy_host>:<comfy_port>/ws
?clientId=<PROCESS_CLIENT_ID>`) и пересылает события "progress" в
Remote как `generation.progress`, чтобы телефон видел прогресс
конкретного шага, а не только "идёт генерация" (это уже даёт этап 2).

ПОЧЕМУ ИМЕННО ЗДЕСЬ, А НЕ В САМОМ REMOTE: ComfyUI шлёт progress/
executing/executed/execution_error ТОЛЬКО тому WS-сокету, чей
`clientId` совпадает с `client_id`, которым конкретное задание было
поставлено в очередь (`POST /prompt`) -- см. подробный разбор в
comfyui_studio/launcher/core/comfy_ws.py (докстринг модуля, раздел
"ОКОНЧАТЕЛЬНЫЙ ОТВЕТ"). Задания, которые видит телефон, ставит в
очередь именно Imagine (`ComfyClient.queue_prompt()`, см.
comfy_client.py), поэтому единственный способ реально получать
progress для них -- открыть WS ИМЕННО с тем же `client_id`, каким
Imagine их и ставит (см. `ComfyClient.PROCESS_CLIENT_ID` -- то же
значение, что и `ComfyClient(...).client_id` у любого экземпляра в
этом процессе). Если бы этот WS-клиент жил в самом Remote (со своим,
отдельным client_id, как и пытался в своё время ResourceMonitor
лаунчера) -- он точно так же не получил бы ни одного progress-события,
см. ту же самую находку в comfy_ws.py.

Пересылка в Remote -- обычный fire-and-forget HTTP POST на
`/api/v1/remote/internal/generation-progress` (эндпоинт защищён
проверкой "только с 127.0.0.1", см. remote/local_guard.py -- то же
самое разделение "локальный вызов от Studio-процесса", что и у pairing/
devices, только теперь вызывающая сторона -- не Qt-настройки, а другой
Studio-процесс). Best-effort: если Remote не запущен или порт не
передан (--remote-port не задан при старте Imagine -- см. __main__.py),
события просто не пересылаются никуда, сам Imagine продолжает работать
как обычно -- этот модуль ни на что не влияет, кроме собственно
пересылки (см. §0/§3 дорожной карты: этап 3 опционален и не блокирует
MVP).
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request

import websockets

from . import config_store
from .comfy_client import PROCESS_CLIENT_ID

log = logging.getLogger("imagine")

RECONNECT_INITIAL_SECONDS = 2.0
RECONNECT_MAX_SECONDS = 15.0
FORWARD_TIMEOUT_SECONDS = 2.0

# Порт Remote, куда пересылать события -- см. __main__.py (--remote-port,
# читается один раз при старте процесса, как и IMAGINE_COMFY_HOST/PORT
# чуть выше в backend/main.py; Remote может быть включён/выключен позже
# в настройках без перезапуска Imagine -- тогда пересылка просто не
# долетает, пока Imagine не перезапустят с новым значением, см. докстринг
# модуля про best-effort).
remote_port: int | None = None


async def run_forever() -> None:
    """Бесконечный цикл подключения с нарастающей паузой между попытками
    (тот же принцип, что и RECONNECT_INITIAL_MS/MAX в comfy_ws.py, здесь
    -- в asyncio, а не в Qt-таймере). Отсутствие ComfyUI/недоступность
    сети -- ожидаемая, не разовая ситуация (ComfyUI ещё не запущен,
    например), поэтому переподключение не прекращается никогда, пока жив
    сам процесс Imagine."""
    delay = RECONNECT_INITIAL_SECONDS
    while True:
        try:
            await _connect_and_forward()
            delay = RECONNECT_INITIAL_SECONDS  # успешное соединение -- сброс паузы
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.debug("WS-соединение прогресса с ComfyUI прервано: %s", exc)
        await asyncio.sleep(delay)
        delay = min(delay * 2, RECONNECT_MAX_SECONDS)


async def _connect_and_forward() -> None:
    cfg = config_store.load_config()
    comfy_cfg = cfg.get("comfyui", {})
    host = comfy_cfg.get("host", "127.0.0.1")
    port = comfy_cfg.get("port")
    if not port:
        # ComfyUI ещё не настроен -- не ошибка, просто ждём следующей
        # попытки (host/port могут появиться, например, если Imagine
        # запущен раньше, чем Studio успела поднять ComfyUI).
        return

    url = f"ws://{host}:{port}/ws?clientId={PROCESS_CLIENT_ID}"
    async with websockets.connect(url, open_timeout=5) as ws:
        log.info("Прогресс: WS-соединение с ComfyUI установлено (%s)", url)
        async for raw_message in ws:
            if isinstance(raw_message, bytes):
                # Бинарные фреймы -- превью-изображения (см. тот же
                # формат в comfy_ws.py) -- этому модулю нужны только
                # текстовые JSON-события, превью не разбираем и не
                # пересылаем (не входит в этап 3).
                continue
            _handle_text_message(raw_message)


def _handle_text_message(raw_message: str) -> None:
    try:
        message = json.loads(raw_message)
    except ValueError:
        return
    if not isinstance(message, dict):
        return

    if message.get("type") != "progress":
        # "executing"/"executed"/"execution_error" тоже приходят на этот
        # же сокет, но этап 3 дорожной карты просит только
        # generation.progress -- generation.completed/generation.error
        # уже покрыты этапом 2 (опрос /api/generate/{id}/status Imagine
        # изнутри Remote, см. generation_watcher.py), дублировать их
        # ещё и отсюда незачем.
        return

    data = message.get("data") or {}
    prompt_id = data.get("prompt_id")
    value = data.get("value")
    max_value = data.get("max")
    if prompt_id is None or value is None or max_value is None:
        return

    _forward_to_remote(prompt_id, value, max_value)


def _forward_to_remote(prompt_id: str, value: int, max_value: int) -> None:
    if remote_port is None:
        return
    url = f"http://127.0.0.1:{remote_port}/api/v1/remote/internal/generation-progress"
    payload = json.dumps(
        {"prompt_id": prompt_id, "value": value, "max": max_value}
    ).encode("utf-8")
    request = urllib.request.Request(
        url, data=payload, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=FORWARD_TIMEOUT_SECONDS):
            pass
    except (urllib.error.URLError, OSError):
        # Remote не запущен / порт неверный / временная сеть -- см.
        # докстринг модуля про best-effort, никакого повторного
        # логирования на каждое отдельное progress-событие (их может
        # быть десятки в секунду) -- иначе лог Imagine быстро
        # захлебнётся одинаковыми строками.
        pass
