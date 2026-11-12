"""Exercise the form/upload/queue/result contract without starting GPU jobs."""

from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from comfyui_studio.ieditor.backend import config_store, main
from comfyui_studio.imagine.backend.comfy_client import ComfyUIError

IMAGE = "ieditor/ieditor_" + "a" * 32 + ".png"
JOB = "00000000-0000-4000-8000-000000000001"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "DATA_DIR", tmp_path)
    comfy = MagicMock()
    comfy.queue_prompt.return_value = JOB
    comfy.get_history.return_value = None
    comfy.get_queue_position.return_value = (True, 0)
    comfy.base_url = "http://127.0.0.1:8188"
    monkeypatch.setattr(main, "get_client", lambda *args, **kwargs: comfy)
    with TestClient(main.app) as client:
        yield client, comfy


def submit(client):
    response = client.post("/api/generate", json={"prompt": "Make the cup blue", "images": [IMAGE], "seed": 42, "randomize_seed": False})
    assert response.status_code == 200, response.text
    return response.json()


def test_queue_then_result_survives_comfy_history_eviction(env):
    client, comfy = env
    job = submit(client)
    assert job["seed"] == 42
    comfy.get_history.return_value = {
        "status": {"status_str": "success", "completed": True},
        "outputs": {"9": {"images": [{"filename": "edit & 1.png", "subfolder": "IEditor", "type": "output"}]}},
    }
    result = client.get(f"/api/generate/{JOB}/status").json()
    assert result["state"] == "done"
    assert "edit+%26+1.png" in result["images"][0]["url"]
    comfy.get_history.side_effect = ComfyUIError("server restarted")
    assert client.get(f"/api/generate/{JOB}/status").json() == result
    assert client.get("/api/jobs").json()["jobs"][0]["result"] == result


def test_execution_error_is_reported_even_with_partial_image_output(env):
    client, comfy = env
    submit(client)
    comfy.get_history.return_value = {
        "status": {"status_str": "error", "completed": False, "messages": [["execution_error", {"exception_message": "CUDA out of memory"}]]},
        "outputs": {"9": {"images": [{"filename": "partial.png"}]}},
    }
    result = client.get(f"/api/generate/{JOB}/status").json()
    assert result["state"] == "error" and result["error"] == "CUDA out of memory"


@pytest.mark.parametrize("body", [
    {"prompt": "test", "images": []},
    {"prompt": " ", "images": [IMAGE]},
    {"prompt": "test", "images": ["../private.png"]},
    {"prompt": "test", "images": [IMAGE], "steps": 0},
])
def test_invalid_request_never_reaches_comfy(env, body):
    client, comfy = env
    assert client.post("/api/generate", json=body).status_code in (400, 422)
    comfy.queue_prompt.assert_not_called()


def test_unknown_jobs_are_not_claimed_from_another_apps_comfy_history(env):
    client, comfy = env
    assert client.get(f"/api/generate/{JOB}/status").json() == {"state": "unknown"}
    comfy.get_history.assert_not_called()


def test_upload_goes_to_comfy_with_unique_name_and_input_subfolder(env, monkeypatch):
    client, _ = env
    captured = []

    def handler(request):
        payload = request.read()
        captured.append(payload)
        assert request.url.path == "/upload/image"
        assert b'name="subfolder"\r\n\r\nieditor' in payload
        name = payload.split(b'filename="', 1)[1].split(b'"', 1)[0].decode()
        return httpx.Response(200, json={"name": name, "subfolder": "ieditor", "type": "input"})

    original = httpx.Client
    monkeypatch.setattr(main.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handler)))
    result = client.post("/api/upload-image", files={"file": ("untrusted-name.txt", b"\x89PNG\r\n\x1a\n" + b"a" * 20)} )
    assert result.status_code == 200, result.text
    assert result.json()["image"].startswith("ieditor/ieditor_")
    assert result.json()["image"].endswith(".png")
    assert len(captured) == 1


def test_non_image_is_rejected(env):
    client, _ = env
    result = client.post("/api/upload-image", files={"file": ("fake.png", b"plain text")})
    assert result.status_code == 400


def test_cancel_targets_only_this_apps_running_job(env):
    client, comfy = env
    assert client.post(f"/api/generate/{JOB}/interrupt").status_code == 404
    comfy._post.assert_not_called()
    submit(client)
    assert client.post(f"/api/generate/{JOB}/interrupt").status_code == 200
    comfy._post.assert_called_once_with("/interrupt", {"prompt_id": JOB})


def test_cancel_pending_job_does_not_interrupt_someone_else(env):
    client, comfy = env
    submit(client)
    comfy.get_queue_position.return_value = (False, 2)
    assert client.post(f"/api/generate/{JOB}/interrupt").status_code == 200
    comfy._post.assert_called_once_with("/queue", {"delete": [JOB]})
    assert client.get(f"/api/generate/{JOB}/status").json()["state"] == "cancelled"


def test_completed_job_is_not_relabelled_cancelled_by_late_stop(env):
    client, comfy = env
    submit(client)
    comfy.get_queue_position.return_value = (False, None)
    comfy.get_history.return_value = {
        "status": {"status_str": "success", "completed": True},
        "outputs": {"9": {"images": [{"filename": "done.png", "type": "output"}]}},
    }
    assert client.post(f"/api/generate/{JOB}/interrupt").json()["state"] == "done"
    comfy._post.assert_not_called()


def test_state_and_static_assets(env):
    client, _ = env
    assert client.put("/api/state", json={"prompt": "Saved prompt", "images": [IMAGE]}).status_code == 200
    assert client.get("/api/state").json()["prompt"] == "Saved prompt"
    for url in ("/", "/css/editor.css", "/js/app.js", "/studio-style/css/style.css", "/studio-style/css/themes.css", "/common/js/i18n_engine.js"):
        assert client.get(url).status_code == 200, url
