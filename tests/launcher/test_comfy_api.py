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

import pytest

from comfyui_studio.launcher.core.comfy_api import (
    ComfyAPIClient,
    QueueState,
    SystemStats,
    count_history_outputs,
    count_steps_in_prompt,
    device_vram_usage,
    fetch_history_ids,
    fetch_queue_status,
    format_history_status,
    history_entry_graph,
    is_port_open,
    summarize_object_info,
)


class _FakeResponse:
    """Достаточно urlopen-совместимого поведения для наших нужд:
    поддержка контекстного менеджера и .read(). status=200 по
    умолчанию -- нужно _post_json (этап 8), который проверяет код
    ответа, а не только успешный urlopen без исключения."""

    def __init__(self, payload, status=200):
        self._buf = BytesIO(json.dumps(payload).encode("utf-8"))
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._buf.read()

    def getcode(self):
        return self.status


def _urlopen_returning(payload):
    def _fake(url, timeout=None):
        return _FakeResponse(payload)

    return _fake


def _urlopen_raising(exc):
    def _fake(url, timeout=None):
        raise exc

    return _fake


def _urlopen_post_capturing(seen_requests, status=200):
    """Для тестов _post_json (этап 8) -- запоминает каждый переданный
    urllib.request.Request (url/method/data), возвращает успешный
    ответ с заданным статусом."""

    def _fake(request, timeout=None):
        seen_requests.append(request)
        return _FakeResponse({}, status=status)

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
            "pytorch_version": "2.11.0+cu130",
            "comfyui_version": "0.3.10",
            "embedded_python": True,
            "ram_total": 16011 * 1024 * 1024,
            "ram_free": 4000 * 1024 * 1024,
        },
        "devices": [
            {
                "name": "NVIDIA RTX",
                "type": "cuda",
                "index": 0,
                "vram_total": 8151 * 1024 * 1024,
                "vram_free": 1234 * 1024 * 1024,
            }
        ],
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
    assert stats.pytorch_version == "2.11.0+cu130"
    assert stats.ram_total == 16011 * 1024 * 1024
    assert stats.devices == payload["devices"]
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


# --------------------------------------------------------------------------
# Этап 8: очередь задач (список) + управление очередью/историей
# --------------------------------------------------------------------------

def test_client_get_queue_items_running_then_pending(monkeypatch):
    payload = {
        "queue_running": [[0, "pid-run", {"1": {"inputs": {"steps": 20}}}]],
        "queue_pending": [
            [1, "pid-a", {"1": {"inputs": {"steps": 10}}}],
            [2, "pid-b", {"1": {"inputs": {"steps": 5}}}],
        ],
    }
    monkeypatch.setattr(
        "comfyui_studio.launcher.core.comfy_api.urllib.request.urlopen",
        _urlopen_returning(payload),
    )
    client = ComfyAPIClient(port=8188)
    items = client.get_queue_items()
    assert items == [
        {"prompt_id": "pid-run", "status": "running", "steps": 20},
        {"prompt_id": "pid-a", "status": "pending", "steps": 10},
        {"prompt_id": "pid-b", "status": "pending", "steps": 5},
    ]


def test_client_get_queue_items_none_without_port():
    client = ComfyAPIClient()
    assert client.get_queue_items() is None


def test_client_get_queue_items_none_on_failure(monkeypatch):
    monkeypatch.setattr(
        "comfyui_studio.launcher.core.comfy_api.urllib.request.urlopen",
        _urlopen_raising(OSError("boom")),
    )
    client = ComfyAPIClient(port=8188)
    assert client.get_queue_items() is None


def test_client_delete_queue_item_posts_delete_body(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "comfyui_studio.launcher.core.comfy_api.urllib.request.urlopen",
        _urlopen_post_capturing(seen),
    )
    client = ComfyAPIClient(port=8188)
    assert client.delete_queue_item("pid-a") is True
    assert len(seen) == 1
    req = seen[0]
    assert req.full_url == "http://127.0.0.1:8188/queue"
    assert req.get_method() == "POST"
    assert json.loads(req.data.decode("utf-8")) == {"delete": ["pid-a"]}


def test_client_clear_queue_posts_clear_body(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "comfyui_studio.launcher.core.comfy_api.urllib.request.urlopen",
        _urlopen_post_capturing(seen),
    )
    client = ComfyAPIClient(port=8188)
    assert client.clear_queue() is True
    assert json.loads(seen[0].data.decode("utf-8")) == {"clear": True}


def test_client_interrupt_posts_empty_body(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "comfyui_studio.launcher.core.comfy_api.urllib.request.urlopen",
        _urlopen_post_capturing(seen),
    )
    client = ComfyAPIClient(port=8188)
    assert client.interrupt() is True
    assert seen[0].full_url == "http://127.0.0.1:8188/interrupt"
    assert seen[0].data == b""


def test_client_delete_history_item_posts_delete_body(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "comfyui_studio.launcher.core.comfy_api.urllib.request.urlopen",
        _urlopen_post_capturing(seen),
    )
    client = ComfyAPIClient(port=8188)
    assert client.delete_history_item("pid-a") is True
    assert seen[0].full_url == "http://127.0.0.1:8188/history"
    assert json.loads(seen[0].data.decode("utf-8")) == {"delete": ["pid-a"]}


def test_client_clear_history_posts_clear_body(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "comfyui_studio.launcher.core.comfy_api.urllib.request.urlopen",
        _urlopen_post_capturing(seen),
    )
    client = ComfyAPIClient(port=8188)
    assert client.clear_history() is True
    assert json.loads(seen[0].data.decode("utf-8")) == {"clear": True}


def test_client_queue_history_mutations_false_without_port():
    client = ComfyAPIClient()
    assert client.delete_queue_item("pid-a") is False
    assert client.clear_queue() is False
    assert client.interrupt() is False
    assert client.delete_history_item("pid-a") is False
    assert client.clear_history() is False


def test_client_queue_history_mutations_false_on_non_2xx(monkeypatch):
    monkeypatch.setattr(
        "comfyui_studio.launcher.core.comfy_api.urllib.request.urlopen",
        lambda req, timeout=None: _FakeResponse({}, status=500),
    )
    client = ComfyAPIClient(port=8188)
    assert client.clear_queue() is False


def test_client_queue_history_mutations_false_on_network_error(monkeypatch):
    monkeypatch.setattr(
        "comfyui_studio.launcher.core.comfy_api.urllib.request.urlopen",
        _urlopen_raising(OSError("boom")),
    )
    client = ComfyAPIClient(port=8188)
    assert client.clear_queue() is False


# --------------------------------------------------------------------------
# Этап 8: чистые хелперы форматирования для QueueHistoryDialog
# (format_history_status/count_history_outputs/history_entry_graph) --
# без сети и без Qt, см. докстрайны в comfy_api.py.
# --------------------------------------------------------------------------

def test_format_history_status_prefers_status_str():
    entry = {"status": {"status_str": "success", "completed": True}}
    assert format_history_status(entry) == "success"


def test_format_history_status_falls_back_to_completed_true():
    entry = {"status": {"completed": True}}
    assert format_history_status(entry) == "success"


def test_format_history_status_falls_back_to_completed_false():
    entry = {"status": {"completed": False}}
    assert format_history_status(entry) == "error"


def test_format_history_status_unknown_shape():
    assert format_history_status({}) == "?"
    assert format_history_status({"status": "not-a-dict"}) == "?"


def test_count_history_outputs_sums_images_across_nodes():
    entry = {
        "outputs": {
            "9": {"images": [{"filename": "a.png"}, {"filename": "b.png"}]},
            "12": {"images": [{"filename": "c.png"}]},
            "13": {"not_images": []},
        }
    }
    assert count_history_outputs(entry) == 3


def test_count_history_outputs_missing_or_malformed():
    assert count_history_outputs({}) == 0
    assert count_history_outputs({"outputs": "not-a-dict"}) == 0


def test_history_entry_graph_dict_form():
    graph = {"1": {"class_type": "KSampler", "inputs": {"steps": 20}}}
    assert history_entry_graph({"prompt": graph}) is graph


def test_history_entry_graph_list_form():
    graph = {"1": {"class_type": "KSampler", "inputs": {"steps": 20}}}
    entry = {"prompt": [0, "pid-1", graph, {}, []]}
    assert history_entry_graph(entry) is graph


def test_history_entry_graph_missing():
    assert history_entry_graph({}) is None
    assert history_entry_graph({"prompt": [0, "pid-1"]}) is None


def test_count_steps_resolves_literal_link_to_single_numeric_input():
    """steps подан не напрямую, а ссылкой на ноду-константу с ровно
    одним числовым входом -- значение всё равно учитывается."""
    graph = {
        "1": {"inputs": {"steps": ["2", 0]}},
        "2": {"class_type": "PrimitiveInt", "inputs": {"value": 15}},
    }
    assert count_steps_in_prompt(graph) == 15


def test_count_steps_ambiguous_link_target_not_resolved():
    """Нода-источник с 2+ числовыми входами -- не гадаем, какой из них
    "тот самый", результат не учитывается (не 0-length assumption)."""
    graph = {
        "1": {"inputs": {"steps": ["2", 0]}},
        "2": {"class_type": "RandomInt", "inputs": {"min": 1, "max": 50}},
    }
    assert count_steps_in_prompt(graph) == 0


def test_count_steps_genuine_runtime_value_not_recoverable():
    """Нода без вообще никаких числовых inputs (типичный случай
    настоящего "рандома", вычисляемого во время выполнения) --
    принципиально не разрешается: значения просто нет в графе."""
    graph = {
        "1": {"inputs": {"steps": ["2", 0]}},
        "2": {"class_type": "RandomSteps", "inputs": {}},
    }
    assert count_steps_in_prompt(graph) == 0


# --------------------------------------------------------------------------
# Этап 8: "Модели и VRAM" + "Custom nodes" -- device_vram_usage/
# summarize_object_info (чистые функции, без сети/Qt)
# --------------------------------------------------------------------------

def test_device_vram_usage_computes_used_and_percent():
    device = {"name": "RTX 5050", "type": "cuda", "vram_total": 8000, "vram_free": 2000}
    used, total, pct = device_vram_usage(device)
    assert used == 6000
    assert total == 8000
    assert pct == pytest.approx(75.0)


def test_device_vram_usage_none_on_missing_or_invalid_fields():
    assert device_vram_usage({}) is None
    assert device_vram_usage({"vram_total": 100}) is None  # нет vram_free
    assert device_vram_usage({"vram_total": 0, "vram_free": 0}) is None  # total<=0
    assert device_vram_usage({"vram_total": "100", "vram_free": 10}) is None  # не число
    assert device_vram_usage("not-a-dict") is None


def test_device_vram_usage_free_larger_than_total_clamped_to_zero():
    # Не должно случаться на практике, но не должно и уходить в минус.
    used, total, pct = device_vram_usage({"vram_total": 100, "vram_free": 150})
    assert used == 0
    assert pct == 0.0


def test_summarize_object_info_sorted_with_fallbacks():
    info = {
        "KSampler": {"display_name": "KSampler", "category": "sampling"},
        "CLIPTextEncode": {"category": "conditioning"},
        "AAA": {},
    }
    result = summarize_object_info(info)
    assert [item["class_type"] for item in result] == ["AAA", "CLIPTextEncode", "KSampler"]
    assert result[0]["display_name"] == "AAA"  # нет display_name -- падаем на class_type
    assert result[0]["category"] == ""
    assert result[1]["display_name"] == "CLIPTextEncode"
    assert result[2]["category"] == "sampling"


def test_summarize_object_info_empty_or_invalid():
    assert summarize_object_info({}) == []
    assert summarize_object_info(None) == []
    assert summarize_object_info("not-a-dict") == []
