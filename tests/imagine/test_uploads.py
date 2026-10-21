"""Uploads use bounded reads and publish a file only after a successful copy."""

import io

import pytest
from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient
from starlette.staticfiles import StaticFiles

from comfyui_studio.imagine.backend import main
from comfyui_studio.web_common import CachePolicyMiddleware


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    directory = tmp_path / "uploads"
    directory.mkdir()
    monkeypatch.setattr(main.config_store, "UPLOADS_DIR", directory)
    monkeypatch.setattr(main, "DEV_MODE", True)
    return directory


class BoundedSource(io.BytesIO):
    def read(self, size=-1):
        assert 0 < size <= 1024 * 1024, "unbounded read"
        return super().read(size)


def test_copy_large_spooled_file_with_bounded_reads(uploads):
    payload = b"image data" * 400000
    with BoundedSource(payload) as source:
        result = main.upload_image(UploadFile(filename="picture.PNG", file=source))
    assert result["url"].startswith("media/") and result["url"].endswith(".png")
    destination = uploads / result["url"].split("/")[1]
    assert destination.read_bytes() == payload
    assert list(uploads.iterdir()) == [destination]


def test_failed_copy_cleans_partial_and_does_not_publish_file(uploads):
    class FailingSource(BoundedSource):
        def read(self, size=-1):
            if self.tell():
                raise OSError("disk read failed")
            return super().read(size)

    with FailingSource(b"x" * (2 * 1024 * 1024)) as source:
        with pytest.raises(OSError, match="disk read failed"):
            main.upload_image(UploadFile(filename="picture.png", file=source))
    assert list(uploads.iterdir()) == []


def test_http_multipart_upload_preserves_relative_url_and_no_store(uploads):
    app = FastAPI()
    app.add_middleware(CachePolicyMiddleware)
    app.post("/api/upload-image")(main.upload_image)
    app.mount("/media", StaticFiles(directory=uploads))
    client = TestClient(app)
    payload = b"image-data" * 200000
    response = client.post("/api/upload-image", files={"file": ("picture.webp", payload, "image/webp")})
    assert response.status_code == 200 and "no-store" in response.headers["cache-control"]
    saved = client.get("/" + response.json()["url"])
    assert saved.content == payload and "no-store" in saved.headers["cache-control"]


def test_rejected_uploads_do_not_create_files(uploads, monkeypatch):
    app = FastAPI()
    app.post("/api/upload-image")(main.upload_image)
    client = TestClient(app)
    assert client.post("/api/upload-image", files={"file": ("bad.txt", b"text")}).status_code == 400
    monkeypatch.setattr(main, "DEV_MODE", False)
    assert client.post("/api/upload-image", files={"file": ("picture.png", b"png")}).status_code == 403
    assert not list(uploads.iterdir())
