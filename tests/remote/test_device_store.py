"""
Тесты comfyui_studio/remote/device_store.py -- чистая логика без Qt
(файловое JSON-хранилище устройств, §1.4 дорожной карты). Каждый тест
подменяет SHARED_DIR/DEVICE_STORE_PATH на tmp_path, чтобы не трогать
настоящий %APPDATA%\\ComfyUIStudio\\ на машине, где запускаются тесты.
"""

import json
import os

import pytest

from comfyui_studio.remote import device_store


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(device_store, "SHARED_DIR", str(tmp_path))
    monkeypatch.setattr(
        device_store, "DEVICE_STORE_PATH", str(tmp_path / "remote_devices.json")
    )
    yield


def test_create_device_returns_token_not_stored_in_plaintext():
    device_id, access_token = device_store.create_device("Телефон Логдрасиля")

    assert device_id
    assert access_token
    with open(device_store.DEVICE_STORE_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    assert len(raw) == 1
    # Токен на диске -- только в виде hash, не в открытом виде (§1.3/§1.4).
    assert raw[0]["token_hash"] != access_token
    assert access_token not in json.dumps(raw)


def test_find_device_by_token_roundtrip():
    device_id, access_token = device_store.create_device("Телефон")

    found = device_store.find_device_by_token(access_token)

    assert found is not None
    assert found.device_id == device_id
    assert found.revoked is False


def test_find_device_by_token_unknown_returns_none():
    device_store.create_device("Телефон")

    assert device_store.find_device_by_token("совсем-не-тот-токен") is None


def test_revoke_device_marks_revoked_and_blocks_token_lookup_semantics():
    device_id, access_token = device_store.create_device("Телефон")

    assert device_store.revoke_device(device_id) is True

    found = device_store.find_device_by_token(access_token)
    # Устройство физически остаётся в файле (см. докстринг revoke_device),
    # но помечено revoked=True -- вызывающая сторона (auth.py) сама
    # проверяет этот флаг и превращает его в 401.
    assert found is not None
    assert found.revoked is True


def test_revoke_device_unknown_id_returns_false():
    assert device_store.revoke_device("нет-такого-устройства") is False


def test_list_devices_returns_all_created_devices():
    device_store.create_device("Телефон 1")
    device_store.create_device("Телефон 2")

    devices = device_store.list_devices()

    assert len(devices) == 2
    assert {d.name for d in devices} == {"Телефон 1", "Телефон 2"}


def test_touch_last_seen_sets_timestamp():
    device_id, _ = device_store.create_device("Телефон")
    assert device_store.list_devices()[0].last_seen is None

    device_store.touch_last_seen(device_id)

    assert device_store.list_devices()[0].last_seen is not None


def test_load_raw_survives_corrupted_file():
    os.makedirs(device_store.SHARED_DIR, exist_ok=True)
    with open(device_store.DEVICE_STORE_PATH, "w", encoding="utf-8") as f:
        f.write("это не json{{{")

    # Не должно бросать исключение -- см. комментарий в _load_raw про
    # повреждённый файл: ведём себя так, будто устройств пока нет.
    assert device_store.list_devices() == []


def test_corrupted_file_is_quarantined_not_overwritten():
    """Битый remote_devices.json откладывается в *.corrupt-<время>: иначе
    ближайший create_device затёр бы его и хеши токенов спаренных
    телефонов пропали бы без следа."""
    with open(device_store.DEVICE_STORE_PATH, "w", encoding="utf-8") as f:
        f.write("это не json{{{")

    device_id, _ = device_store.create_device("Телефон")

    assert [d.device_id for d in device_store.list_devices()] == [device_id]
    directory = os.path.dirname(device_store.DEVICE_STORE_PATH)
    kept = [n for n in os.listdir(directory) if ".corrupt-" in n]
    assert len(kept) == 1
    with open(os.path.join(directory, kept[0]), encoding="utf-8") as f:
        assert f.read() == "это не json{{{"


def test_root_that_is_not_a_list_is_treated_as_corrupt():
    with open(device_store.DEVICE_STORE_PATH, "w", encoding="utf-8") as f:
        f.write('{"devices": []}')
    assert device_store.list_devices() == []
    directory = os.path.dirname(device_store.DEVICE_STORE_PATH)
    assert [n for n in os.listdir(directory) if ".corrupt-" in n]


def test_save_leaves_no_temp_files():
    device_store.create_device("Телефон")
    directory = os.path.dirname(device_store.DEVICE_STORE_PATH)
    assert not [n for n in os.listdir(directory) if n.endswith(".tmp")]
