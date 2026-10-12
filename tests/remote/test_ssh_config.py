"""
Тесты цепочки SSH-доступа "вне дома" на стороне Remote (этап 3 аудита):
ssh_config_store.py (персистентность и key_version), POST
/internal/ssh-config (только loopback) и GET /ssh/config (Bearer-токен
устройства). Хранилища устройств и SSH-конфига подменяются на tmp_path --
настоящий %APPDATA%\\ComfyUIStudio не трогается. Lifespan приложения
(mDNS, GenerationWatcher) не запускается: TestClient используется без
`with`.
"""

import json
import logging
import os

import pytest
from fastapi.testclient import TestClient

from comfyui_studio.remote import device_store
from comfyui_studio.remote import ssh_config_store as store_module
from comfyui_studio.remote.app import API_PREFIX, app
from comfyui_studio.remote.ssh_config_store import SshConfigStore

KEY_BODY = "SECRET-KEY-MATERIAL-0123456789"
PRIVATE_KEY = f"-----BEGIN OPENSSH PRIVATE KEY-----\n{KEY_BODY}\n-----END OPENSSH PRIVATE KEY-----\n"

PUSH_URL = f"{API_PREFIX}/internal/ssh-config"
GET_URL = f"{API_PREFIX}/ssh/config"


def _push_body(**over):
    body = {
        "username": "comfyui-ssh",
        "ssh_port": 2222,
        "host": "2001:db8::5",
        "private_key": PRIVATE_KEY,
    }
    body.update(over)
    return body


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(device_store, "SHARED_DIR", str(tmp_path))
    monkeypatch.setattr(
        device_store, "DEVICE_STORE_PATH", str(tmp_path / "remote_devices.json")
    )
    monkeypatch.setattr(store_module, "SHARED_DIR", str(tmp_path))
    monkeypatch.setattr(
        store_module, "SSH_CONFIG_STORE_PATH", str(tmp_path / "remote_ssh_config.json")
    )
    return tmp_path


def _from_loopback(asgi_app):
    """TestClient(client=...) есть не во всех Starlette (fastapi 0.110 его не
    знает), поэтому подменяем адрес клиента обёрткой над ASGI-приложением."""

    async def wrapper(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = {**scope, "client": ("127.0.0.1", 50000)}
        await asgi_app(scope, receive, send)

    return wrapper


@pytest.fixture
def studio():
    """Клиент "с самого ПК" -- 127.0.0.1, как Studio UI."""
    return TestClient(_from_loopback(app))


@pytest.fixture
def lan_client():
    """Клиент не с loopback (TestClient по умолчанию представляется
    хостом "testclient")."""
    return TestClient(app)


@pytest.fixture
def phone_headers():
    _device_id, token = device_store.create_device("Тестовый телефон")
    return {"Authorization": f"Bearer {token}"}


# -- ssh_config_store ----------------------------------------------------------


def test_push_grows_key_version_even_for_identical_content():
    store = SshConfigStore()

    versions = [store.push("u", 2222, "h", PRIVATE_KEY) for _ in range(3)]

    assert versions == [1, 2, 3]
    assert store.current().key_version == 3


def test_current_is_none_without_file():
    assert SshConfigStore().current() is None


def test_push_then_current_roundtrip():
    store = SshConfigStore()
    store.push("comfyui-ssh", 2300, "2001:db8::5", PRIVATE_KEY)

    config = store.current()

    assert (config.username, config.ssh_port, config.host) == ("comfyui-ssh", 2300, "2001:db8::5")
    assert config.private_key == PRIVATE_KEY


def test_corrupt_store_file_behaves_like_missing_and_push_recovers(isolated):
    path = isolated / "remote_ssh_config.json"
    path.write_text("{not json", encoding="utf-8")
    store = SshConfigStore()

    assert store.current() is None
    assert store.push("u", 2222, "h", PRIVATE_KEY) == 1
    assert store.current().key_version == 1


def test_store_file_with_foreign_shape_is_ignored(isolated):
    path = isolated / "remote_ssh_config.json"
    path.write_text(json.dumps({"something": "else"}), encoding="utf-8")

    assert SshConfigStore().current() is None


def test_store_file_with_wrong_types_is_ignored(isolated):
    path = isolated / "remote_ssh_config.json"
    path.write_text(
        json.dumps(
            {
                "username": "u",
                "ssh_port": "2222",  # строка вместо числа
                "host": "h",
                "private_key": PRIVATE_KEY,
                "key_version": 1,
            }
        ),
        encoding="utf-8",
    )

    assert SshConfigStore().current() is None


def test_push_survives_non_integer_key_version_in_file(isolated):
    path = isolated / "remote_ssh_config.json"
    path.write_text(json.dumps({"key_version": "abc"}), encoding="utf-8")

    assert SshConfigStore().push("u", 2222, "h", PRIVATE_KEY) == 1


def test_push_leaves_no_temp_file(isolated):
    SshConfigStore().push("u", 2222, "h", PRIVATE_KEY)

    names = os.listdir(isolated)
    assert "remote_ssh_config.json" in names
    assert not [n for n in names if n.endswith(".tmp")]


@pytest.mark.skipif(os.name == "nt", reason="права POSIX; на Windows доступ задаёт ACL профиля")
def test_store_file_is_private_on_posix(isolated):
    SshConfigStore().push("u", 2222, "h", PRIVATE_KEY)

    mode = os.stat(isolated / "remote_ssh_config.json").st_mode & 0o777
    assert mode == 0o600


# -- GET /ssh/config -----------------------------------------------------------


def test_get_without_token_is_401(lan_client):
    assert lan_client.get(GET_URL).status_code == 401


def test_get_with_unknown_token_is_401(lan_client):
    response = lan_client.get(GET_URL, headers={"Authorization": "Bearer nope"})

    assert response.status_code == 401


def test_get_with_revoked_device_is_401(lan_client):
    device_id, token = device_store.create_device("Телефон")
    device_store.revoke_device(device_id)

    response = lan_client.get(GET_URL, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


def test_get_without_config_is_unavailable(lan_client, phone_headers):
    response = lan_client.get(GET_URL, headers=phone_headers)

    assert response.status_code == 200
    assert response.json() == {
        "available": False,
        "key_version": None,
        "username": None,
        "host": None,
        "ssh_port": None,
        "private_key": None,
    }


def test_get_with_empty_host_is_unavailable(lan_client, phone_headers):
    SshConfigStore().push("u", 2222, "", PRIVATE_KEY)

    response = lan_client.get(GET_URL, headers=phone_headers)

    assert response.json()["available"] is False
    assert response.json()["private_key"] is None


def test_get_after_post_returns_data_and_is_not_cacheable(studio, lan_client, phone_headers):
    assert studio.post(PUSH_URL, json=_push_body()).status_code == 200

    response = lan_client.get(GET_URL, headers=phone_headers)

    assert response.status_code == 200
    assert response.json() == {
        "available": True,
        "key_version": 1,
        "username": "comfyui-ssh",
        "host": "2001:db8::5",
        "ssh_port": 2222,
        "private_key": PRIVATE_KEY,
    }
    assert response.headers["cache-control"] == "no-store"


# -- POST /internal/ssh-config -------------------------------------------------


def test_post_from_non_loopback_is_rejected_and_store_untouched(lan_client):
    response = lan_client.post(PUSH_URL, json=_push_body())

    assert response.status_code == 403
    assert SshConfigStore().current() is None


def test_post_returns_growing_key_version(studio):
    first = studio.post(PUSH_URL, json=_push_body())
    second = studio.post(PUSH_URL, json=_push_body())

    assert first.json() == {"key_version": 1}
    assert second.json() == {"key_version": 2}


def test_post_strips_whitespace_around_host(studio):
    studio.post(PUSH_URL, json=_push_body(host="  2001:db8::5 "))

    assert SshConfigStore().current().host == "2001:db8::5"


def test_post_accepts_empty_host(studio):
    response = studio.post(PUSH_URL, json=_push_body(host=""))

    assert response.status_code == 200
    assert SshConfigStore().current().host == ""


def test_post_rejects_value_that_is_not_a_private_key_without_echoing_it(studio):
    response = studio.post(PUSH_URL, json=_push_body(private_key="garbage-" + KEY_BODY))

    assert response.status_code == 422
    assert KEY_BODY not in response.text
    assert SshConfigStore().current() is None


@pytest.mark.parametrize("port", [0, 65536, -1])
def test_post_rejects_invalid_port(studio, port):
    assert studio.post(PUSH_URL, json=_push_body(ssh_port=port)).status_code == 422


def test_post_does_not_log_private_key(studio, caplog):
    with caplog.at_level(logging.DEBUG):
        studio.post(PUSH_URL, json=_push_body())

    assert KEY_BODY not in caplog.text


def test_routes_are_registered_under_api_prefix(lan_client):
    # Новые версии FastAPI хранят в app.routes обёртки include_router без
    # .path -- проверяем поведением: 404 значило бы, что роута нет.
    assert lan_client.get(GET_URL).status_code == 401
    assert lan_client.post(PUSH_URL, json=_push_body()).status_code == 403
