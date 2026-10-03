"""
ssh_config_store.py -- состояние SSH-доступа "вне дома".

ЖИВОЙ ТЕСТ 2026-09-27 нашёл баг: изначально это было только в памяти
процесса Remote (как pairing.py::PairingManager) -- пользователь прогнал
мастер настройки в Studio, но телефон так и не получил ключ, потому что
Remote успел перезапуститься (разбирались с не связанной проблемой --
работающий на ПК VPN перехватывал трафик) МЕЖДУ пушем из Studio и
следующим открытием терминала на телефоне. Чисто оперативная память
переживает перезапуск процесса ровно 0 раз.

Персистентность -- %APPDATA%\\ComfyUIStudio\\remote_ssh_config.json,
тот же паттерн, та же общая папка и тот же приём (temp-файл +
os.replace, "битый файл -- ведём себя как будто его нет, не роняем
процесс"), что и remote_devices.json (см. её докстринг и
_load_raw/_save_raw в device_store.py) -- token_hash там персистится
по той же причине: pairing тоже обязан переживать перезапуск Remote, а
не требовать пересопряжения на каждый чих.

Приватный ключ здесь хранится в открытом виде -- В ОТЛИЧИЕ от
access_token в device_store.py, который хранится только как
sha256-хеш. Это не ослабление модели угроз, а необходимость: в отличие
от access_token (сервер только ПРОВЕРЯЕТ его, хеша для этого
достаточно), приватный ключ сервер обязан повторно ОТДАВАТЬ телефону по
запросу -- хеш для этого бесполезен. Компенсируется тем, что сама
SSH-учётная запись изначально спроектирована максимально урезанной (см.
Setup-SshAccess.ps1 -- только проброс портов, ForceCommand echo, без
интерактивного шелла, без sudo/администраторских прав).

Источник данных -- Studio UI (ui/settings/remote_page.py "Доступ вне
дома (SSH)"), который после успешного прогона
tools/ssh_setup/Setup-SshAccess.ps1 пушит результат сюда через
POST /internal/ssh-config (см. routes/internal.py, тот же
loopback-only принцип, что и у generation-progress).

Потребитель -- сопряжённый телефон, дёргающий GET /ssh/config при
каждом открытии терминала (routes/ssh_config.py) -- ДО решения,
поднимать ли SshTunnelService: приватный ключ уходит на телефон тем же
уже аутентифицированным Bearer-токеном каналом, которым идут все
остальные вызовы Remote API, отдельного "защищённого канала" для этого
заводить не пришлось (см. переписку про "тот же код, что и для
синхронизации").
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from typing import Optional

SHARED_DIR = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")), "ComfyUIStudio"
)
SSH_CONFIG_STORE_PATH = os.path.join(SHARED_DIR, "remote_ssh_config.json")

# Тот же приём, что и в device_store.py -- обычный threading.Lock, без
# претензий на межпроцессную синхронизацию (Remote -- единственный
# процесс, пишущий этот файл).
_lock = threading.Lock()


@dataclass
class SshConfig:
    username: str
    ssh_port: int
    host: str
    private_key: str
    key_version: int


def _load_raw() -> Optional[dict]:
    if not os.path.isfile(SSH_CONFIG_STORE_PATH):
        return None
    try:
        with open(SSH_CONFIG_STORE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        # Повреждённый/нечитаемый файл -- тот же принцип, что и в
        # device_store.py::_load_raw: не роняем процесс, ведём себя так,
        # будто конфигурации ещё не было. Следующий push() (повторный
        # прогон мастера в Studio) перезапишет файл целиком корректным
        # содержимым.
        return None


def _save_raw(data: dict) -> None:
    os.makedirs(SHARED_DIR, exist_ok=True)
    tmp_path = SSH_CONFIG_STORE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, SSH_CONFIG_STORE_PATH)


class SshConfigStore:
    def push(self, username: str, ssh_port: int, host: str, private_key: str) -> int:
        """Вызывается из routes/internal.py при POST /internal/ssh-config.
        Возвращает новую key_version -- растёт при КАЖДОМ пуше, даже если
        содержимое совпадает с прошлым разом (простое повторное нажатие
        кнопки в Studio без реальных изменений тоже должно довести до
        телефона свежий приватный ключ -- ssh-keygen в PS1-скрипте
        каждый раз генерирует новую пару, старый ключ на телефоне сразу
        становится нерабочим, значит версия обязана вырасти)."""
        with _lock:
            current = _load_raw()
            version = (current.get("key_version", 0) if current else 0) + 1
            data = {
                "username": username,
                "ssh_port": ssh_port,
                "host": host,
                "private_key": private_key,
                "key_version": version,
            }
            _save_raw(data)
            return version

    def current(self) -> Optional[SshConfig]:
        with _lock:
            data = _load_raw()
        if not data:
            return None
        try:
            return SshConfig(**data)
        except TypeError:
            # Формат файла из более старой/новой версии не совпадает с
            # текущим SshConfig -- тот же принцип, что и у битого файла
            # в _load_raw: ведём себя как будто конфигурации нет, а не
            # падаем с 500 на GET /ssh/config.
            return None


# Один экземпляр на процесс Remote -- см. докстринг модуля.
ssh_config_store = SshConfigStore()
