"""
Pairing-протокол — §1.3 дорожной карты (без изменений от исходной
версии 1 роадмапа):

    Studio UI: "Подключить телефон"
       -> POST /pair/start (без токена, локальный вызов -- см.
          local_guard.py)
       -> код: криптографически случайные 6 цифр, TTL 120-300 сек,
          <=5 попыток

    Android (нативный экран, ещё не реализован -- этап 6): пользователь
    вводит код
       -> POST /pair/confirm {code, device_name}
       -> код уничтожается сразу после успеха (одноразовый)
       -> генерируются device_id + access_token, в device_store
          сохраняется ТОЛЬКО hash(access_token)
       -> access_token отдаётся один раз в ответе

Хранит ровно один активный код одновременно: запрос нового кода
(pair/start) молча заменяет предыдущий, ещё не подтверждённый. Личное
использование (см. §0 дорожной карты) не предполагает нескольких
одновременных попыток pairing разных телефонов -- усложнять до
словаря {code: session} пока незачем; если это понадобится позже
(семейный доступ, несколько устройств сразу), это локализованная
переделка внутри одного этого класса, наружу (routes/) ничего не
протекает.
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from .device_store import create_device

CODE_TTL_SECONDS = 180  # середина диапазона 120-300, см. §1.3
MAX_ATTEMPTS = 5


class PairingError(Exception):
    """Ожидаемая ошибка pairing (код неверный/истёк/попытки исчерпаны)
    -- routes/pairing_routes.py превращает её в HTTP 400 с тем же
    текстом, отдельно от неожиданных исключений (которые остаются
    500-ми)."""


@dataclass
class PairingSession:
    code: str
    expires_at: datetime
    attempts_left: int = MAX_ATTEMPTS


class PairingManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session: Optional[PairingSession] = None

    def start(self) -> PairingSession:
        # secrets.randbelow -- криптографически случайный источник (не
        # random.randint), см. требование §1.3 "криптографически
        # случайные 6 цифр". Формат "ddd-ddd" -- только для читаемости
        # на экране Studio UI/телефона, сравнение в confirm() идёт по
        # полной строке с учётом дефиса.
        code = f"{secrets.randbelow(1000):03d}-{secrets.randbelow(1000):03d}"
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=CODE_TTL_SECONDS)
        session = PairingSession(code=code, expires_at=expires_at)
        with self._lock:
            self._session = session
        return session

    def confirm(self, code: str, device_name: str, fcm_token: Optional[str] = None) -> tuple[str, str]:
        """Возвращает (device_id, access_token) при успехе, иначе
        бросает PairingError с понятным (русским) сообщением."""
        with self._lock:
            session = self._session
            if session is None:
                raise PairingError("Код не запрошен или уже был использован.")
            if datetime.now(timezone.utc) >= session.expires_at:
                self._session = None
                raise PairingError("Код истёк, запросите новый в Studio.")
            if session.attempts_left <= 0:
                self._session = None
                raise PairingError("Превышено число попыток, запросите новый код.")
            # compare_digest -- защита от тайминг-атаки, отдельная
            # переменная code_matches вместо прямого return изнутри
            # `with`, чтобы success-путь (создание устройства) выполнялся
            # уже ПОСЛЕ освобождения лока (create_device делает файловый
            # I/O, незачем держать его под этим же локом).
            code_matches = secrets.compare_digest(session.code, (code or "").strip())
            if not code_matches:
                session.attempts_left -= 1
                raise PairingError("Неверный код.")
            # Успех — код одноразовый, уничтожаем сразу (см. §1.3).
            self._session = None

        return create_device(device_name, fcm_token=fcm_token)

    def current_status(self) -> Optional[PairingSession]:
        with self._lock:
            return self._session


# Один экземпляр на процесс Remote -- достаточно, т.к. Remote сам по
# себе отдельный процесс (см. §0.1), состояние pairing не нужно шарить
# ни с чем ещё за пределами этого процесса.
pairing_manager = PairingManager()
