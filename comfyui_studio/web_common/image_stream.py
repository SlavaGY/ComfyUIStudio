"""Stream ComfyUI images without buffering the complete response in RAM."""

import httpx
from anyio import CancelScope
from fastapi import HTTPException
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send


class ImageStreamingResponse(StreamingResponse):
    def __init__(self, upstream: httpx.Response, client: httpx.AsyncClient):
        self.upstream = upstream
        self.client = client
        headers = {
            name: upstream.headers[name]
            for name in ("content-type", "content-length", "content-encoding")
            if name in upstream.headers
        }
        headers.setdefault("content-type", "image/png")
        super().__init__(upstream.aiter_raw(), headers=headers)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Also runs if sending headers fails, before the iterator is started.
            with CancelScope(shield=True):
                try:
                    await self.upstream.aclose()
                finally:
                    await self.client.aclose()


async def stream_image(url: str, timeout: float) -> ImageStreamingResponse:
    client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
    upstream = None
    transferred = False
    try:
        request = client.build_request("GET", url, headers={"Accept-Encoding": "identity"})
        upstream = await client.send(request, stream=True)
        upstream.raise_for_status()
        response = ImageStreamingResponse(upstream, client)
        transferred = True
        return response
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Не удалось получить изображение из ComfyUI") from exc
    finally:
        # Successful responses own the connection until their ASGI call finishes.
        if not transferred:
            with CancelScope(shield=True):
                try:
                    if upstream is not None:
                        await upstream.aclose()
                finally:
                    await client.aclose()
