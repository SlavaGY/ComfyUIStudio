"""Paired-phone editing through the real Remote proxy and IEditor routes."""

import json
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient

from comfyui_studio.ieditor.backend import config_store
from comfyui_studio.ieditor.backend import main as editor
from comfyui_studio.launcher.core import remote_process
from comfyui_studio.remote import __main__ as remote_cli
from comfyui_studio.remote import app as remote_app
from comfyui_studio.remote import apps_registry, device_store, ieditor_launcher, imagine_proxy
from comfyui_studio.remote.generation_watcher import GenerationWatcher
from comfyui_studio.remote.routes import apps, home, system
from comfyui_studio.remote.state import runtime
from comfyui_studio.remote.ws_hub import ConnectionHub

JOB = "00000000-0000-4000-8000-000000000001"
PNG = b"\x89PNG\r\n\x1a\n" + b"test image data"


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    apps_registry.clear_registry()
    for name in runtime.__dataclass_fields__:
        monkeypatch.setattr(runtime, name, getattr(runtime, name))
    runtime.comfy_port = runtime.imagine_port = runtime.imagine_pony_port = runtime.lm_studio_port = runtime.ieditor_port = None
    for name, value in (("_process", None), ("_last_error", None), ("_awaiting_comfy", False), ("_generation", 0)):
        monkeypatch.setattr(ieditor_launcher, name, value)
    yield
    apps_registry.clear_registry()


def test_editor_is_registered_independently_of_imagine(monkeypatch):
    runtime.ieditor_port = 7895
    monkeypatch.setattr(ieditor_launcher, "is_ready", lambda port: True)
    remote_app._register_known_apps()
    app = apps_registry.get_app("ieditor")
    assert app.name == "IEditor" and app.path == "/apps/ieditor/"
    assert app.proxy_target() == "http://127.0.0.1:7895"
    assert app.start_fn and app.stop_fn and app.status_fn
    assert len(apps_registry.list_all_apps()) == 1


@pytest.fixture
def phone(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "DATA_DIR", tmp_path / "editor")
    runtime.ieditor_port, runtime.comfy_port = 7865, 8188
    monkeypatch.setattr(ieditor_launcher, "is_ready", lambda port: True)
    comfy = Mock()
    comfy.base_url = "http://127.0.0.1:8188"
    comfy.queue_prompt.return_value = JOB
    comfy.get_history.return_value = {
        "status": {"status_str": "success", "completed": True},
        "outputs": {"9": {"images": [{"filename": "phone.png", "subfolder": "IEditor", "type": "output"}]}},
    }
    monkeypatch.setattr(editor, "get_client", lambda *args: comfy)
    calls = []

    async def forward(request):
        calls.append(request)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=editor.app)) as upstream:
            response = await upstream.send(request)
        return httpx.Response(response.status_code, headers=response.headers, stream=httpx.ByteStream(response.content))

    monkeypatch.setattr(imagine_proxy, "_client", httpx.AsyncClient(transport=httpx.MockTransport(forward)))
    original = httpx.Client

    def upload(request):
        body = request.read()
        assert b'name="subfolder"\r\n\r\nieditor' in body
        name = body.split(b'filename="', 1)[1].split(b'"', 1)[0].decode()
        return httpx.Response(200, json={"name": name, "subfolder": "ieditor"})

    async def image(url, timeout):
        return Response(PNG, media_type="image/png")

    monkeypatch.setattr(editor.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(upload)))
    monkeypatch.setattr(editor, "stream_image", image)
    remote_app._register_known_apps()
    remote = FastAPI()
    remote.include_router(imagine_proxy.router)
    remote.include_router(home.router)
    remote.include_router(apps.router, prefix="/api/v1/remote")
    device_id, token = device_store.create_device("Isolated test phone")
    with TestClient(remote, base_url="http://phone:7861") as client:
        yield client, token, device_id, comfy, calls


def test_unpaired_and_revoked_phones_cannot_access_editor_assets_or_upload(phone):
    client, token, device, _, calls = phone
    for path in ("/apps/ieditor/", "/apps/ieditor/js/app.js", "/apps/ieditor/api/jobs"):
        assert client.get(path).status_code == 401
    assert not calls
    assert client.get("/apps/ieditor/?token=" + token).status_code == 200
    device_store.revoke_device(device)
    assert client.get("/apps/ieditor/js/app.js").status_code == 401
    assert client.post("/apps/ieditor/api/upload-image", files={"file": ("photo.png", PNG)}).status_code == 401


def test_mobile_home_and_registry_expose_editor_without_apk_changes(phone):
    client, token, _, _, _ = phone
    headers = {"Authorization": "Bearer " + token}
    listing = client.get("/api/v1/remote/apps", headers=headers).json()
    assert listing[0]["id"] == "ieditor" and listing[0]["status"] == "running"
    assert listing[0]["path"] == "/apps/ieditor/" and listing[0]["startable"]
    assert 'href="/apps/ieditor/"' in client.get("/?token=" + token).text


@pytest.mark.parametrize("count", [1, 2])
def test_phone_upload_edit_status_and_result_keep_proxy_prefix(phone, count):
    client, token, _, comfy, _ = phone
    page = client.get("/apps/ieditor/?token=" + token)
    assert "window.__REMOTE_TOKEN__" in page.text
    assert 'href="studio-style/css/style.css' in page.text
    for path in ("js/app.js", "common/js/i18n_engine.js", "studio-style/css/themes.css"):
        assert client.get("/apps/ieditor/" + path).status_code == 200
    images = []
    for index in range(count):
        response = client.post("/apps/ieditor/api/upload-image", files={"file": (f"photo{index}.png", PNG, "image/png")})
        assert response.status_code == 200, response.text
        images.append(response.json()["image"])
    queued = client.post("/apps/ieditor/api/generate", json={"prompt": "Change the circle to green", "images": images})
    assert queued.status_code == 200, queued.text
    graph = comfy.queue_prompt.call_args.args[0]
    assert len([node for node in graph.values() if node["class_type"] == "LoadImage"]) == count
    status = client.get(f"/apps/ieditor/api/generate/{JOB}/status").json()
    assert status["state"] == "done"
    result = client.get("/apps/ieditor/" + status["images"][0]["url"])
    assert result.status_code == 200 and result.content == PNG
    assert result.headers["content-type"] == "image/png"


def test_start_reuses_desktop_editor_instead_of_creating_duplicate(monkeypatch):
    runtime.ieditor_port, runtime.comfy_port = 7865, 8188
    monkeypatch.setattr(ieditor_launcher, "is_ready", lambda port: True)
    factory = Mock()
    monkeypatch.setattr(ieditor_launcher, "IEditorProcess", factory)
    ieditor_launcher.start()
    factory.assert_not_called()


def test_remote_start_and_stop_use_configured_ports_without_stopping_comfy(monkeypatch):
    runtime.ieditor_port, runtime.comfy_port = 7895, 8199
    monkeypatch.setattr(ieditor_launcher, "is_ready", lambda port: False)
    monkeypatch.setattr(ieditor_launcher, "check_ieditor_port", Mock())
    monkeypatch.setattr(ieditor_launcher.comfy_launcher, "is_comfyui_running", lambda **kw: True)
    comfy_stop = Mock()
    monkeypatch.setattr(ieditor_launcher.comfy_launcher, "stop_comfyui", comfy_stop)
    process = Mock()
    process.is_running.return_value = True
    factory = Mock(return_value=process)
    monkeypatch.setattr(ieditor_launcher, "IEditorProcess", factory)
    ieditor_launcher.start()
    ieditor_launcher.start()
    factory.assert_called_once_with(host="127.0.0.1", port=7895, comfy_host="127.0.0.1", comfy_port=8199)
    ieditor_launcher.stop()
    process.stop.assert_called_once_with()
    comfy_stop.assert_not_called()


def test_cancelled_cold_start_cannot_resurrect_editor(monkeypatch):
    runtime.ieditor_port, runtime.comfy_port = 7865, 8188
    monkeypatch.setattr(ieditor_launcher, "is_ready", lambda port: False)
    monkeypatch.setattr(ieditor_launcher, "check_ieditor_port", Mock())
    monkeypatch.setattr(ieditor_launcher.comfy_launcher, "is_comfyui_running", lambda **kw: False)
    monkeypatch.setattr(ieditor_launcher.comfy_launcher, "comfyui_status", lambda **kw: "starting")
    thread = Mock()
    monkeypatch.setattr(ieditor_launcher.threading, "Thread", thread)
    spawn = Mock()
    monkeypatch.setattr(ieditor_launcher, "_spawn", spawn)
    ieditor_launcher.start()
    generation = ieditor_launcher._generation
    assert ieditor_launcher.status() == "starting"
    ieditor_launcher.stop()
    monkeypatch.setattr(ieditor_launcher.time, "sleep", lambda _: None)
    ieditor_launcher._wait_for_comfy_then_start(generation)
    spawn.assert_not_called()
    assert ieditor_launcher.status() == "stopped"


def test_stop_desktop_editor_does_not_kill_unrelated_http_service(monkeypatch):
    runtime.ieditor_port = 7865
    monkeypatch.setattr(ieditor_launcher, "is_ready", lambda port: False)
    lookup = Mock()
    monkeypatch.setattr(ieditor_launcher, "find_pid_listening_on_port", lookup)
    ieditor_launcher.stop()
    lookup.assert_not_called()


def test_stop_desktop_editor_by_port(monkeypatch):
    runtime.ieditor_port = 7865
    monkeypatch.setattr(ieditor_launcher, "is_ready", lambda port: True)
    monkeypatch.setattr(ieditor_launcher, "find_pid_listening_on_port", lambda port: 1234)
    kill = Mock()
    monkeypatch.setattr(ieditor_launcher, "kill_pid_tree", kill)
    ieditor_launcher.stop()
    kill.assert_called_once_with(1234, "IEditor")


def test_editor_is_stopped_before_shared_comfy_on_global_stop(monkeypatch):
    order = []
    for module, name in ((system.lm_studio_launcher, "stop"), (system.ieditor_launcher, "stop"), (system.pony_launcher, "stop_pony"), (system.app_launcher, "stop_imagine"), (system.comfy_launcher, "stop_comfyui")):
        monkeypatch.setattr(module, name, lambda key=(module.__name__, name): order.append(key))
    system.stop_server("test")
    assert order.index((system.ieditor_launcher.__name__, "stop")) < order.index((system.comfy_launcher.__name__, "stop_comfyui"))


def test_remote_cli_and_frozen_command_receive_custom_editor_port(monkeypatch):
    monkeypatch.setattr(remote_process, "_missing_remote_dependencies", lambda: [])
    for frozen in (False, True):
        monkeypatch.setattr(remote_process.sys, "frozen", frozen, raising=False)
        command, _, error = remote_process.resolve_remote_launch("127.0.0.1", 7861, "127.0.0.1", 8188, 7860, ieditor_port=7895)
        assert error is None and command[command.index("--ieditor-port") + 1] == "7895"
    assert remote_cli._parse_args(["--ieditor-port", "7895"]).ieditor_port == 7895


def test_restarting_remote_from_older_open_studio_reads_editor_port(monkeypatch):
    import uvicorn

    from comfyui_studio.launcher.core import config
    monkeypatch.setattr(config, "load_config", lambda: {"ieditor": {"port": 7895}})
    monkeypatch.setattr(uvicorn, "run", Mock())
    remote_cli.main(["--comfy-port", "8188"])
    assert runtime.ieditor_port == 7895
    remote_cli.main(["--comfy-port", "8188", "--ieditor-port", "7896"])
    assert runtime.ieditor_port == 7896


def test_completion_keeps_editor_path_even_with_imagine_running(monkeypatch):
    runtime.ieditor_port, runtime.imagine_port = 7865, 7860

    class Reply:
        def __init__(self, data): self.data = data
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return json.dumps(self.data).encode()

    def get(url, **kwargs):
        assert ":7865/" in url  # Imagine must not claim this editor job.
        return Reply({"jobs": [{"prompt_id": JOB}]} if url.endswith("/api/jobs") else {"state": "done", "images": [{"url": "api/image?filename=phone.png"}]})

    monkeypatch.setattr("comfyui_studio.remote.generation_watcher.urllib.request.urlopen", get)
    watcher = GenerationWatcher(ConnectionHub())
    assert watcher._fetch_imagine_status(JOB) == ("done", ["/apps/ieditor/api/image?filename=phone.png"])
    assert watcher._resolved_app_path == "/apps/ieditor/"
