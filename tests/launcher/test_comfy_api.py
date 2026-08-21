"""
Unit-тесты comfyui_studio.launcher.core.comfy_api (этап 6 дорожной карты
рефакторинга — HTTP API abstraction).

Покрывают чистую логику без Qt: свободные функции, оставшиеся с
момента этапа 1 (count_steps_in_prompt, fetch_queue_status,
fetch_history_ids, is_port_open — см. план этапа 0, где они были
названы первыми кандидатами на unit-тесты до переноса), и новый класс
ComfyAPIClient поверх них.

Сеть везде замокана через monkeypatch на urllib.request.urlopen —
никаких реальных HTTP-запросов и никакого настоящего ComfyUI тесты не
требуют.
"""

import json
import urllib.error
from io import BytesIO

from comfyui_studio.launcher.core.comfy_api import (
    ComfyAPIClient,
    QueueState,
    SystemStats,
    count_steps_in_prompt,
    fetch_history_ids,
    fetch_queue_status,
    is_port_open,
)


class _FakeResponse:
    """Достаточно urlopen-совместимого поведения для наших нужд:
    поддержка контекстного менеджера и .read()."""

    def __init__(self, payload):
        self._buf = BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._buf.read()


def _urlopen_returning(payload):
    def _fake(url, timeout=None):
        return _FakeResponse(payload)

    return _fake


def _urlopen_raising(exc):
    def _fake(url, timeout=None):
        raise exc

    return _fake


# --------------------------------------------------------------------------
# count_steps_in_prompt — чистая логика без сети вообще
# --------------------------------------------------------------------------

def test_count_steps_in_prompt_sums_known_keys():
    prompt = {
        "1": {"class_type": "KSampler", "inputs": {"steps": 20}},
        "2": {"class_type": "KSamplerAdvanced", "inputs": {"sampling_steps": 10}},
        "3": {"class_type": "CheckpointLoader", "inputs": {"ckpt_name": "foo.safetensors"}},
    }
    assert count_steps_in_prompt(prompt) == 30


def test_count_steps_in_prompt_ignores_non_numeric_and_bool():
    prompt = {
        "1": {"inputs": {"steps": ["4", 0]}},  # ссылка на другой узел — не число
        "2": {"inputs": {"steps": True}},  # bool — не считаем как число шагов
    }
    assert count_steps_in_prompt(prompt) == 0


def test_count_steps_in_prompt_empty_or_none():
    assert count_steps_in_prompt({}) == 0
    assert count_steps_in_prompt(None) == 0


# --------------------------------------------------------------------------
# is_port_open / fetch_queue_status / fetch_history_ids
# --------------------------------------------------------------------------

def test_is_port_open_true(monkeypatch):
    monkeypatch.setattr(
        "comfyui_studio.launcher.core.comfy_api.urllib.request.urlopen",
        lambda url, timeout=None: _FakeResponse({}),
    )
    assert is_port_open(8188) is True


def test_is_port_open_false_on_url_error(monkeypatch):
    monkeypatch.setattr(
        "comfyui_studio.launcher.core.comfy_api.urllib.request.urlopen",
        _urlopen_raising(urllib.error.URLError("connection refused")),
    )
    assert is_port_open(8188) is False


def test_fetch_queue_status_shapes_result(monkeypatch):
    payload = {
        "queue_running": [[0, "pid-running", {"1": {"inputs": {"steps": 20}}}]],
        "queue_pending": [[0, "pid-pending", {"1": {"inputs": {"steps": 15}}}]],
    }
    monkeypatch.setattr(
        "comfyui_studio.launcher.core.comfy_api.urllib.request.urlopen",
        _urlopen_returning(payload),
    )
    result = fetch_queue_status(8188)
    assert result == {
        "running": 1,
        "pending": 1,
        "running_ids": {"pid-running"},
        "step_totals": {"pid-running": 20, "pid-pending": 15},
    }


def test_fetch_queue_status_none_on_failure(monkeypatch):
    monkeypatch.setattr(
        "comfyui_studio.launcher.core.comfy_api.urllib.request.urlopen",
        _urlopen_raising(OSError("boom")),
    )
    assert fetch_queue_status(8188) is None


def test_fetch_history_ids(monkeypatch):
    monkeypatch.setattr(
        "comfyui_studio.launcher.core.comfy_api.urllib.request.urlopen",
        _urlopen_returning({"pid-a": {}, "pid-b": {}}),
    )
    assert fetch_history_ids(8188) == {"pid-a", "pid-b"}


def test_fetch_history_ids_none_on_failure(monkeypatch):
    monkeypatch.setattr(
        "comfyui_studio.launcher.core.comfy_api.urllib.request.urlopen",
        _urlopen_raising(OSError("boom")),
    )
    assert fetch_history_ids(8188) is None


# --------------------------------------------------------------------------
# ComfyAPIClient
# --------------------------------------------------------------------------

def test_client_get_queue_returns_dataclass(monkeypatch):
    payload = {
        "queue_running": [[0, "pid-1", {"1": {"inputs": {"steps": 5}}}]],
        "queue_pending": [],
    }
    monkeypatch.setattr(
        "comfyui_studio.launcher.core.comfy_api.urllib.request.urlopen",
        _urlopen_returning(payload),
    )
    client = ComfyAPIClient(port=8188)
    state = client.get_queue()
    assert isinstance(state, QueueState)
    assert state.running == 1
    assert state.pending == 0
    assert state.running_ids == {"pid-1"}
    assert state.step_totals == {"pid-1": 5}


def test_client_port_override_beats_constructor_port(monkeypatch):
    seen_urls = []

    def _fake(url, timeout=None):
        seen_urls.append(url)
        return _FakeResponse({"queue_running": [], "queue_pending": []})

    monkeypatch.setattr(
        "comfyui_studio.launcher.core.comfy_api.urllib.request.urlopen", _fake
    )
    client = ComfyAPIClient(port=8188)
    client.get_queue(port=9999)
    assert seen_urls == ["http://127.0.0.1:9999/queue"]


def test_client_returns_none_without_any_port():
    client = ComfyAPIClient()  # ни в конструкторе, ни в вызове порта нет
    assert client.get_queue() is None
    assert client.get_history_ids() is None
    assert client.get_history() is None
    assert client.get_system_stats() is None
    assert client.get_current_workflow() is None
    assert client.get_object_info() is None
    assert client.is_available() is False


def test_client_get_history_wraps_entries_with_id(monkeypatch):
    payload = {
        "pid-1": {"status": {"completed": True}, "outputs": {}},
        "pid-2": {"status": {"completed": True}, "outputs": {}},
    }
    monkeypatch.setattr(
        "comfyui_studio.launcher.core.comfy_api.urllib.request.urlopen",
        _urlopen_returning(payload),
    )
    client = ComfyAPIClient(port=8188)
    entries = client.get_history()
    assert {e["id"] for e in entries} == {"pid-1", "pid-2"}
    assert all("status" in e for e in entries)


def test_client_get_history_respects_limit(monkeypatch):
    payload = {f"pid-{i}": {"status": {}} for i in range(5)}
    monkeypatch.setattr(
        "comfyui_studio.launcher.core.comfy_api.urllib.request.urlopen",
        _urlopen_returning(payload),
    )
    client = ComfyAPIClient(port=8188)
    entries = client.get_history(limit=2)
    assert len(entries) == 2


def test_client_get_system_stats(monkeypatch):
    payload = {
        "system": {
            "os": "nt",
            "python_version": "3.11.5",
            "comfyui_version": "0.3.10",
            "embedded_python": True,
        },
        "devices": [{"name": "NVIDIA RTX", "type": "cuda", "index": 0}],
    }
    monkeypatch.setattr(
        "comfyui_studio.launcher.core.comfy_api.urllib.request.urlopen",
        _urlopen_returning(payload),
    )
    client = ComfyAPIClient(port=8188)
    stats = client.get_system_stats()
    assert isinstance(stats, SystemStats)
    assert stats.os == "nt"
    assert stats.comfyui_version == "0.3.10"
    assert stats.devices == [{"name": "NVIDIA RTX", "type": "cuda", "index": 0}]
    assert stats.raw == payload


def test_client_get_system_stats_tolerates_missing_fields(monkeypatch):
    # Схема /system_stats не проверялась вживую (см. докстринг
    # SystemStats) — важно, чтобы клиент не падал на неполном/другом
    # ответе, а просто оставлял поля пустыми.
    monkeypatch.setattr(
        "comfyui_studio.launcher.core.comfy_api.urllib.request.urlopen",
        _urlopen_returning({}),
    )
    client = ComfyAPIClient(port=8188)
    stats = client.get_system_stats()
    assert stats.os is None
    assert stats.devices == []


def test_client_get_current_workflow_single_running(monkeypatch):
    graph = {"1": {"class_type": "KSampler", "inputs": {"steps": 20}}}
    payload = {"queue_running": [[0, "pid-1", graph]], "queue_pending": []}
    monkeypatch.setattr(
        "comfyui_studio.launcher.core.comfy_api.urllib.request.urlopen",
        _urlopen_returning(payload),
    )
    client = ComfyAPIClient(port=8188)
    assert client.get_current_workflow() == graph


def test_client_get_current_workflow_none_when_ambiguous(monkeypatch):
    graph = {"1": {}}
    payload = {
        "queue_running": [[0, "pid-1", graph], [0, "pid-2", graph]],
        "queue_pending": [],
    }
    monkeypatch.setattr(
        "comfyui_studio.launcher.core.comfy_api.urllib.request.urlopen",
        _urlopen_returning(payload),
    )
    client = ComfyAPIClient(port=8188)
    # Два одновременно running — неоднозначно, чей граф вернуть.
    assert client.get_current_workflow() is None


def test_client_get_current_workflow_none_when_idle(monkeypatch):
    monkeypatch.setattr(
        "comfyui_studio.launcher.core.comfy_api.urllib.request.urlopen",
        _urlopen_returning({"queue_running": [], "queue_pending": []}),
    )
    client = ComfyAPIClient(port=8188)
    assert client.get_current_workflow() is None


def test_client_get_object_info_all_nodes(monkeypatch):
    seen_urls = []

    def _fake(url, timeout=None):
        seen_urls.append(url)
        return _FakeResponse({"KSampler": {"input": {}, "output": []}})

    monkeypatch.setattr(
        "comfyui_studio.launcher.core.comfy_api.urllib.request.urlopen", _fake
    )
    client = ComfyAPIClient(port=8188)
    info = client.get_object_info()
    assert "KSampler" in info
    assert seen_urls == ["http://127.0.0.1:8188/object_info"]


def test_client_get_object_info_single_class(monkeypatch):
    seen_urls = []

    def _fake(url, timeout=None):
        seen_urls.append(url)
        return _FakeResponse({"KSampler": {"input": {}, "output": []}})

    monkeypatch.setattr(
        "comfyui_studio.launcher.core.comfy_api.urllib.request.urlopen", _fake
    )
    client = ComfyAPIClient(port=8188)
    client.get_object_info(node_class="KSampler")
    assert seen_urls == ["http://127.0.0.1:8188/object_info/KSampler"]


def test_client_is_available(monkeypatch):
    monkeypatch.setattr(
        "comfyui_studio.launcher.core.comfy_api.urllib.request.urlopen",
        lambda url, timeout=None: _FakeResponse({}),
    )
    client = ComfyAPIClient(port=8188)
    assert client.is_available() is True


def test_client_is_available_false_on_error(monkeypatch):
    monkeypatch.setattr(
        "comfyui_studio.launcher.core.comfy_api.urllib.request.urlopen",
        _urlopen_raising(urllib.error.URLError("refused")),
    )
    client = ComfyAPIClient(port=8188)
    assert client.is_available() is False
