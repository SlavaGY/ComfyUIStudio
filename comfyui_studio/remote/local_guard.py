"""
Часть эндпоинтов Remote API управляется не телефоном, а самим Studio UI
на том же ПК (§1.2: "POST /pair/start -- локальный вызов из Studio UI",
"GET /devices -- админ-контекст -- вызывается из Studio UI"). У этих
вызовов нет Bearer-токена (это не сопряжённое устройство, это сам
владелец сервера) -- вместо этого доступ ограничен адресом клиента:
только 127.0.0.1/::1 (сам ПК). Remote слушает на LAN-адресе (см. §0.1,
--host в __main__.py), так что без этой проверки любой человек в той же
локальной сети мог бы сам запросить себе pairing-код или посмотреть/
отозвать чужие устройства -- достаточно серьёзная дыра, чтобы не
оставлять её даже на "только LAN, ещё не VPN" этапе 1.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def require_loopback(request: Request) -> None:
    host = request.client.host if request.client else None
    if host not in _LOOPBACK_HOSTS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Этот эндпоинт управляется только из Studio UI на самом "
                "ПК, где запущен Remote (запрос пришёл не с 127.0.0.1)."
            ),
        )
