"""Authenticated phone access to the actual LM Studio chat through Remote."""

import base64
import json
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from comfyui_studio.launcher.core import remote_process
from comfyui_studio.lm_studio.backend import create_app
from comfyui_studio.lm_studio.runtime import Runtime
from comfyui_studio.remote import app as remote_app
from comfyui_studio.remote import (
    apps_registry,
    device_store,
    imagine_proxy,
    lm_studio_launcher,
    state,
)
from comfyui_studio.remote.routes import apps


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    for name in ("imagine_port", "imagine_pony_port", "lm_studio_port"):
        monkeypatch.setattr(state.runtime, name, None)
    monkeypatch.setattr(lm_studio_launcher, "_process", None)
    monkeypatch.setattr(lm_studio_launcher, "_last_error", None)
    apps_registry.clear_registry()
    yield
    apps_registry.clear_registry()


def test_lm_studio_registered_without_other_apps(monkeypatch):
    state.runtime.lm_studio_port = 7864
    monkeypatch.setattr(lm_studio_launcher, "is_app_ready", lambda port: True)
    remote_app._register_known_apps()
    app = apps_registry.get_app("lm_studio")
    assert app.name == "LM Studio" and app.path == "/apps/lm_studio/"
    assert app.proxy_target() == "http://127.0.0.1:7864"
    assert app.start_fn and app.stop_fn
    assert len(apps_registry.list_all_apps()) == 1


def test_remote_source_and_frozen_commands_include_chat_port(monkeypatch):
    monkeypatch.setattr(remote_process, "_missing_remote_dependencies", lambda: [])
    for frozen in (False, True):
        monkeypatch.setattr(remote_process.sys, "frozen", frozen, raising=False)
        cmd, _, error = remote_process.resolve_remote_launch("127.0.0.1", 7861, "127.0.0.1", None, None, lm_studio_port=7994)
        assert error is None and cmd[cmd.index("--lm-studio-port") + 1] == "7994"


def test_start_is_independent_of_comfyui_and_repeated_click_does_not_duplicate(monkeypatch):
    state.runtime.lm_studio_port = 7994
    monkeypatch.setattr(lm_studio_launcher, "load_config", lambda: {"lm_studio": {"api_port": 1234}})
    monkeypatch.setattr(lm_studio_launcher, "is_app_ready", lambda port: False)
    process = Mock()
    process.is_running.return_value = True
    factory = Mock(return_value=process)
    monkeypatch.setattr(lm_studio_launcher, "LmStudioAppProcess", factory)
    lm_studio_launcher.start()
    lm_studio_launcher.start()
    factory.assert_called_once_with({"lm_studio": {"api_port": 1234, "ui_port": 7994}})
    process.start.assert_called_once_with()
    assert lm_studio_launcher.status() == "starting"
    process.is_running.return_value = False
    process.exit_code.return_value = 1
    assert lm_studio_launcher.last_error()


def test_stop_keeps_failed_process_available_for_another_attempt(monkeypatch):
    process = Mock()
    process.is_running.return_value = True
    monkeypatch.setattr(lm_studio_launcher, "_process", process)
    with pytest.raises(RuntimeError, match="Не удалось остановить"):
        lm_studio_launcher.stop()
    assert lm_studio_launcher._process is process
    process.is_running.return_value = False
    lm_studio_launcher.stop()
    assert lm_studio_launcher._process is None


def test_stop_desktop_started_app_uses_graceful_shutdown(monkeypatch):
    state.runtime.lm_studio_port = 7994
    ready = iter((True, True, False))
    monkeypatch.setattr(lm_studio_launcher, "is_app_ready", lambda port: next(ready))
    request = Mock()
    request.__enter__ = Mock()
    request.__exit__ = Mock()
    urlopen = Mock(return_value=request)
    monkeypatch.setattr(lm_studio_launcher.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(lm_studio_launcher.time, "sleep", lambda _: None)
    lm_studio_launcher.stop()
    assert urlopen.call_args.args[0].full_url == "http://127.0.0.1:7994/api/shutdown"


def test_stop_reconciles_old_remote_process_before_stopping_desktop_restart(monkeypatch):
    previous = Mock()
    previous.is_running.return_value = False
    monkeypatch.setattr(lm_studio_launcher, "_process", previous)
    test_stop_desktop_started_app_uses_graceful_shutdown(monkeypatch)
    previous.stop.assert_not_called()


@pytest.fixture
def phone(tmp_path, monkeypatch):
    upstream_calls, proxy_calls = [], []

    def engine_handler(request):
        upstream_calls.append(request)
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json={"models": []})
        body = 'data: {"choices":[{"delta":{"content":"Phone answer"}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, text=body)

    engine = Runtime({}, httpx.AsyncClient(base_url="http://127.0.0.1:1234", transport=httpx.MockTransport(engine_handler)))
    chat_app = create_app({}, tmp_path / "chat", runtime=engine)

    async def forward(request):
        proxy_calls.append(request)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=chat_app)) as client:
            response = await client.send(request)
        return httpx.Response(response.status_code, headers=response.headers, stream=httpx.ByteStream(response.content))

    monkeypatch.setattr(imagine_proxy, "_client", httpx.AsyncClient(transport=httpx.MockTransport(forward)))
    state.runtime.lm_studio_port = 7864
    monkeypatch.setattr(lm_studio_launcher, "is_app_ready", lambda port: True)
    remote_app._register_known_apps()
    remote = FastAPI()
    remote.include_router(imagine_proxy.router)
    remote.include_router(apps.router, prefix="/api/v1/remote")
    device_id, token = device_store.create_device("Test phone")
    with TestClient(chat_app, base_url="http://127.0.0.1"):
        with TestClient(remote, base_url="http://phone:7861") as client:
            yield client, token, device_id, upstream_calls, proxy_calls


def test_unauthorized_and_revoked_devices_cannot_read_chat_or_assets(phone):
    client, token, device_id, _, seen = phone
    assert client.get("/apps/lm_studio/").status_code == 401
    assert not seen
    assert client.get("/apps/lm_studio/?token=" + token).status_code == 200
    assert client.get("/apps/lm_studio/js/app.js").status_code == 200
    device_store.revoke_device(device_id)
    assert client.get("/apps/lm_studio/api/chats").status_code == 401
    assert client.get("/apps/lm_studio/js/app.js").status_code == 401


def test_proxy_preserves_origin_guard_and_does_not_leak_device_credentials(phone):
    client, token, _, _, seen = phone
    response = client.get("/apps/lm_studio/?token=" + token)
    assert "window.__REMOTE_TOKEN__" in response.text
    assert "token" not in seen[-1].url.params
    client.headers["Authorization"] = "Bearer " + token
    assert client.post("/apps/lm_studio/api/chats", headers={"Origin": "http://phone:7861"}).status_code == 200
    assert seen[-1].headers["origin"] == "http://127.0.0.1:7864"
    assert "authorization" not in seen[-1].headers and "cookie" not in seen[-1].headers
    count = len(seen)
    assert client.post("/apps/lm_studio/api/chats", headers={"Origin": "http://evil.test"}).status_code == 403
    assert len(seen) == count


def test_phone_chat_files_retry_and_long_stream_configuration(phone):
    client, token, _, engine_calls, proxy_calls = phone
    client.get("/apps/lm_studio/?token=" + token)
    prefix = "/apps/lm_studio/api/"
    file = client.post(prefix + "attachments", json={"name": "phone.txt", "data": base64.b64encode(b"Phone file content").decode()}, headers={"Origin": "http://phone:7861"}).json()
    chat = client.post(prefix + "chats").json()
    body = {"model": "test", "text": "Question", "attachments": [file["id"]]}
    response = client.post(prefix + "chats/" + chat["id"] + "/send", json=body, headers={"Origin": "http://phone:7861"})
    assert '"done": true' in response.text and "Phone answer" in response.text
    assert proxy_calls[-1].extensions["timeout"]["read"] is None
    assert "Phone file content" in json.loads(engine_calls[-1].content)["messages"][0]["content"]
    saved = client.get(prefix + "chats/" + chat["id"]).json()
    assert saved["messages"][0]["attachments"][0]["name"] == "phone.txt"
    response = client.post(prefix + "chats/" + chat["id"] + "/send", json={"model": "test", "retry": True})
    assert '"done": true' in response.text
    assert len(client.get(prefix + "chats/" + chat["id"]).json()["messages"]) == 2


def test_stop_endpoint_requires_device_token(phone, monkeypatch):
    client, token, _, _, _ = phone
    stop = Mock()
    apps_registry.get_app("lm_studio").stop_fn = stop
    url = "/api/v1/remote/apps/lm_studio/stop"
    assert client.post(url).status_code == 401
    assert client.post(url, headers={"Authorization": "Bearer " + token}).status_code == 200
    stop.assert_called_once_with()


def test_global_stop_still_stops_other_services_when_lm_shutdown_fails(monkeypatch):
    from fastapi import HTTPException

    from comfyui_studio.remote.routes import system

    monkeypatch.setattr(lm_studio_launcher, "stop", Mock(side_effect=RuntimeError("LM Studio shutdown failed")))
    others = [Mock(), Mock(), Mock()]
    monkeypatch.setattr(system.pony_launcher, "stop_pony", others[0])
    monkeypatch.setattr(system.app_launcher, "stop_imagine", others[1])
    monkeypatch.setattr(system.comfy_launcher, "stop_comfyui", others[2])
    with pytest.raises(HTTPException, match="LM Studio shutdown failed"):
        system.stop_server(_device_id="test")
    for stop in others:
        stop.assert_called_once_with()
