"""
Фоновая задача Remote, поставляющая события в ConnectionHub (см.
ws_hub.py) -- этап 2 дорожной карты ("грубый realtime-статус"):

    queue.update          -- {"running": int, "pending": int}, из /queue
                              ComfyUI (тем же ComfyAPIClient, что и
                              routes/system.py и routes/queue.py этапа 1)
    generation.started    -- {"prompt_id": str}
    generation.completed  -- {"prompt_id": str, "image_urls": [str]}
    generation.error      -- {"prompt_id": str, "message": str}

ОТСТУПЛЕНИЕ ОТ ФОРМУЛИРОВКИ ДОРОЖНОЙ КАРТЫ (см. также "Как реализовано
по факту" в самом файле дорожной карты): §2 описывает источник данных
как "поллинг /api/generate/{id}/status бэкенда Imagine" -- это
предполагает, что Remote уже ЗНАЕТ, какие prompt_id опрашивать. На
этом этапе дорожной карты подать задание НА ГЕНЕРАЦИЮ через сам Remote
ещё нельзя (реестр апп и reverse-proxy -- только этап 4, Android --
этап 6), так что взять список активных prompt_id неоткуда, кроме как у
самого ComfyUI: GenerationWatcher опрашивает /queue ComfyUI (через
ComfyAPIClient.get_queue(), это и даёт как queue.update, так и
множество prompt_id, которые ПРЯМО СЕЙЧАС выполняются --
QueueState.running_ids), сам отслеживает появление/исчезновение
prompt_id из этого множества (см. _tick), и уже ЗАТЕМ, только для
собственно наступившего события "задание пропало из running" --
подтверждает его результат (готово/ошибка/картинки) через
GET /api/generate/{prompt_id}/status бэкенда Imagine -- то есть Imagine
здесь используется ровно так, как и предполагает дорожная карта, просто
"откуда взять сам prompt_id" пришлось решить самостоятельно, раз
реестра ещё нет. Если Imagine не запущен или не знает про такой
prompt_id (задание было поставлено не через него, например прямо через
ComfyUI Manager) -- честно шлём generation.completed с пустым
image_urls, а не выдумываем результат.

asyncio.to_thread() оборачивает вызовы ComfyAPIClient (обычный
блокирующий urllib, см. его докстринг) -- иначе синхронный сетевой
вызов застопорил бы весь event loop Remote-процесса, включая обработку
уже открытых WS-соединений.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request

from ..launcher.core.comfy_api import ComfyAPIClient
from . import fcm
from .state import runtime
from .ws_hub import ConnectionHub

POLL_INTERVAL_SECONDS = 1.5
IMAGINE_STATUS_TIMEOUT_SECONDS = 3.0


class GenerationWatcher:
    def __init__(self, hub: ConnectionHub) -> None:
        self._hub = hub
        self._comfy_client = ComfyAPIClient()
        # prompt_id заданий, которые в ПРЕДЫДУЩЕМ опросе числились
        # выполняющимися -- сравнение с текущим running_ids на каждом
        # тике и даёт события generation.started/generation.completed
        # (см. _tick).
        self._tracked_running: set[str] = set()
        self._last_queue_counts: tuple[int, int] | None = None
        self._stopped = False

    async def run(self) -> None:
        """Бесконечный цикл опроса -- запускается один раз при старте
        приложения (см. lifespan в app.py) и живёт, пока жив процесс
        Remote. Ошибка одного тика не должна убивать весь фоновый
        таск -- иначе один неожиданный сбой навсегда останавливает
        realtime-события до перезапуска всего процесса Remote."""
        while not self._stopped:
            try:
                await self._tick()
            except Exception:
                # Осознанно не логируем через `log` из launcher/core --
                # Remote-процесс использует стандартный логгер uvicorn,
                # а не Qt-логгер лаунчера (это отдельный процесс, см.
                # __init__.py пакета). uvicorn сам выводит трейсбек
                # необработанных исключений в свой лог, если понадобится
                # отладка -- здесь достаточно не дать одному сбою
                # остановить весь цикл.
                pass
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    def stop(self) -> None:
        self._stopped = True

    async def _tick(self) -> None:
        if runtime.comfy_port is None:
            return
        state = await asyncio.to_thread(self._comfy_client.get_queue, runtime.comfy_port)
        if state is None:
            return

        counts = (state.running, state.pending)
        if counts != self._last_queue_counts:
            self._last_queue_counts = counts
            await self._hub.broadcast(
                {"type": "queue.update", "running": state.running, "pending": state.pending}
            )

        current_running = set(state.running_ids)
        newly_started = current_running - self._tracked_running
        finished = self._tracked_running - current_running
        self._tracked_running = current_running

        for prompt_id in newly_started:
            await self._hub.broadcast({"type": "generation.started", "prompt_id": prompt_id})

        for prompt_id in finished:
            await self._resolve_finished(prompt_id)

    async def _resolve_finished(self, prompt_id: str) -> None:
        result = await asyncio.to_thread(self._fetch_imagine_status, prompt_id)
        if result is None:
            # Imagine не запущен, недоступен, или не знает про этот
            # prompt_id (задание поставлено не через него) -- честно
            # говорим "готово", но без картинок, а не выдумываем URL.
            event = {"type": "generation.completed", "prompt_id": prompt_id, "image_urls": []}
            await self._hub.broadcast(event)
            self._notify_push(event)
            return
        state, payload = result
        if state == "error":
            event = {"type": "generation.error", "prompt_id": prompt_id, "message": payload}
            await self._hub.broadcast(event)
            self._notify_push(event)
        else:
            event = {"type": "generation.completed", "prompt_id": prompt_id, "image_urls": payload}
            await self._hub.broadcast(event)
            self._notify_push(event)

    def _notify_push(self, event: dict) -> None:
        """НОВОЕ (§Этап 6.5, "Push-уведомления"): дублирует то же
        событие в FCM для устройств, у которых нет открытого WS-
        соединения прямо сейчас (свёрнутое приложение/выключенный
        экран -- см. докстринг fcm.py). asyncio.create_task, а не
        await -- настоящий сетевой вызов к Google (внутри
        send_generation_push, через asyncio.to_thread) не должен
        задерживать следующий тик опроса очереди ComfyUI; сама функция
        никогда не бросает исключений наружу (см. её докстринг), так
        что здесь не нужен ни try/except, ни обработка результата таска."""
        asyncio.create_task(asyncio.to_thread(fcm.send_generation_push, event))

    def _fetch_imagine_status(self, prompt_id: str):
        """Синхронный (вызывается через asyncio.to_thread) поход в
        GET /api/generate/{prompt_id}/status бэкенда Imagine (см.
        comfyui_studio/imagine/backend/main.py). Возвращает
        ("done", [urls]) / ("error", message) / None (Imagine
        недоступен или ответил не тем, что ожидалось)."""
        if runtime.imagine_port is None:
            return None
        url = f"http://127.0.0.1:{runtime.imagine_port}/api/generate/{prompt_id}/status"
        try:
            with urllib.request.urlopen(url, timeout=IMAGINE_STATUS_TIMEOUT_SECONDS) as resp:
                data = json.loads(resp.read())
        except (urllib.error.URLError, ValueError, OSError):
            return None

        state = data.get("state")
        if state == "done":
            # ЭТАП 4 дорожной карты Remote добавил reverse-proxy
            # (imagine_proxy.py, `/apps/imagine/*`) -- используем его
            # вместо прямого адреса Imagine. Раньше здесь стоял
            # `http://127.0.0.1:{imagine_port}{img['url']}` (см. историю
            # этого файла) -- это было временным решением ДО этапа 4,
            # и вдобавок содержало отдельный скрытый баг: жёстко
            # прошитый "127.0.0.1" означает "loopback ТОГО устройства,
            # которое обрабатывает этот адрес" -- на телефоне это сам
            # телефон, а не ПК со Studio, так что такая ссылка на
            # реальном LAN никогда не открылась бы. Путь ниже --
            # КОРНЕВОЙ ОТНОСИТЕЛЬНЫЙ (с ведущим "/", но без host:port),
            # поэтому клиент резолвит его от адреса, на котором уже
            # открыт сам Remote (WS-соединение и так уже туда
            # подключено) -- и в LAN, и на localhost одинаково корректно.
            # `img['url']` от Imagine (см. imagine/backend/main.py) --
            # уже БЕЗ ведущего "/" (относительный путь для случая, когда
            # страница Imagine открыта не через прокси, см. комментарий
            # там же) -- здесь добавляем "/apps/imagine/" сами.
            urls = [
                f"/apps/imagine/{img['url']}"
                for img in data.get("images", [])
                if "url" in img
            ]
            return "done", urls
        if state == "error":
            return "error", "Генерация завершилась с ошибкой -- подробности в логе ComfyUI."
        # "running"/"pending"/"unknown" здесь не ожидаются (prompt_id
        # только что пропал из running_ids ComfyUI -- по идее уже должен
        # быть либо в истории, либо ошибиться), но если ComfyUI и Imagine
        # на секунду разошлись во мнениях -- лучше промолчать в этом
        # тике, чем соврать про результат; следующий тик, если prompt_id
        # ещё раз всплывёт как running, снова добавит его в отслеживание.
        return None
