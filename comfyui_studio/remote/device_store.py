"""
Хранилище устройств, сопряжённых с Remote (§1.3/§1.4 дорожной карты).

%APPDATA%\\ComfyUIStudio\\remote_devices.json — тот же паттерн и та же
общая на весь комплект папка, что уже используют theme.json/
language.json (см. comfyui_studio/shared_theme.py,
comfyui_studio/shared_language.py) — НЕ %APPDATA%\\ComfyUILauncher\\
(та папка — только для файлов самого лаунчера, см.
launcher/core/constants.py).

Токен в открытом виде на диске никогда не хранится — только
sha256(access_token) (см. §1.3: "в device_store сохраняется ТОЛЬКО
hash(access_token)"). Сам access_token отдаётся вызывающему ровно один
раз, в момент создания устройства (create_device), и дальше нигде не
восстановим — потеря токена телефоном означает необходимость pairing
заново.

Формат хранения — плоский список словарей (не JSON-объект по
device_id), чтобы файл оставался человекочитаемым при ручном осмотре
(тот же стиль, что и у остальных файлов в %APPDATA%\\ComfyUIStudio\\).
Запись — через временный файл + os.replace(), чтобы конкурентная запись
(два почти одновременных запроса pairing/revoke) не могла оставить файл
наполовину записанным.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional

SHARED_DIR = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")), "ComfyUIStudio"
)
DEVICE_STORE_PATH = os.path.join(SHARED_DIR, "remote_devices.json")

# Один и тот же процесс-Remote может обрабатывать несколько HTTP-запросов
# параллельно (uvicorn/starlette по умолчанию гоняет sync-обработчики в
# пуле потоков) — обычный threading.Lock, без претензий на межпроцессную
# синхронизацию (Remote — единственный процесс, пишущий этот файл, см.
# §0.1: пакет работает как собственный процесс).
_lock = threading.Lock()


@dataclass
class Device:
    device_id: str
    name: str
    token_hash: str
    created_at: str
    last_seen: Optional[str] = None
    revoked: bool = False
    # НОВОЕ (§Этап 6.5, "Push-уведомления"): токен FCM этого устройства,
    # если Android успел его получить и передать -- либо при pairing
    # (см. PairConfirmRequest.fcm_token), либо позже через
    # POST /fcm-token (см. routes/fcm.py -- FCM-токены иногда
    # переиздаются, см. §Этап 6.5, п.3 "Реализация"). None, если ещё не
    # передан -- fcm.py просто пропускает такое устройство при рассылке.
    fcm_token: Optional[str] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _load_raw() -> list:
    if not os.path.isfile(DEVICE_STORE_PATH):
        return []
    try:
        with open(DEVICE_STORE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        # Повреждённый/нечитаемый файл — тот же принцип, что и у
        # shared_theme.read_shared_theme(): не роняем процесс, просто
        # ведём себя так, будто устройств пока нет. Следующая успешная
        # запись (create_device/revoke_device) перезапишет файл целиком
        # корректным содержимым.
        return []


def _save_raw(devices: list) -> None:
    os.makedirs(SHARED_DIR, exist_ok=True)
    tmp_path = DEVICE_STORE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(devices, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, DEVICE_STORE_PATH)


def create_device(name: str, fcm_token: Optional[str] = None) -> tuple[str, str]:
    """Создаёт новое устройство, возвращает (device_id, access_token).
    access_token — 256-бит случайный, base64url (см. §1.3) — отдаётся
    вызывающему здесь и только здесь; на диск попадает только его хеш.
    fcm_token — см. докстринг Device.fcm_token выше, опционален (Android
    мог не успеть получить его к моменту pairing)."""
    device_id = str(uuid.uuid4())
    access_token = secrets.token_urlsafe(32)  # 32 байта = 256 бит
    device = Device(
        device_id=device_id,
        name=name or "Телефон",
        token_hash=_hash_token(access_token),
        created_at=_now_iso(),
        fcm_token=fcm_token,
    )
    with _lock:
        devices = _load_raw()
        devices.append(asdict(device))
        _save_raw(devices)
    return device_id, access_token


def find_device_by_token(access_token: str) -> Optional[Device]:
    """None, если токен не найден — вызывающая сторона (auth.py) сама
    решает, что делать (401), независимо от причины (нет такого
    устройства вовсе или оно отозвано — на это отдельно проверяет
    device.revoked после получения результата)."""
    token_hash = _hash_token(access_token)
    with _lock:
        devices = _load_raw()
    for d in devices:
        if d.get("token_hash") == token_hash:
            return Device(**d)
    return None


def touch_last_seen(device_id: str) -> None:
    with _lock:
        devices = _load_raw()
        changed = False
        for d in devices:
            if d.get("device_id") == device_id:
                d["last_seen"] = _now_iso()
                changed = True
                break
        if changed:
            _save_raw(devices)


def list_devices() -> list[Device]:
    with _lock:
        devices = _load_raw()
    return [Device(**d) for d in devices]


def update_fcm_token(device_id: str, fcm_token: str) -> bool:
    """True, если устройство найдено (независимо от revoked -- см.
    докстринг ниже про то, почему это не проверяется здесь). Вызывается
    из POST /fcm-token (routes/fcm.py), защищённого require_device --
    device_id туда приходит уже из проверенного Bearer-токена, а не из
    URL/тела запроса, так что устройство физически не может обновить
    чужой токен."""
    with _lock:
        devices = _load_raw()
        found = False
        for d in devices:
            if d.get("device_id") == device_id:
                d["fcm_token"] = fcm_token
                found = True
                break
        if found:
            _save_raw(devices)
    return found


def list_fcm_tokens() -> list[str]:
    """Токены всех НЕотозванных устройств, у которых он вообще есть --
    используется fcm.py при рассылке push на каждое generation.completed/
    generation.error (см. generation_watcher.py). Отозванные устройства
    исключены здесь же, а не в fcm.py, -- та же логика, что и token_hash
    для обычной Bearer-аутентификации (auth.py тоже сам проверяет
    revoked), чтобы это правило не пришлось помнить в каждом вызывающем
    месте по отдельности."""
    with _lock:
        devices = _load_raw()
    return [
        d["fcm_token"]
        for d in devices
        if d.get("fcm_token") and not d.get("revoked", False)
    ]


def revoke_device(device_id: str) -> bool:
    """True, если устройство найдено и помечено отозванным (устройства
    физически не удаляются из файла — история "было и отозвано" полезнее
    молчаливого исчезновения записи, а сам файл не настолько большой,
    чтобы это стало проблемой)."""
    with _lock:
        devices = _load_raw()
        found = False
        for d in devices:
            if d.get("device_id") == device_id:
                d["revoked"] = True
                found = True
                break
        if found:
            _save_raw(devices)
    return found
