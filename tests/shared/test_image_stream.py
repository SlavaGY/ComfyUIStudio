"""Backpressure, HTTP failures and disconnect cleanup for both image endpoints."""

import asyncio
import gzip

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

from comfyui_studio.imagine.backend import main as imagine
from comfyui_studio.imagine.backend.comfy_client import ComfyClient
from comfyui_studio.imagine_pony.backend import main as pony
from comfyui_studio.web_common import CachePolicyMiddleware, image_stream


class Source(httpx.AsyncByteStream):
    def __init__(self, chunks=8, fail=False):
        self.chunks = chunks
        self.reads = 0
        self.closed = False
        self.fail = fail
        self.payload = b"x" * 65536

    async def __aiter__(self):
        for _ in range(self.chunks):
            self.reads += 1
            yield self.payload
            if self.fail:
                raise httpx.ReadError("upstream disconnected")

    async def aclose(self):
        self.closed = True


@pytest.fixture
def upstream(monkeypatch):
    clients, requests = [], []
    source = Source()
    config = {"status": 200, "headers": {"Content-Type": "image/webp"}}
    real_client = httpx.AsyncClient

    def handler(request):
        requests.append(request)
        if config.get("error"):
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(config["status"], headers=config["headers"], stream=source)

    def factory(**kwargs):
        client = real_client(transport=httpx.MockTransport(handler), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(image_stream.httpx, "AsyncClient", factory)
    return source, config, clients, requests


@pytest.mark.parametrize("backend", [imagine, pony])
def test_image_route_streams_and_preserves_no_store(backend, upstream, monkeypatch):
    source, config, clients, requests = upstream
    config["headers"]["Content-Length"] = str(source.chunks * 65536)
    monkeypatch.setattr(backend, "get_client", lambda: ComfyClient("localhost", 8188))
    app = FastAPI()
    app.add_middleware(CachePolicyMiddleware)
    app.get("/api/image")(backend.get_image)
    with TestClient(app) as client:
        result = client.get("/api/image", params={"filename": "a b.png", "subfolder": "a/b"})
    assert result.status_code == 200
    assert result.content == b"x" * (source.chunks * 65536)
    assert result.headers["content-type"] == "image/webp"
    assert "no-store" in result.headers["cache-control"]
    assert requests[0].url.params["filename"] == "a b.png"
    assert requests[0].url.params["subfolder"] == "a/b"
    assert requests[0].headers["accept-encoding"] == "identity"
    assert source.closed and clients[0].is_closed


def test_stream_waits_for_consumer_instead_of_buffering(upstream):
    source, _, clients, _ = upstream

    async def run():
        response = await image_stream.stream_image("http://localhost/view", 10)
        assert source.reads == 0
        iterator = response.body_iterator
        assert await anext(iterator) == b"x" * 65536
        assert source.reads == 1
        await iterator.aclose()
        await source.aclose()
        await clients[0].aclose()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["headers", "body", "read", "cancel", "disconnect"])
def test_closes_upstream_even_on_early_disconnect_or_read_error(upstream, failure):
    source, _, clients, _ = upstream
    source.fail = failure == "read"

    async def run():
        response = await image_stream.stream_image("http://localhost/view", 10)
        disconnected = asyncio.Event()
        scope = {"type": "http", "asgi": {"spec_version": "2.0" if failure == "disconnect" else "2.4"}}

        async def receive():
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if failure == "headers" or failure == "body" and message["type"] == "http.response.body":
                raise OSError("browser disconnected")
            if message["type"] == "http.response.body":
                if failure == "cancel":
                    raise asyncio.CancelledError()
                if failure == "disconnect":
                    disconnected.set()
                    await asyncio.Event().wait()

        if failure == "disconnect":
            await response(scope, receive, send)
        else:
            error = {"read": httpx.ReadError, "cancel": asyncio.CancelledError}.get(failure, ClientDisconnect)
            with pytest.raises(error):
                await response(scope, receive, send)
        assert source.closed and clients[0].is_closed
        assert source.reads <= 1

    asyncio.run(run())


@pytest.mark.parametrize("status", [302, 404, 500, None])
def test_open_failures_close_resources_and_return_502(upstream, status):
    source, config, clients, _ = upstream
    config["status"] = status or 200
    config["error"] = status is None

    async def run():
        with pytest.raises(HTTPException) as exc:
            await image_stream.stream_image("http://localhost/view", 10)
        assert exc.value.status_code == 502
        assert clients[0].is_closed
        if status is not None:
            assert source.closed

    asyncio.run(run())


def test_encoded_upstream_is_forwarded_without_length_mismatch(upstream):
    source, config, clients, _ = upstream
    encoded = gzip.compress(b"image")
    source.payload = encoded
    config["headers"] = {"Content-Encoding": "gzip", "Content-Length": str(len(encoded))}

    async def run():
        response = await image_stream.stream_image("http://localhost/view", 10)
        assert response.headers["content-length"] == str(len(encoded))
        assert response.headers["content-encoding"] == "gzip"
        # aiter_raw leaves bytes unchanged even if the server ignores identity.
        assert await anext(response.body_iterator) == encoded
        await source.aclose()
        await clients[0].aclose()

    asyncio.run(run())
