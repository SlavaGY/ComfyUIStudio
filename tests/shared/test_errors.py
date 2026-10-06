"""
Тесты comfyui_studio/errors.py и его использования на границах сервисов
(этап R11.1 плана рефакторинга). Без Qt; сеть -- только localhost.
"""
import http.server
import json
import socket
import threading

import pytest

from comfyui_studio import errors
from comfyui_studio.imagine.backend import comfy_client
from comfyui_studio.launcher.core import imagine_process, remote_process

# -- иерархия ----------------------------------------------------------------


def test_hierarchy():
    assert issubclass(errors.ProcessStartError, errors.StudioError)
    assert issubclass(errors.ComfyUIError, errors.StudioError)
    assert issubclass(errors.ComfyUIConnectionError, errors.ComfyUIError)


def test_process_start_error_is_still_a_runtime_error():
    # прежний код бросал RuntimeError, а вызывающие ловят именно его
    assert issubclass(errors.ProcessStartError, RuntimeError)


def test_connection_error_is_caught_by_old_except_clause():
    try:
        raise errors.ComfyUIConnectionError("нет связи")
    except errors.ComfyUIError as exc:
        assert str(exc) == "нет связи"


def test_studio_error_does_not_swallow_foreign_bugs():
    for foreign in (KeyError, TypeError, ValueError, OSError):
        assert not issubclass(foreign, errors.StudioError)


def test_comfy_client_reexports_same_classes():
    assert comfy_client.ComfyUIError is errors.ComfyUIError
    assert comfy_client.ComfyUIConnectionError is errors.ComfyUIConnectionError


def test_errors_module_is_stdlib_only():
    import ast
    from pathlib import Path

    tree = ast.parse(Path(errors.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__"}


# -- процессы: «не запустился» ----------------------------------------------


def test_imagine_start_failure_raises_process_start_error(monkeypatch):
    monkeypatch.setattr(
        imagine_process, "resolve_imagine_launch", lambda *a, **k: (None, None, "нет fastapi")
    )
    p = imagine_process.ImagineProcess("h", 1, "c", 2)
    with pytest.raises(errors.ProcessStartError, match="нет fastapi"):
        p.start()


def test_remote_start_failure_raises_process_start_error(monkeypatch):
    monkeypatch.setattr(
        remote_process, "resolve_remote_launch", lambda *a, **k: (None, None, "нет zeroconf")
    )
    p = remote_process.RemoteProcess("h", 1, "c", 2)
    with pytest.raises(errors.ProcessStartError, match="нет zeroconf"):
        p.start()


# -- ComfyClient: «недоступен» против «ответил ошибкой» ---------------------


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]  # порт освобождён при выходе из with


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):  # тишина в выводе pytest
        pass

    def do_GET(self):
        if self.path == "/garbage":
            body = b"not json"
            self.send_response(200)
        else:
            body = b"nope"
            self.send_response(404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        body = json.dumps({"error": "bad"}).encode()
        self.send_response(500)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def live_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def _client(port):
    return comfy_client.ComfyClient("127.0.0.1", port)


def test_get_without_server_raises_connection_error():
    with pytest.raises(errors.ComfyUIConnectionError, match="GET /queue failed"):
        _client(_closed_port())._get("/queue")


def test_post_without_server_raises_connection_error():
    with pytest.raises(errors.ComfyUIConnectionError, match="POST /prompt failed"):
        _client(_closed_port())._post("/prompt", {})


def test_fetch_image_without_server_raises_connection_error():
    with pytest.raises(errors.ComfyUIConnectionError, match="Не удалось получить изображение"):
        _client(_closed_port()).fetch_image_bytes("a.png", "", "output")


def test_get_http_error_is_comfyui_error_but_not_connection_error(live_server):
    with pytest.raises(errors.ComfyUIError) as info:
        _client(live_server)._get("/missing")
    assert not isinstance(info.value, errors.ComfyUIConnectionError)
    assert "GET /missing failed" in str(info.value)


def test_get_bad_json_is_comfyui_error_but_not_connection_error(live_server):
    with pytest.raises(errors.ComfyUIError) as info:
        _client(live_server)._get("/garbage")
    assert not isinstance(info.value, errors.ComfyUIConnectionError)


def test_post_http_error_is_comfyui_error_but_not_connection_error(live_server):
    with pytest.raises(errors.ComfyUIError, match="HTTP 500") as info:
        _client(live_server)._post("/prompt", {})
    assert not isinstance(info.value, errors.ComfyUIConnectionError)


def test_fetch_image_http_error_is_comfyui_error_but_not_connection_error(live_server):
    with pytest.raises(errors.ComfyUIError) as info:
        _client(live_server).fetch_image_bytes("a.png", "", "output")
    assert not isinstance(info.value, errors.ComfyUIConnectionError)
