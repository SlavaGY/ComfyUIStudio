"""
Тесты comfyui_studio/remote/generation_watcher.py -- чистая логика
(diff множеств running_ids между тиками), без реального FastAPI/
uvicorn/сети. ComfyAPIClient.get_queue и поход к Imagine подменяются
напрямую -- GenerationWatcher вызывает их через asyncio.to_thread(),
поэтому подмена простой синхронной функцией/методом работает без
дополнительной обвязки.

Тестовые функции синхронные (не `async def`) и сами оборачивают вызовы
в asyncio.run() -- в проекте нет pytest-asyncio/anyio (см. dev-группу
pyproject.toml, там только pytest-qt), заводить новую тестовую
зависимость ради всего пяти тестов одного модуля не стали.
"""

import asyncio
from dataclasses import dataclass

import pytest

from comfyui_studio.remote import state
from comfyui_studio.remote.generation_watcher import GenerationWatcher
from comfyui_studio.remote.ws_hub import ConnectionHub


@dataclass
class FakeQueueState:
    running: int
    pending: int
    running_ids: set


class RecordingHub(ConnectionHub):
    def __init__(self):
        super().__init__()
        self.events = []

    async def broadcast(self, event):
        self.events.append(event)


@pytest.fixture(autouse=True)
def reset_runtime():
    # state.runtime -- модуль-синглтон (см. его докстринг), общий на
    # процесс -- сбрасываем между тестами, чтобы один тест не подсовывал
    # comfy_port/imagine_port следующему.
    state.runtime.comfy_host = "127.0.0.1"
    state.runtime.comfy_port = None
    state.runtime.imagine_port = None
    yield
    state.runtime.comfy_port = None
    state.runtime.imagine_port = None


def test_tick_without_comfy_port_does_nothing():
    hub = RecordingHub()
    watcher = GenerationWatcher(hub)
    # comfy_port не задан -- _tick должен молча выйти, а не упасть.
    asyncio.run(watcher._tick())
    assert hub.events == []


def test_tick_emits_queue_update_only_on_change():
    hub = RecordingHub()
    watcher = GenerationWatcher(hub)
    state.runtime.comfy_port = 8188

    watcher._comfy_client.get_queue = lambda port: FakeQueueState(1, 2, set())
    asyncio.run(watcher._tick())
    asyncio.run(watcher._tick())  # то же самое состояние -- без дублирования события

    queue_events = [e for e in hub.events if e["type"] == "queue.update"]
    assert queue_events == [{"type": "queue.update", "running": 1, "pending": 2}]


def test_new_running_prompt_emits_started():
    hub = RecordingHub()
    watcher = GenerationWatcher(hub)
    state.runtime.comfy_port = 8188

    watcher._comfy_client.get_queue = lambda port: FakeQueueState(1, 0, {"abc"})
    asyncio.run(watcher._tick())

    started = [e for e in hub.events if e["type"] == "generation.started"]
    assert started == [{"type": "generation.started", "prompt_id": "abc"}]


def test_finished_prompt_without_imagine_completes_with_no_images():
    hub = RecordingHub()
    watcher = GenerationWatcher(hub)
    state.runtime.comfy_port = 8188
    state.runtime.imagine_port = None  # Imagine не запущен

    watcher._comfy_client.get_queue = lambda port: FakeQueueState(1, 0, {"abc"})
    asyncio.run(watcher._tick())
    watcher._comfy_client.get_queue = lambda port: FakeQueueState(0, 0, set())
    asyncio.run(watcher._tick())

    completed = [e for e in hub.events if e["type"] == "generation.completed"]
    assert completed == [
        {"type": "generation.completed", "prompt_id": "abc", "image_urls": []}
    ]


def test_finished_prompt_with_imagine_done_includes_proxied_image_urls():
    hub = RecordingHub()
    watcher = GenerationWatcher(hub)
    state.runtime.comfy_port = 8188
    state.runtime.imagine_port = 7860
    # image_urls -- корневые ОТНОСИТЕЛЬНЫЕ пути через reverse-proxy
    # Remote (/apps/imagine/..., этап 4 дорожной карты), НЕ абсолютные
    # ссылки на порт Imagine напрямую -- см. подробное объяснение в
    # _fetch_imagine_status (жёстко прошитый "127.0.0.1" на реальном
    # LAN означал бы loopback телефона, а не ПК со Studio).
    watcher._fetch_imagine_status = lambda prompt_id: (
        "done",
        ["/apps/imagine/api/image?filename=out.png&subfolder=&type=output"],
    )

    watcher._comfy_client.get_queue = lambda port: FakeQueueState(1, 0, {"abc"})
    asyncio.run(watcher._tick())
    watcher._comfy_client.get_queue = lambda port: FakeQueueState(0, 0, set())
    asyncio.run(watcher._tick())

    completed = [e for e in hub.events if e["type"] == "generation.completed"]
    assert completed == [
        {
            "type": "generation.completed",
            "prompt_id": "abc",
            "image_urls": [
                "/apps/imagine/api/image?filename=out.png&subfolder=&type=output"
            ],
        }
    ]


def test_finished_prompt_with_imagine_error_emits_generation_error():
    hub = RecordingHub()
    watcher = GenerationWatcher(hub)
    state.runtime.comfy_port = 8188
    state.runtime.imagine_port = 7860
    watcher._fetch_imagine_status = lambda prompt_id: ("error", "что-то пошло не так")

    watcher._comfy_client.get_queue = lambda port: FakeQueueState(1, 0, {"abc"})
    asyncio.run(watcher._tick())
    watcher._comfy_client.get_queue = lambda port: FakeQueueState(0, 0, set())
    asyncio.run(watcher._tick())

    errors = [e for e in hub.events if e["type"] == "generation.error"]
    assert errors == [
        {"type": "generation.error", "prompt_id": "abc", "message": "что-то пошло не так"}
    ]


def test_fetch_imagine_status_builds_proxied_relative_urls(monkeypatch):
    """Проверяет саму сборку URL внутри _fetch_imagine_status (в отличие
    от тестов выше, где этот метод подменяется целиком) -- именно тут
    раньше пряталась ошибка конкатенации после перехода img['url'] у
    Imagine на относительный путь (без ведущего "/", см.
    imagine/backend/main.py): "http://host:port" + "api/image?..." без
    разделителя дало бы "http://host:portapi/image?...". Через
    /apps/imagine/ конкатенация другая (см. сам метод), но регрессию
    такого рода стоит ловить явным тестом, а не полагаться только на
    ручную проверку при живом тестировании."""
    hub = RecordingHub()
    watcher = GenerationWatcher(hub)
    state.runtime.imagine_port = 7860

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            import json

            return json.dumps(
                {
                    "state": "done",
                    "images": [
                        {"url": "api/image?filename=out.png&subfolder=&type=output"}
                    ],
                }
            ).encode("utf-8")

    monkeypatch.setattr(
        "urllib.request.urlopen", lambda url, timeout=None: FakeResponse()
    )

    result = watcher._fetch_imagine_status("abc")

    assert result == (
        "done",
        ["/apps/imagine/api/image?filename=out.png&subfolder=&type=output"],
    )
