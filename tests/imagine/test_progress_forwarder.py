"""
Тесты comfyui_studio/imagine/backend/progress_forwarder.py -- чистая
логика разбора WS-сообщений ComfyUI и пересылки в Remote, без реального
WS-соединения/сети (§3 дорожной карты Remote, "Пошаговый прогресс").

_connect_and_forward() (реальное WS-соединение через `websockets`) и
run_forever() (бесконечный цикл переподключения) намеренно НЕ
тестируются здесь -- это уже сетевой ввод-вывод, который стоило бы
проверять только на реальном ComfyUI (как и WS-цикл Remote в этапе 2,
см. tests/remote/test_generation_watcher.py, где тестируется только
_tick(), а не сам HTTP/сеть). _handle_text_message() и
_forward_to_remote() -- вся логика, которую имеет смысл проверять
юнит-тестами.
"""

import json

from comfyui_studio.imagine.backend import progress_forwarder as pf


def test_handle_progress_message_forwards(monkeypatch):
    calls = []
    monkeypatch.setattr(
        pf, "_forward_to_remote", lambda prompt_id, value, max_value: calls.append(
            (prompt_id, value, max_value)
        )
    )

    pf._handle_text_message(
        json.dumps(
            {"type": "progress", "data": {"prompt_id": "abc", "value": 3, "max": 20}}
        )
    )

    assert calls == [("abc", 3, 20)]


def test_handle_non_progress_message_ignored(monkeypatch):
    calls = []
    monkeypatch.setattr(
        pf, "_forward_to_remote", lambda *a: calls.append(a)
    )

    pf._handle_text_message(
        json.dumps({"type": "executing", "data": {"prompt_id": "abc", "node": None}})
    )

    assert calls == []


def test_handle_progress_message_missing_fields_ignored(monkeypatch):
    calls = []
    monkeypatch.setattr(
        pf, "_forward_to_remote", lambda *a: calls.append(a)
    )

    # "value" отсутствует -- по спецификации ComfyUI этого не бывает, но
    # лучше молча проигнорировать, чем упасть на KeyError/TypeError и
    # оборвать весь WS-цикл на одном непредвиденном сообщении.
    pf._handle_text_message(
        json.dumps({"type": "progress", "data": {"prompt_id": "abc", "max": 20}})
    )

    assert calls == []


def test_handle_malformed_json_ignored():
    # Не должно бросать исключение.
    pf._handle_text_message("это не json{{{")


def test_forward_to_remote_without_remote_port_does_nothing(monkeypatch):
    monkeypatch.setattr(pf, "remote_port", None)
    calls = []
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **kw: calls.append((a, kw))
    )

    pf._forward_to_remote("abc", 3, 20)

    assert calls == []


def test_forward_to_remote_posts_expected_payload(monkeypatch):
    monkeypatch.setattr(pf, "remote_port", 7861)
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["content_type"] = request.get_header("Content-type")
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    pf._forward_to_remote("abc", 3, 20)

    assert captured["url"] == (
        "http://127.0.0.1:7861/api/v1/remote/internal/generation-progress"
    )
    assert captured["method"] == "POST"
    assert captured["body"] == {"prompt_id": "abc", "value": 3, "max": 20}
    assert captured["content_type"] == "application/json"


def test_forward_to_remote_swallows_connection_errors(monkeypatch):
    monkeypatch.setattr(pf, "remote_port", 7861)

    def raise_url_error(*_args, **_kwargs):
        import urllib.error
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", raise_url_error)

    # Best-effort -- не должно бросать исключение наружу (см. докстринг
    # модуля).
    pf._forward_to_remote("abc", 3, 20)
