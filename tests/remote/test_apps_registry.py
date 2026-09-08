"""
Тесты comfyui_studio/remote/apps_registry.py -- чистая логика, без
FastAPI/сети. Реестр модуль-уровневый (см. его докстринг), поэтому
каждый тест сбрасывает его в начале через clear_registry().
"""

import pytest

from comfyui_studio.remote import apps_registry as ar


@pytest.fixture(autouse=True)
def clean_registry():
    ar.clear_registry()
    yield
    ar.clear_registry()


def test_empty_registry_has_no_available_apps():
    assert ar.list_available_apps() == []
    assert ar.get_app("imagine") is None


def test_registered_app_hidden_while_proxy_target_returns_none():
    ar.register_app(
        ar.RemoteApp(id="imagine", name="Imagine", path="/apps/imagine/", proxy_target=lambda: None)
    )

    # Зарегистрировано -- get_app() его видит...
    assert ar.get_app("imagine") is not None
    # ...но недоступно прямо сейчас -- в списке для телефона его нет.
    assert ar.list_available_apps() == []


def test_registered_app_appears_once_proxy_target_resolves():
    state = {"port": None}
    ar.register_app(
        ar.RemoteApp(
            id="imagine",
            name="Imagine",
            path="/apps/imagine/",
            proxy_target=lambda: (f"http://127.0.0.1:{state['port']}" if state["port"] else None),
        )
    )
    assert ar.list_available_apps() == []

    state["port"] = 7860
    apps = ar.list_available_apps()

    assert len(apps) == 1
    assert apps[0].id == "imagine"
    assert apps[0].proxy_target() == "http://127.0.0.1:7860"


def test_register_app_overwrites_existing_id():
    ar.register_app(
        ar.RemoteApp(id="imagine", name="Old name", path="/apps/imagine/", proxy_target=lambda: None)
    )
    ar.register_app(
        ar.RemoteApp(
            id="imagine", name="Imagine", path="/apps/imagine/", proxy_target=lambda: "http://127.0.0.1:7860"
        )
    )

    apps = ar.list_available_apps()
    assert len(apps) == 1
    assert apps[0].name == "Imagine"
