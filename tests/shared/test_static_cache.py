"""Cache freshness across direct and Remote-prefixed UI requests."""

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.staticfiles import StaticFiles

from comfyui_studio.web_common import CachedStaticFiles, CachePolicyMiddleware


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "app" / "static"
    common = tmp_path / "web_common" / "static"
    for directory in (root / "js", root / "css", root / "fonts", common / "js"):
        directory.mkdir(parents=True)
    (root / "js/app.js").write_text("const app = 1;", encoding="utf-8")
    (root / "css/style.css").write_text("body { color: red; }", encoding="utf-8")
    (root / "fonts/font.woff2").write_bytes(b"font-data")
    (common / "js/shared.js").write_text("const shared = 1;", encoding="utf-8")
    (root / "index.html").write_text('''<html><head>
      <link href="css/style.css" rel="stylesheet">
      <link href="https://example.com/external.css" rel="stylesheet">
      </head><body>Привет
      <script src="common/js/shared.js"></script><script src="js/app.js"></script>
      </body></html>''', encoding="utf-8")
    app = FastAPI()
    app.add_middleware(CachePolicyMiddleware)

    @app.get("/api/state.js")
    def state():
        return {"secret": True}

    app.mount("/common", CachedStaticFiles(directory=common))
    app.mount("/media", StaticFiles(directory=root / "fonts"))
    app.mount("/", CachedStaticFiles(directory=root, html=True))
    return app, root


def asset_url(page, path):
    return re.search(r'''(?:src|href)=["'](''' + re.escape(path) + r'''\?v=[0-9a-f]+)["']''', page.text)[1]


@pytest.mark.parametrize("prefix", ["", "/apps/imagine"])
def test_versioned_assets_are_cacheable_and_relative_under_proxy(site, prefix):
    app, _ = site
    if prefix:
        wrapper = FastAPI()
        wrapper.mount(prefix, app)
        app = wrapper
    client = TestClient(app)
    page = client.get(prefix + "/")
    assert "no-store" in page.headers["cache-control"]
    assert "etag" not in page.headers and "last-modified" not in page.headers
    assert "Привет" in page.text
    assert 'href="https://example.com/external.css"' in page.text
    for path in ("js/app.js", "css/style.css", "common/js/shared.js"):
        url = asset_url(page, path)
        assert not url.startswith("/")
        response = client.get(page.url.join(url))
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, max-age=31536000, immutable"
        assert "pragma" not in response.headers


def test_unversioned_assets_revalidate_and_support_304(site):
    app, _ = site
    client = TestClient(app)
    for path in ("/js/app.js", "/fonts/font.woff2", "/common/js/shared.js"):
        first = client.get(path)
        assert first.headers["cache-control"] == "private, no-cache, must-revalidate"
        cached = client.get(path, headers={"If-None-Match": first.headers["etag"]})
        assert cached.status_code == 304 and cached.content == b""
        assert cached.headers["cache-control"] == first.headers["cache-control"]


def test_changed_asset_gets_new_url_and_old_url_cannot_be_immutable(site):
    app, root = site
    client = TestClient(app)
    old_url = asset_url(client.get("/"), "js/app.js")
    first = client.get("/" + old_url)
    (root / "js/app.js").write_text("const app = 'updated version';", encoding="utf-8")
    page = client.get("/", headers={"If-None-Match": '"old-html-validator"'})
    new_url = asset_url(page, "js/app.js")
    assert new_url != old_url and page.status_code == 200
    old = client.get("/" + old_url, headers={"If-None-Match": first.headers["etag"]})
    assert old.status_code == 200 and "updated version" in old.text
    assert "immutable" not in old.headers["cache-control"]
    assert "immutable" in client.get("/" + new_url).headers["cache-control"]


def test_versioned_304_head_and_range_preserve_policy(site):
    app, _ = site
    client = TestClient(app)
    url = "/" + asset_url(client.get("/"), "js/app.js")
    first = client.get(url)
    assert client.head(url).content == b""
    cached = client.get(url, headers={"If-None-Match": first.headers["etag"]})
    assert cached.status_code == 304 and "immutable" in cached.headers["cache-control"]
    partial = client.get(url, headers={"Range": "bytes=0-4"})
    # Older supported Starlette releases return the whole file for Range;
    # newer releases support partial responses. Keep either native behaviour.
    assert partial.status_code in {200, 206}
    if partial.status_code == 206:
        assert partial.content == first.content[:5]
        assert partial.headers["content-range"].startswith("bytes 0-4/")
    else:
        assert partial.content == first.content
    assert "immutable" in partial.headers["cache-control"]
    page = client.get("/")
    head = client.head("/")
    assert head.content == b"" and int(head.headers["content-length"]) == len(page.content)
    assert "no-store" in head.headers["cache-control"]


@pytest.mark.parametrize("url", ["/api/state.js?v=fake", "/media/font.woff2?v=fake", "/missing.js?v=fake"])
def test_api_user_media_and_errors_are_not_cached(site, url):
    app, _ = site
    response = TestClient(app).get(url)
    assert "no-store" in response.headers["cache-control"]


def test_only_local_assets_inside_static_roots_are_versioned(site):
    app, root = site
    outside = root.parent / "private.js"
    outside.write_text("private", encoding="utf-8")
    (root / "index.html").write_text('''<html><head></head><body>
      <script src="../private.js"></script><img src="/media/picture.png">
      <script src="js/app.js?mode=local&amp;v=old#fragment"></script></body></html>''', encoding="utf-8")
    page = TestClient(app).get("/")
    assert 'src="../private.js"' in page.text
    assert 'src="/media/picture.png"' in page.text
    assert "mode=local&amp;v=" in page.text and "#fragment" in page.text
    assert "v=old" not in page.text
