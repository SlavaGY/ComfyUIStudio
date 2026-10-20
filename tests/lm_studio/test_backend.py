"""Chat persistence, model operations and streaming against the LM Studio protocol."""

import asyncio
import base64
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
        data = "".join("data: " + json.dumps(c, ensure_ascii=False) + "\n\n" for c in options.get("chunks", response_chunks))
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


def test_tagged_reasoning_is_streamed_and_saved_separately(environment):
    client, _, captured, options = environment
    options["chunks"] = [{"choices": [{"delta": {"content": text}}]}
                         for text in ["<thi", "nk>Think", " carefully</thi", "nk>Final answer"]]
    chat_id = client.post("/api/chats").json()["id"]
    response = send(client, chat_id)
    deltas = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert "".join(d.get("content", "") for d in deltas) == "Final answer"
    assert "".join(d.get("reasoning", "") for d in deltas) == "Think carefully"
    answer = client.get(f"/api/chats/{chat_id}").json()["messages"][-1]
    assert answer["content"] == "Final answer" and answer["reasoning"] == "Think carefully"
    send(client, chat_id, text="Continue")
    assert json.loads(captured[-1].content)["messages"][1]["content"] == "Final answer"


def test_edit_and_retry_earlier_prompt_replace_only_following_messages(environment):
    client, _, captured, _ = environment
    chat_id = client.post("/api/chats").json()["id"]
    send(client, chat_id, text="First")
    send(client, chat_id, text="Second")
    send(client, chat_id, text="Edited", message_index=0)
    messages = client.get(f"/api/chats/{chat_id}").json()["messages"]
    assert len(messages) == 2 and messages[0]["content"] == "Edited"
    send(client, chat_id, text="Followup")
    send(client, chat_id, retry=True, message_index=0)
    assert len(client.get(f"/api/chats/{chat_id}").json()["messages"]) == 2
    assert json.loads(captured[-1].content)["messages"] == [{"role": "user", "content": "Edited"}]
    assert send(client, chat_id, message_index=1).status_code == 400


def test_files_reach_model_and_survive_edit_retry_and_reload(environment):
    client, _, captured, _ = environment
    chat_id = client.post("/api/chats").json()["id"]
    upload = client.post("/api/attachments", json={"name": "notes.txt", "data": base64.b64encode("Файл с фактами".encode()).decode()})
    assert upload.status_code == 200
    file = upload.json()
    send(client, chat_id, text="", attachments=[file["id"]])
    assert "Файл с фактами" in json.loads(captured[-1].content)["messages"][0]["content"]
    saved = client.get(f"/api/chats/{chat_id}").json()
    assert saved["title"] == "notes.txt" and saved["messages"][0]["attachments"] == [file]
    send(client, chat_id, retry=True)
    assert "Файл с фактами" in json.loads(captured[-1].content)["messages"][0]["content"]
    before = client.get(f"/api/chats/{chat_id}").json()
    assert send(client, chat_id, message_index=0, attachments=["../invalid"]).status_code == 400
    assert client.get(f"/api/chats/{chat_id}").json() == before


def test_images_are_sent_as_multimodal_content(environment):
    client, _, captured, _ = environment
    image = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jJ1cAAAAASUVORK5CYII="
    file = client.post("/api/attachments", json={"name": "pixel.png", "data": image}).json()
    chat_id = client.post("/api/chats").json()["id"]
    send(client, chat_id, attachments=[file["id"]])
    content = json.loads(captured[-1].content)["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "Hello"}
    assert content[1]["image_url"]["url"] == "data:image/png;base64," + image


def test_mcp_settings_drive_responses_api_without_storing_credentials_in_chat(environment):
    client, _, captured, options = environment
    body = {"integrations": [{"name": "test-mcp", "url": "http://127.0.0.1:9999/mcp", "headers": {"Authorization": "Bearer secret"}, "allowed_tools": ["search"]}]}
    assert client.put("/api/integrations", json=body).status_code == 200
    assert client.get("/api/integrations").json()["integrations"][0]["enabled"]
    options["chunks"] = [{"type": "response.output_text.delta", "delta": "Tool result"}, {"type": "response.completed"}]
    chat_id = client.post("/api/chats").json()["id"]
    response = send(client, chat_id)
    assert '"done": true' in response.text
    assert captured[-1].url.path == "/v1/responses"
    payload = json.loads(captured[-1].content)
    assert payload["tools"][0]["headers"] == {"Authorization": "Bearer secret"}
    assert payload["tools"][0]["allowed_tools"] == ["search"]
    assert payload["input"][0]["content"][0]["text"] == "Hello"
    saved = client.get(f"/api/chats/{chat_id}").json()
    assert saved["messages"][-1]["content"] == "Tool result" and "secret" not in json.dumps(saved)
    send(client, chat_id, text="Continue")
    assert [m["role"] for m in json.loads(captured[-1].content)["input"]] == ["user", "assistant", "user"]
    assert client.put("/api/integrations", json={"integrations": [{"name": "bad", "url": "file:///etc/passwd"}]}).status_code == 422
    assert client.get("/api/integrations").json()["integrations"][0]["name"] == "test-mcp"


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
