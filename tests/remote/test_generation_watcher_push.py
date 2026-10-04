"""
Какие события GenerationWatcher отправляет в push (этап R0, раунд 2).

Реальной отправки здесь нет: tests/remote/conftest.py подменяет
fcm.send_generation_push записью в список `sent_pushes`. Раньше эти
вызовы случайно доходили до настоящего Firebase при прогоне
test_generation_watcher.py -- см. docstring conftest.py.

Тесты синхронные и сами вызывают asyncio.run(), как в соседнем
test_generation_watcher.py (pytest-asyncio в проекте нет). push уходит
через asyncio.create_task(asyncio.to_thread(...)) без ожидания. Нельзя
полагаться на то, что asyncio.run() «сам доделает» такой таск: при
выходе он его ОТМЕНЯЕТ, и если рабочий поток ещё не успел взять задачу
(на Windows поток стартует медленнее), отправка молча пропадает --
тест становится плавающим. Поэтому _tick() ниже всегда запускается
через _tick_and_drain(), которая явно дожидается всех фоновых тасков.
"""

import asyncio
from dataclasses import dataclass

import pytest

from comfyui_studio.remote import state
from comfyui_studio.remote.generation_watcher import GenerationWatcher
from comfyui_studio.remote.ws_hub import ConnectionHub


@dataclass
class _Queue:
    running: int
    pending: int
    running_ids: set


class _Hub(ConnectionHub):
    def __init__(self):
        super().__init__()
        self.events = []

    async def broadcast(self, event):
        self.events.append(event)


@pytest.fixture(autouse=True)
def _reset_runtime():
    state.runtime.comfy_host = "127.0.0.1"
    state.runtime.comfy_port = 8188
    state.runtime.imagine_port = None
    yield
    state.runtime.comfy_port = None
    state.runtime.imagine_port = None


async def _tick_and_drain(watcher):
    """Один тик + ожидание фоновых тасков (fire-and-forget push)."""
    await watcher._tick()
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def _run_one_job_to_finish(watcher):
    """Тик с запущенным заданием 'abc', затем тик, где оно пропало из running."""
    watcher._comfy_client.get_queue = lambda port: _Queue(1, 0, {"abc"})
    asyncio.run(_tick_and_drain(watcher))
    watcher._comfy_client.get_queue = lambda port: _Queue(0, 0, set())
    asyncio.run(_tick_and_drain(watcher))


def test_no_push_before_job_finishes(sent_pushes):
    watcher = GenerationWatcher(_Hub())
    watcher._comfy_client.get_queue = lambda port: _Queue(1, 1, {"abc"})
    asyncio.run(_tick_and_drain(watcher))  # started + queue.update -- это не повод для push
    assert sent_pushes == []


def test_finished_job_requests_completed_push(sent_pushes):
    hub = _Hub()
    watcher = GenerationWatcher(hub)
    _run_one_job_to_finish(watcher)  # Imagine не запущен -> completed без картинок
    assert sent_pushes == [
        {"type": "generation.completed", "prompt_id": "abc", "image_urls": []}
    ]


def test_failed_job_requests_error_push_with_message(sent_pushes):
    state.runtime.imagine_port = 7860
    watcher = GenerationWatcher(_Hub())
    watcher._fetch_imagine_status = lambda prompt_id: ("error", "нет VRAM")
    _run_one_job_to_finish(watcher)
    assert sent_pushes == [
        {"type": "generation.error", "prompt_id": "abc", "message": "нет VRAM"}
    ]


def test_push_event_matches_what_was_broadcast_over_ws(sent_pushes):
    """push дублирует WS-событие, а не строит своё -- содержимое одинаково."""
    hub = _Hub()
    watcher = GenerationWatcher(hub)
    _run_one_job_to_finish(watcher)
    broadcast = [e for e in hub.events if e["type"] == "generation.completed"]
    assert broadcast == sent_pushes
