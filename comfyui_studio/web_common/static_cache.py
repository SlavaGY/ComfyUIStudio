"""Content-versioned UI assets; HTML and API responses are never stored."""

import hashlib
import html
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders, QueryParams
from starlette.responses import FileResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

NO_STORE = "no-store, no-cache, must-revalidate, max-age=0"
REVALIDATE = "private, no-cache, must-revalidate"
IMMUTABLE = "private, max-age=31536000, immutable"
ASSET_SUFFIXES = {
    ".js", ".css", ".woff", ".woff2", ".ttf", ".otf", ".ico", ".svg",
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
}
_TAGS = re.compile(r"<(?:script|link|img)\b[^>]*>", re.IGNORECASE)
_URL_ATTRIBUTE = re.compile(r'''\b(src|href)\s*=\s*(["'])(.*?)\2''', re.IGNORECASE)


@lru_cache(maxsize=512)
def _fingerprint(path: str, mtime_ns: int, ctime_ns: int, size: int) -> str:
    # Metadata keys invalidate the cache when a developer edits an asset.
    with open(path, "rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _version(path: Path) -> str:
    metadata = path.stat()
    return _fingerprint(str(path), metadata.st_mtime_ns, metadata.st_ctime_ns, metadata.st_size)


class CachePolicyMiddleware:
    """Apply a safe default without wrapping/buffering request or response bodies."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_policy(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("Cache-Control", NO_STORE)
                if "no-store" in headers["Cache-Control"]:
                    headers["Pragma"] = "no-cache"
            await send(message)

        await self.app(scope, receive, send_with_policy)


class CachedStaticFiles(StaticFiles):
    """Version local HTML asset references and cache only matching asset versions.

    Unversioned files (including CSS font URLs) revalidate using Starlette's
    ETag/Last-Modified handling. Dynamic /media and API routes use other handlers.
    """

    def __init__(self, *, directory: str | Path, html: bool = False):
        directory = Path(directory).resolve()
        super().__init__(directory=directory, html=html)
        self._common_directory = Path(directory).parent.parent / "web_common" / "static"

    def file_response(self, full_path, stat_result, scope, status_code=200):
        if Path(full_path).suffix.lower() == ".html":
            # HTML references change when *any* referenced asset changes. Its
            # original file's mtime/ETag cannot validate the rendered page.
            return FileResponse(full_path, status_code=status_code, stat_result=stat_result)
        return super().file_response(full_path, stat_result, scope, status_code)

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        return await run_in_threadpool(self._prepare, path, scope, response)

    def _asset_url(self, url: str, html_path: Path) -> str:
        parsed = urlsplit(html.unescape(url))
        if parsed.scheme or parsed.netloc or parsed.path.startswith(("/", "\\")):
            return url
        if Path(parsed.path).suffix.lower() not in ASSET_SUFFIXES:
            return url
        if parsed.path.startswith("common/"):
            root, relative = self._common_directory, parsed.path[len("common/"):]
        else:
            root, relative = Path(str(self.directory)), parsed.path
            relative = str(html_path.parent.relative_to(root) / relative)
        try:
            asset = (root / relative).resolve()
            if not asset.is_relative_to(root.resolve()) or not asset.is_file():
                return url
            query = [(key, value) for key, value in parse_qsl(parsed.query) if key != "v"]
            query.append(("v", _version(asset)))
        except OSError:
            return url
        return html.escape(urlunsplit(parsed._replace(query=urlencode(query))), quote=True)

    def _prepare(self, path: str, scope: Scope, response: Response) -> Response:
        if isinstance(response, FileResponse) and response.media_type == "text/html":
            html_path = Path(response.path)
            source = html_path.read_text(encoding="utf-8")

            def version_tag(tag: re.Match) -> str:
                return _URL_ATTRIBUTE.sub(
                    lambda attr: f"{attr[1]}={attr[2]}{self._asset_url(attr[3], html_path)}{attr[2]}",
                    tag[0],
                )

            body = _TAGS.sub(version_tag, source).encode("utf-8")
            return Response(
                content=b"" if scope["method"] == "HEAD" else body,
                status_code=response.status_code, media_type="text/html",
                headers={"Cache-Control": NO_STORE, "Content-Length": str(len(body))},
            )

        if response.status_code in {200, 206, 304}:
            response.headers["Cache-Control"] = REVALIDATE
            version = QueryParams(scope.get("query_string", b"")).get("v")
            if version:
                full_path, metadata = self.lookup_path(path)
                if metadata is not None and Path(full_path).suffix.lower() in ASSET_SUFFIXES:
                    if version == _version(Path(full_path)):
                        response.headers["Cache-Control"] = IMMUTABLE
        return response
