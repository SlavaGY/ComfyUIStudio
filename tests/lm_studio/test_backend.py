"""Chat persistence, model operations and streaming against the LM Studio protocol."""

import asyncio
import json
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from comfyui_studio.lm_studio.backend import create_app
from comfyui_studio.lm_studio.runtime import Runtime


@pytest.fixture
def environment(tmp_path):
    captured = []
    response_chunks = [
        {"choices": [{"delta": {"reasoning_content": "Think "}}]},
        {"choices": [{"delta": {"content": "Привет <script>alert(1)</script>"}}]},
    ]
    options = {"finish": True, "status": 200}

    def upstream(request):
        captured.append(request)
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json={"models": [{"key": "test", "type": "llm", "display_name": "Test", "loaded_instances": []}]})
        if request.url.path == "/api/v1/models/load":
            return httpx.Response(200, json={"instance_id": "loaded-test", "status": "loaded"})
        if request.url.path == "/api/v1/models/unload":
            return httpx.Response(200, json={"status": "unloaded"})
        if options["status"] != 200:
            return httpx.Response(options["status"], json={"error": {"message": "model failed"}})
        data = "".join("data: " + json.dumps(c, ensure_ascii=False) + "\n\n" for c in response_chunks)
        if options["finish"]:
            data += "data: [DONE]\n\n"
        return httpx.Response(200, text=data)

    runtime = Runtime({}, client=httpx.AsyncClient(base_url="http://127.0.0.1:1234", transport=httpx.MockTransport(upstream)))
    app = create_app({}, tmp_path, runtime=runtime)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, app, captured, options


def send(client, chat_id, **kwargs):
    return client.post(f"/api/chats/{chat_id}/send", json={"model": "test", "text": "Hello", **kwargs})


def test_stream_persists_conversation_and_separates_reasoning(environment):
    client, app, captured, _ = environment
    chat_id = client.post("/api/chats").json()["id"]
    response = send(client, chat_id, system="Answer briefly", temperature=0.3, max_tokens=100)
    assert response.status_code == 200 and '"done": true' in response.text
    chat = client.get(f"/api/chats/{chat_id}").json()
    assert chat["title"] == "Hello"
    assert chat["messages"][1]["reasoning"] == "Think "
    assert chat["messages"][1]["content"].startswith("Привет <script>")
    assert chat["messages"][1]["status"] == "completed"
    payload = json.loads(captured[-1].content)
    assert payload["messages"][0] == {"role": "system", "content": "Answer briefly"}
    assert payload["max_tokens"] == 100
    assert app.state.store.get(chat_id) == chat


def test_followup_uses_previous_messages_without_reasoning(environment):
    client, _, captured, _ = environment
    chat_id = client.post("/api/chats").json()["id"]
    send(client, chat_id)
    send(client, chat_id, text="Continue")
    assert client.get(f"/api/chats/{chat_id}").json()["title"] == "Hello"
    payload = json.loads(captured[-1].content)
    assert [m["role"] for m in payload["messages"]] == ["user", "assistant", "user"]
    assert all("reasoning" not in m for m in payload["messages"])


def test_retry_replaces_last_answer_instead_of_duplicating_prompt(environment):
    client, _, captured, _ = environment
    chat_id = client.post("/api/chats").json()["id"]
    send(client, chat_id)
    response = send(client, chat_id, retry=True)
    assert response.status_code == 200
    chat = client.get(f"/api/chats/{chat_id}").json()
    assert len(chat["messages"]) == 2
    assert len(json.loads(captured[-1].content)["messages"]) == 1


@pytest.mark.parametrize("status,finish", [(500, True), (200, False), (401, True)])
def test_failed_stream_is_saved_and_retry_still_works(environment, status, finish):
    client, _, _, options = environment
    chat_id = client.post("/api/chats").json()["id"]
    options.update(status=status, finish=finish)
    response = send(client, chat_id)
    assert '"error":' in response.text
    assert client.get(f"/api/chats/{chat_id}").json()["messages"][-1]["status"] == "error"
    options.update(status=200, finish=True)
    assert '"done": true' in send(client, chat_id, retry=True).text


def test_models_load_unload_and_client_auth_is_not_exposed(environment):
    client, app, captured, _ = environment
    assert client.get("/api/models").json()["models"][0]["key"] == "test"
    client.post("/api/models/load", json={"model": "test", "context_length": 8192})
    assert "loaded-test" in app.state.engine.owned_models
    assert json.loads(captured[-1].content)["context_length"] == 8192
    client.post("/api/models/unload", json={"instance_id": "loaded-test"})
    assert not app.state.engine.owned_models
    assert "token" not in client.get("/api/health").text


def test_chat_settings_delete_and_validation(environment):
    client, _, _, _ = environment
    chat_id = client.post("/api/chats").json()["id"]
    assert send(client, chat_id, text="   ").status_code == 400
    assert send(client, chat_id, model="").status_code == 400
    assert send(client, chat_id, temperature=-5).status_code == 422
    assert send(client, chat_id, retry=True).status_code == 400
    assert client.put(f"/api/chats/{chat_id}", json={"title": "Test", "system": "Be kind"}).status_code == 200
    assert client.get("/api/chats").json()["chats"][0]["title"] == "Test"
    assert client.delete(f"/api/chats/{chat_id}").status_code == 200
    assert client.get(f"/api/chats/{chat_id}").status_code == 404


def test_local_only_and_static_assets(environment):
    client, _, _, _ = environment
    assert client.post("/api/chats", headers={"origin": "https://example.com"}).status_code == 403
    assert client.get("/api/health", headers={"host": "attacker.test"}).status_code == 403
    assert client.get("/").status_code == 200
    assert client.get("/css/themes.css").status_code == 200
    assert client.get("/common/js/i18n_engine.js").status_code == 200
    assert "no-store" in client.get("/").headers["cache-control"]
    assert client.get("/api/v1/remote/apps").status_code == 404


def test_stop_persists_partial_reply_and_releases_chat(tmp_path):
    token_sent = threading.Event()

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
            token_sent.set()
            await asyncio.Event().wait()

    def upstream(request):
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(200, stream=SlowStream())

    runtime = Runtime({}, httpx.AsyncClient(base_url="http://127.0.0.1", transport=httpx.MockTransport(upstream)))
    app = create_app({}, tmp_path, runtime)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        chat_id = client.post("/api/chats").json()["id"]
        responses = []
        worker = threading.Thread(target=lambda: responses.append(send(client, chat_id)), daemon=True)
        worker.start()
        assert token_sent.wait(3)
        stopped = client.post(f"/api/chats/{chat_id}/cancel").json()
        worker.join(timeout=3)
        assert not worker.is_alive()
        assert stopped["messages"][-1]["content"] == "partial"
        assert stopped["messages"][-1]["status"] == "interrupted"
        assert not app.state.generations
        assert client.put(f"/api/chats/{chat_id}", json={"title": "After stop"}).status_code == 200
