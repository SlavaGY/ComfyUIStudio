"""
Проверка Bearer-токена для эндпоинтов, которые дёргает уже
сопряжённый телефон (не сам ПК) -- §1.3:

    Authorization: Bearer <access_token>
    -> auth.py находит device по hash(token), проверяет revoked=False,
       обновляет last_seen

401 в обоих случаях -- токен не найден И устройство отозвано (§1.5,
критерий готовности: "Отзыв устройства -> 401") -- специально не
различаем эти два случая в тексте ответа наружу, чтобы не давать
атакующему лишней информации о том, существует ли вообще такой токен.
"""

from __future__ import annotations

from fastapi import Header, HTTPException, status

from .device_store import find_device_by_token, touch_last_seen

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Требуется действительный Bearer-токен устройства.",
)


async def require_device(authorization: str | None = Header(default=None)) -> str:
    """FastAPI-зависимость -- возвращает device_id при успехе, иначе
    бросает 401. Используется как Depends(require_device) в эндпоинтах,
    доступных телефону (status/apps/queue -- см. routes/)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise _UNAUTHORIZED
    token = authorization[len("Bearer "):].strip()
    if not token:
        raise _UNAUTHORIZED
    device = find_device_by_token(token)
    if device is None or device.revoked:
        raise _UNAUTHORIZED
    touch_last_seen(device.device_id)
    return device.device_id


def resolve_device_from_token(token: str | None) -> str | None:
    """Тот же самый набор проверок, что и require_device() выше, но как
    обычная (не async, не бросающая исключение) функция -- используется
    в routes/ws.py (§2 дорожной карты), где токен приходит query-
    параметром (`?token=...`), а не Bearer-заголовком (WebSocket API
    браузеров/WebView не позволяет произвольно проставлять заголовки при
    установке соединения, см. §0.3 дорожной карты), и где отказ должен
    оформляться закрытием сокета, а не HTTP-исключением. Возвращает
    device_id либо None."""
    if not token:
        return None
    device = find_device_by_token(token)
    if device is None or device.revoked:
        return None
    touch_last_seen(device.device_id)
    return device.device_id
