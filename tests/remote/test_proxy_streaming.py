"""Backpressure from the upstream must reach the incoming HTTP body."""

import asyncio
import gzip

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from comfyui_studio.remote import apps_registry, imagine_proxy


@pytest.fixture
def proxy(monkeypatch):
    monkeypatch.setattr(imagine_proxy, "resolve_device_and_token", lambda request: ("dev", "test-token"))
    monkeypatch.setattr(imagine_proxy, "get_app", lambda app_id: apps_registry.RemoteApp(
        app_id, "Test", "/apps/test/", lambda: None
    ))
    monkeypatch.setattr(imagine_proxy.runtime, "imagine_port", 7860)
    app = FastAPI()
    app.include_router(imagine_proxy.router)
    return app


@pytest.mark.parametrize("known_length", [True, False])
def test_upload_is_forwarded_chunk_by_chunk_without_body_buffer(proxy, monkeypatch, known_length):
    async def scenario():
        produced, consumed = [], []
        chunks = [bytes([index]) * (256 * 1024) for index in range(6)]

        async def incoming():
            for index, chunk in enumerate(chunks):
                # The next input chunk cannot be requested before the prior
                # one has reached the upstream transport.
                assert len(consumed) == index
                produced.append(index)
                yield chunk

        class Upstream(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                assert request.headers["content-type"] == "multipart/form-data; boundary=test"
                if known_length:
                    assert request.headers["content-length"] == str(sum(map(len, chunks)))
                    assert "transfer-encoding" not in request.headers
                else:
                    assert request.headers["transfer-encoding"] == "chunked"
                async for chunk in request.stream:
                    if chunk:
                        consumed.append(chunk)
                        assert len(produced) == len(consumed)
                assert consumed == chunks
                return httpx.Response(200, headers={"content-type": "application/json"},
                                      stream=httpx.ByteStream(b'{"ok":true}'))

        async with httpx.AsyncClient(transport=Upstream()) as upstream:
            monkeypatch.setattr(imagine_proxy, "_client", upstream)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy), base_url="http://remote") as client:
                headers = {"content-type": "multipart/form-data; boundary=test"}
                if known_length:
                    headers["content-length"] = str(sum(map(len, chunks)))
                response = await client.post("/apps/imagine/api/upload-image", content=incoming(), headers=headers)
                assert response.json() == {"ok": True}

    asyncio.run(scenario())


def test_html_with_injected_token_has_no_validators_or_encoding(proxy, monkeypatch):
    def handler(request):
        return httpx.Response(200, content=gzip.compress(b"<html><head></head><body>ok</body></html>"),
                              headers={"content-type": "text/html", "content-encoding": "gzip",
                                       "etag": '"upstream"', "last-modified": "yesterday",
                                       "cache-control": "public, max-age=3600"})

    monkeypatch.setattr(imagine_proxy, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    response = TestClient(proxy).get("/apps/imagine/")
    assert "window.__REMOTE_TOKEN__ = 'test-token'" in response.text
    assert response.headers["cache-control"] == "no-store"
    assert all(header not in response.headers for header in ("etag", "last-modified", "content-encoding"))
    assert int(response.headers["content-length"]) == len(response.content)


def test_asset_cache_headers_and_conditional_headers_survive_proxy(proxy, monkeypatch):
    def handler(request):
        assert request.url.params["v"] == "content-hash"
        assert request.headers["if-none-match"] == '"asset"'
        return httpx.Response(304, headers={"etag": '"asset"', "cache-control": "private, max-age=31536000, immutable"},
                              stream=httpx.ByteStream(b""))

    monkeypatch.setattr(imagine_proxy, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    response = TestClient(proxy).get("/apps/imagine/js/app.js?v=content-hash", headers={"if-none-match": '"asset"'})
    assert response.status_code == 304 and not response.content
    assert response.headers["etag"] == '"asset"' and "immutable" in response.headers["cache-control"]


def test_partial_asset_response_keeps_range_and_cache_policy(proxy, monkeypatch):
    def handler(request):
        assert request.headers["range"] == "bytes=0-3"
        return httpx.Response(206, headers={"content-type": "application/octet-stream",
                                          "content-range": "bytes 0-3/100",
                                          "cache-control": "private, no-cache, must-revalidate"},
                              stream=httpx.ByteStream(b"part"))

    monkeypatch.setattr(imagine_proxy, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    response = TestClient(proxy).get("/apps/imagine/fonts/font.woff2", headers={"range": "bytes=0-3"})
    assert response.status_code == 206 and response.content == b"part"
    assert response.headers["content-range"] == "bytes 0-3/100"
    assert response.headers["cache-control"] == "private, no-cache, must-revalidate"


def test_upstream_write_failure_does_not_read_rest_of_upload(proxy, monkeypatch):
    async def scenario():
        produced = []

        async def incoming():
            for index in range(5):
                produced.append(index)
                yield b"x" * 1024

        class FailingUpstream(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                async for chunk in request.stream:
                    if chunk:
                        raise httpx.WriteError("upstream disconnected", request=request)
                raise AssertionError("no body received")

        async with httpx.AsyncClient(transport=FailingUpstream()) as upstream:
            monkeypatch.setattr(imagine_proxy, "_client", upstream)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy), base_url="http://remote") as client:
                response = await client.post("/apps/imagine/api/upload-image", content=incoming())
                assert response.status_code == 502
                assert produced == [0]

    asyncio.run(scenario())
