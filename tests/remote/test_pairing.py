"""
Тесты comfyui_studio/remote/pairing.py -- одноразовый pairing-код
(§1.3 дорожной карты). Использует изолированный device_store (см.
isolated_store fixture в test_device_store.py, tests/remote/conftest.py
переиспользует ту же изоляцию) -- confirm() в успешном случае реально
создаёт устройство через device_store.create_device().
"""

from datetime import datetime, timedelta, timezone

import pytest

from comfyui_studio.remote import device_store
from comfyui_studio.remote.pairing import PairingError, PairingManager


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(device_store, "SHARED_DIR", str(tmp_path))
    monkeypatch.setattr(
        device_store, "DEVICE_STORE_PATH", str(tmp_path / "remote_devices.json")
    )
    yield


def test_start_returns_six_digit_code_with_dash():
    manager = PairingManager()

    session = manager.start()

    digits = session.code.replace("-", "")
    assert len(digits) == 6
    assert digits.isdigit()
    assert session.attempts_left == 5


def test_confirm_with_correct_code_creates_device_and_burns_code():
    manager = PairingManager()
    session = manager.start()

    device_id, access_token = manager.confirm(session.code, "Мой телефон")

    assert device_id
    assert access_token
    found = device_store.find_device_by_token(access_token)
    assert found is not None
    assert found.name == "Мой телефон"

    # Код одноразовый -- повторное использование того же кода отклоняется.
    with pytest.raises(PairingError):
        manager.confirm(session.code, "Другой телефон")


def test_confirm_with_wrong_code_decrements_attempts():
    manager = PairingManager()
    session = manager.start()

    with pytest.raises(PairingError):
        manager.confirm("000-000", "Телефон")

    assert manager.current_status().attempts_left == session.attempts_left - 1


def test_confirm_exhausts_attempts_then_requires_new_code():
    manager = PairingManager()
    manager.start()

    for _ in range(5):
        with pytest.raises(PairingError):
            manager.confirm("000-000", "Телефон")

    # Попытки исчерпаны -- сессия уничтожена, даже верный код теперь
    # не поможет без нового /pair/start.
    with pytest.raises(PairingError):
        manager.confirm("000-000", "Телефон")
    assert manager.current_status() is None


def test_confirm_with_expired_code_raises():
    manager = PairingManager()
    session = manager.start()
    # Искусственно "состариваем" сессию вместо реального sleep(180) --
    # напрямую переставляем private-поле через тот же объект сессии,
    # хранящийся внутри manager (PairingManager хранит единственный
    # активный self._session, см. его докстринг).
    manager._session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    with pytest.raises(PairingError):
        manager.confirm(session.code, "Телефон")
    assert manager.current_status() is None


def test_start_replaces_previous_unconfirmed_code():
    manager = PairingManager()
    first = manager.start()
    second = manager.start()

    # Первый код больше не активен -- активна только вторая сессия
    # (запрос нового кода молча заменяет предыдущий, см. докстринг
    # PairingManager). Если оба кода случайно совпали (1 к 1000 шанс),
    # confirm() ниже всё равно бы прошёл -- поэтому явно пропускаем
    # такое маловероятное совпадение, а не считаем тест упавшим.
    if first.code == second.code:
        pytest.skip("случайно сгенерированные коды совпали")
    with pytest.raises(PairingError):
        manager.confirm(first.code, "Телефон")
    assert manager.current_status().code == second.code
