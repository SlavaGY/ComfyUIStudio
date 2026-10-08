"""Imagine Pony в Remote: регистрация в реестре, reverse-proxy, запуск/остановка
с телефона, определение завершения генерации."""

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from comfyui_studio.launcher.core import remote_process
from comfyui_studio.remote import app as remote_app
from comfyui_studio.remote import apps_registry, imagine_proxy, pony_launcher, state
from comfyui_studio.remote.generation_watcher import GenerationWatcher
from comfyui_studio.remote.routes import system
from comfyui_studio.remote.ws_hub import ConnectionHub


@pytest.fixture(autouse=True)
def clean():
    apps_registry.clear_registry()
    pony_launcher.clear_state()
    rt = state.runtime
    saved = (rt.comfy_host, rt.comfy_port, rt.imagine_port, rt.imagine_pony_port)
    rt.comfy_port = rt.imagine_port = rt.imagine_pony_port = None
    yield
    rt.comfy_host, rt.comfy_port, rt.imagine_port, rt.imagine_pony_port = saved
    apps_registry.clear_registry()
    pony_launcher.clear_state()


# -- реестр ------------------------------------------------------------------


def test_both_apps_registered_when_both_ports_known():
    state.runtime.imagine_port, state.runtime.imagine_pony_port = 7860, 7862
    remote_app._register_known_apps()
    ids = [a.id for a in apps_registry.list_all_apps()]
    assert sorted(ids) == ["imagine", "imagine_pony"]
    pony = apps_registry.get_app("imagine_pony")
    assert pony.name == "Imagine Pony" and pony.path == "/apps/imagine_pony/"
    assert pony.start_fn is not None and pony.status_fn is not None


def test_pony_registered_without_imagine():
    """Раньше без порта Imagine регистрация обрывалась целиком."""
    state.runtime.imagine_pony_port = 7862
    remote_app._register_known_apps()
    assert [a.id for a in apps_registry.list_all_apps()] == ["imagine_pony"]


def test_nothing_registered_without_ports():
    remote_app._register_known_apps()
    assert apps_registry.list_all_apps() == []


# -- reverse-proxy -------------------------------------------------------------


@pytest.fixture
def proxy_client(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.port, request.url.path))
        if request.url.path.endswith("/"):
            return httpx.Response(200, headers={"content-type": "text/html"},
                                  content=b"<html><head></head><body>x</body></html>")
        # stream=ByteStream -- как настоящий транспорт: ответ ещё не прочитан,
        # прокси отдаёт его потоком (aiter_raw).
        return httpx.Response(200, headers={"content-type": "application/json"},
                              stream=httpx.ByteStream(b'{"ok": true}'))

    monkeypatch.setattr(imagine_proxy, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(imagine_proxy, "resolve_device_and_token", lambda request: ("dev", "tok123"))
    app = FastAPI()
    app.include_router(imagine_proxy.router)
    return TestClient(app), seen


def test_proxy_routes_pony_to_its_own_port(proxy_client):
    client, seen = proxy_client
    state.runtime.imagine_port, state.runtime.imagine_pony_port = 7860, 7862
    state.runtime.imagine_pony_port = 7862
    apps_registry.register_app(apps_registry.RemoteApp("imagine", "Imagine", "/apps/imagine/", lambda: None))
    apps_registry.register_app(apps_registry.RemoteApp("imagine_pony", "Imagine Pony", "/apps/imagine_pony/", lambda: None))

    assert client.get("/apps/imagine_pony/api/config").json() == {"ok": True}
    assert client.get("/apps/imagine/api/config").json() == {"ok": True}
    assert seen == [(7862, "/api/config"), (7860, "/api/config")]


def test_proxy_injects_token_into_pony_html(proxy_client):
    client, _ = proxy_client
    state.runtime.imagine_pony_port = 7862
    apps_registry.register_app(apps_registry.RemoteApp("imagine_pony", "Imagine Pony", "/apps/imagine_pony/", lambda: None))
    body = client.get("/apps/imagine_pony/").text
    assert "window.__REMOTE_TOKEN__ = 'tok123';</script></head>" in body


def test_proxy_503_when_pony_port_unknown(proxy_client):
    client, _ = proxy_client
    apps_registry.register_app(apps_registry.RemoteApp("imagine_pony", "Imagine Pony", "/apps/imagine_pony/", lambda: None))
    r = client.get("/apps/imagine_pony/")
    assert r.status_code == 503 and "Imagine Pony" in r.json()["detail"]


def test_proxy_404_when_pony_not_registered(proxy_client):
    client, _ = proxy_client
    state.runtime.imagine_pony_port = 7862
    assert client.get("/apps/imagine_pony/").status_code == 404


# -- запуск/остановка с телефона ---------------------------------------------------


class FakePonyProcess:
    instances = []

    def __init__(self, **kw):
        self.kw, self.stopped = kw, False
        FakePonyProcess.instances.append(self)

    def start(self):
        pass

    def stop(self):
        self.stopped = True

    def is_running(self):
        return not self.stopped

    def exit_code(self):
        return None


@pytest.fixture
def fake_proc(monkeypatch):
    FakePonyProcess.instances = []
    monkeypatch.setattr(pony_launcher, "ImaginePonyProcess", FakePonyProcess)
    monkeypatch.setattr(pony_launcher, "is_imagine_available", lambda port: False)
    return FakePonyProcess


def test_start_without_port_raises():
    with pytest.raises(RuntimeError, match="Imagine Pony не настроен"):
        pony_launcher.start_pony()
    assert pony_launcher.pony_status() == "stopped"


def test_start_with_running_comfy_spawns_with_runtime_ports(monkeypatch, fake_proc):
    state.runtime.imagine_pony_port, state.runtime.comfy_port = 7862, 8188
    monkeypatch.setattr(pony_launcher.comfy_launcher, "is_comfyui_running", lambda port=None: True)
    pony_launcher.start_pony()
    assert fake_proc.instances[0].kw == {
        "host": "127.0.0.1", "port": 7862, "comfy_host": "127.0.0.1", "comfy_port": 8188,
    }
    assert pony_launcher.pony_status() == "starting"
    pony_launcher.start_pony()  # повторное нажатие не плодит второй процесс
    assert len(fake_proc.instances) == 1


def test_start_does_not_double_launch_comfy_already_starting(monkeypatch, fake_proc):
    state.runtime.imagine_pony_port, state.runtime.comfy_port = 7862, 8188
    launched = []
    monkeypatch.setattr(pony_launcher.comfy_launcher, "is_comfyui_running", lambda port=None: False)
    monkeypatch.setattr(pony_launcher.comfy_launcher, "comfyui_status", lambda port=None: "starting")
    monkeypatch.setattr(pony_launcher.comfy_launcher, "start_comfyui", lambda port=None: launched.append(port))
    monkeypatch.setattr(pony_launcher, "_COMFY_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(pony_launcher, "_wait_for_comfy_then_start", lambda: None)
    pony_launcher.start_pony()
    assert launched == []  # ComfyUI уже стартует (напр. по запросу Imagine)
    assert pony_launcher.pony_status() == "starting"


def test_stop_stops_own_process(fake_proc, monkeypatch):
    state.runtime.imagine_pony_port, state.runtime.comfy_port = 7862, 8188
    monkeypatch.setattr(pony_launcher.comfy_launcher, "is_comfyui_running", lambda port=None: True)
    pony_launcher.start_pony()
    pony_launcher.stop_pony()
    assert fake_proc.instances[0].stopped is True
    assert pony_launcher.pony_status() == "stopped"


def test_stop_by_port_when_started_from_studio(monkeypatch):
    state.runtime.imagine_pony_port = 7862
    killed = []
    monkeypatch.setattr(pony_launcher, "find_pid_listening_on_port", lambda port: 4242)
    monkeypatch.setattr(pony_launcher, "kill_pid_tree", lambda pid, label: killed.append((pid, label)))
    pony_launcher.stop_pony()
    assert killed == [(4242, "Imagine Pony")]


def test_stop_server_route_stops_pony_before_comfy(monkeypatch):
    order = []
    monkeypatch.setattr(system.pony_launcher, "stop_pony", lambda: order.append("pony"))
    monkeypatch.setattr(system.app_launcher, "stop_imagine", lambda: order.append("imagine"))
    monkeypatch.setattr(system.comfy_launcher, "stop_comfyui", lambda: order.append("comfy"))
    assert system.stop_server("dev") == {"status": "stopped"}
    assert order.index("pony") < order.index("comfy") and order.index("imagine") < order.index("comfy")


# -- события генерации -----------------------------------------------------------------


class _Resp:
    def __init__(self, payload):
        self._p = json.dumps(payload).encode()

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_watcher_resolves_pony_generation_with_pony_prefix(monkeypatch):
    state.runtime.imagine_port, state.runtime.imagine_pony_port = 7860, 7862

    def fake_urlopen(url, timeout=None):
        if ":7860/" in url:
            return _Resp({"state": "unknown"})  # Imagine про это задание не знает
        return _Resp({"state": "done", "images": [{"url": "api/image?filename=a.png"}]})

    monkeypatch.setattr("comfyui_studio.remote.generation_watcher.urllib.request.urlopen", fake_urlopen)
    w = GenerationWatcher(ConnectionHub())
    assert w._fetch_imagine_status("pid") == ("done", ["/apps/imagine_pony/api/image?filename=a.png"])


def test_watcher_imagine_generation_keeps_imagine_prefix(monkeypatch):
    state.runtime.imagine_port, state.runtime.imagine_pony_port = 7860, 7862
    monkeypatch.setattr(
        "comfyui_studio.remote.generation_watcher.urllib.request.urlopen",
        lambda url, timeout=None: _Resp({"state": "done", "images": [{"url": "api/image?x=1"}]}),
    )
    w = GenerationWatcher(ConnectionHub())
    assert w._fetch_imagine_status("pid") == ("done", ["/apps/imagine/api/image?x=1"])


def test_watcher_none_when_neither_knows_the_prompt(monkeypatch):
    state.runtime.imagine_port, state.runtime.imagine_pony_port = 7860, 7862
    monkeypatch.setattr(
        "comfyui_studio.remote.generation_watcher.urllib.request.urlopen",
        lambda url, timeout=None: _Resp({"state": "unknown"}),
    )
    assert GenerationWatcher(ConnectionHub())._fetch_imagine_status("pid") is None


# -- параметры запуска процесса Remote ---------------------------------------------------


def test_remote_launch_passes_pony_port(monkeypatch):
    monkeypatch.setattr(remote_process, "_missing_remote_dependencies", lambda: [], raising=False)
    cmd, _cwd, err = remote_process.resolve_remote_launch(
        "127.0.0.1", 7861, "127.0.0.1", 8188, 7860, imagine_pony_port=7862
    )
    assert err is None
    assert cmd[cmd.index("--imagine-pony-port") + 1] == "7862"
    cmd2, _, _ = remote_process.resolve_remote_launch("127.0.0.1", 7861, "127.0.0.1", 8188, 7860)
    assert "--imagine-pony-port" not in cmd2


# -- FCM: поле app_path в data-payload ---------------------------------------------------


def _capture_fcm_data(monkeypatch, event):
    from comfyui_studio.remote import fcm

    sent = []
    monkeypatch.setattr(fcm, "list_fcm_tokens", lambda: ["tok-1"])
    monkeypatch.setattr(fcm, "_load_credentials", lambda: ("access", "proj"))

    def fake_urlopen(request, timeout=None):
        sent.append(json.loads(request.data)["message"]["data"])
        return _Resp({})

    monkeypatch.setattr(fcm.urllib.request, "urlopen", fake_urlopen)
    # tests/remote/conftest.py подменяет send_generation_push -- зовём
    # настоящую реализацию напрямую.
    fcm._send_generation_push_unsafe(event)
    return sent


def test_push_data_carries_app_path(monkeypatch):
    data, = _capture_fcm_data(monkeypatch, {
        "type": "generation.completed", "prompt_id": "p1", "image_urls": ["x"],
        "app_path": "/apps/imagine_pony/",
    })
    assert data["app_path"] == "/apps/imagine_pony/" and data["prompt_id"] == "p1"
    assert data["state"] == "generation.completed"


def test_push_data_app_path_empty_when_unknown(monkeypatch):
    data, = _capture_fcm_data(monkeypatch, {"type": "generation.completed", "prompt_id": "p1", "image_urls": []})
    assert data["app_path"] == ""
