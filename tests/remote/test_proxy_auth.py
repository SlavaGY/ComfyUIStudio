"""
Тесты comfyui_studio/remote/proxy_auth.py -- порядок приоритета
источников токена (заголовок -> кука -> query-параметр, см. докстринг
модуля) и поведение set_token_cookie(). Использует изолированный
device_store (см. isolated_store в test_device_store.py) и простой
дублирующий фейк вместо настоящего fastapi.Request/Response -- эти два
класса используются только как контейнеры атрибутов
(headers/cookies/query_params, set_cookie), полноценный TestClient
здесь не нужен.
"""

import pytest

from comfyui_studio.remote import device_store
from comfyui_studio.remote import proxy_auth as pa


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(device_store, "SHARED_DIR", str(tmp_path))
    monkeypatch.setattr(
        device_store, "DEVICE_STORE_PATH", str(tmp_path / "remote_devices.json")
    )
    yield


class FakeRequest:
    def __init__(self, headers=None, cookies=None, query=None):
        self.headers = headers or {}
        self.cookies = cookies or {}
        self.query_params = query or {}


class FakeResponse:
    def __init__(self):
        self.cookies_set = {}

    def set_cookie(self, name, value, **_kwargs):
        self.cookies_set[name] = value


def _paired_device():
    return device_store.create_device("Тест")


def test_resolves_via_authorization_header():
    device_id, token = _paired_device()
    request = FakeRequest(headers={"authorization": f"Bearer {token}"})

    assert pa.resolve_device_and_token(request) == (device_id, token)


def test_resolves_via_cookie():
    device_id, token = _paired_device()
    request = FakeRequest(cookies={pa.COOKIE_NAME: token})

    assert pa.resolve_device_and_token(request) == (device_id, token)


def test_resolves_via_query_param():
    device_id, token = _paired_device()
    request = FakeRequest(query={"token": token})

    assert pa.resolve_device_and_token(request) == (device_id, token)


def test_header_takes_priority_over_cookie_and_query():
    device_id, good_token = _paired_device()
    request = FakeRequest(
        headers={"authorization": f"Bearer {good_token}"},
        cookies={pa.COOKIE_NAME: "bad-cookie-token"},
        query={"token": "bad-query-token"},
    )

    assert pa.resolve_device_and_token(request) == (device_id, good_token)


def test_no_token_anywhere_returns_none():
    _paired_device()
    assert pa.resolve_device_and_token(FakeRequest()) is None


def test_unknown_token_returns_none():
    _paired_device()
    request = FakeRequest(query={"token": "совсем-не-тот-токен"})
    assert pa.resolve_device_and_token(request) is None


def test_revoked_device_returns_none():
    device_id, token = _paired_device()
    device_store.revoke_device(device_id)
    request = FakeRequest(headers={"authorization": f"Bearer {token}"})

    assert pa.resolve_device_and_token(request) is None


def test_set_token_cookie_sets_expected_cookie():
    response = FakeResponse()
    pa.set_token_cookie(response, "abc123")

    assert response.cookies_set[pa.COOKIE_NAME] == "abc123"
