"""comfyui_studio.web_common: общий слой веб-приложений Imagine и Imagine Pony.

Проверяется на «голом» FastAPI-приложении, без импорта самих приложений
(их main.py при импорте создаёт каталоги данных); что оба приложения
действительно подключают общий слой -- сверяется по тексту их main.py.
"""

import ast
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from comfyui_studio import web_common  # noqa: E402

PACKAGE = Path(web_common.__file__).resolve().parent.parent


class _Store:
    """Подставной shared_theme / shared_language: хранит значение в памяти."""

    def __init__(self, value=None):
        self.value = value
        self.writes = []

    def read_shared_theme(self):
        return self.value

    def write_shared_theme(self, value):
        self.writes.append(value)
        self.value = value

    def read_shared_language(self):
        return self.value

    def write_shared_language(self, value):
        self.writes.append(value)
        self.value = value


@pytest.fixture
def stores(monkeypatch):
    theme, language = _Store("Nord"), _Store("en")
    monkeypatch.setattr(web_common, "shared_theme", theme)
    monkeypatch.setattr(web_common, "shared_language", language)
    return theme, language


@pytest.fixture
def client():
    app = FastAPI()
    web_common.register_ui_prefs(app)
    web_common.mount_common_static(app)
    return TestClient(app)


def test_ui_prefs_returns_shared_theme_and_language(client, stores):
    assert client.get("/api/ui-prefs").json() == {"theme": "Nord", "language": "en"}


def test_ui_prefs_put_writes_only_what_was_sent(client, stores):
    theme, language = stores
    assert client.put("/api/ui-prefs", json={"theme": "Dracula"}).json() == {"ok": True}
    assert theme.writes == ["Dracula"] and language.writes == []
    assert client.put("/api/ui-prefs", json={"language": "ru"}).json() == {"ok": True}
    assert language.writes == ["ru"]


def test_ui_prefs_put_ignores_empty_values(client, stores):
    theme, language = stores
    client.put("/api/ui-prefs", json={"theme": "", "language": None})
    assert theme.writes == [] and language.writes == []


def test_ui_prefs_without_studio_files_answers_nulls(client, monkeypatch):
    """Приложение запущено отдельно от Studio (нет shared_*): не ошибка."""
    monkeypatch.setattr(web_common, "shared_theme", None)
    monkeypatch.setattr(web_common, "shared_language", None)
    assert client.get("/api/ui-prefs").json() == {"theme": None, "language": None}
    assert client.put("/api/ui-prefs", json={"theme": "Nord", "language": "en"}).json() == {"ok": True}


def test_common_static_serves_the_i18n_engine(client):
    response = client.get("/common/js/i18n_engine.js")
    assert response.status_code == 200
    assert "createI18n" in response.text


def test_mount_common_static_registers_the_common_route():
    """Корневой StaticFiles("/") перехватил бы и /common, поэтому в
    приложениях mount_common_static() вызывается раньше него (см. тест
    test_apps_use_the_shared_layer); здесь -- что маршрут вообще появился."""
    app = FastAPI()
    web_common.mount_common_static(app)
    assert [route.path for route in app.routes if getattr(route, "name", "") == "common"] == ["/common"]


def test_no_cache_middleware_disables_browser_cache():
    app = FastAPI()
    app.add_middleware(web_common.NoCacheMiddleware)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    headers = TestClient(app).get("/ping").headers
    assert headers["cache-control"] == "no-store, no-cache, must-revalidate, max-age=0"
    assert headers["pragma"] == "no-cache"


@pytest.mark.parametrize("package", ["imagine", "imagine_pony", "web_common"])
def test_static_dir_in_sources(package):
    path = web_common.static_dir(package)
    assert path == PACKAGE / package / "static"
    assert (path / ("index.html" if package != "web_common" else "js")).exists()


def test_static_dir_in_frozen_build(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert web_common.static_dir("imagine") == tmp_path / "comfyui_studio" / "imagine" / "static"


@pytest.mark.parametrize("app_dir", ["imagine", "imagine_pony"])
def test_apps_use_the_shared_layer(app_dir):
    """main.py обоих приложений: общий middleware, статика и ui-prefs из
    web_common, /common монтируется ДО корневого "/"."""
    source = (PACKAGE / app_dir / "backend" / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "comfyui_studio.web_common"
        for alias in node.names
    }
    assert imported == {"NoCacheMiddleware", "mount_common_static", "register_ui_prefs", "static_dir"}

    # собственных копий больше нет
    assert not [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "NoCacheMiddleware"]
    assert '"/api/ui-prefs"' not in source

    calls = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            calls.setdefault(node.func.id, []).append(node.lineno)
    assert calls["register_ui_prefs"] and calls["mount_common_static"] and calls["static_dir"]

    root_mounts = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "mount"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "/"
    ]
    assert len(root_mounts) == 1
    assert max(calls["mount_common_static"]) < root_mounts[0]
