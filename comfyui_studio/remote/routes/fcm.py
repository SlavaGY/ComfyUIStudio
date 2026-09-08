"""POST /api/v1/remote/fcm-token -- §Этап 6.5 дорожной карты
("Push-уведомления"), п.3: "обновление на сервере при его смене (FCM
токены иногда переиздаются)". Отдельный файл, а не routes/devices.py --
тот роутер целиком под require_loopback (§"админ-контекст", вызывается
из Studio UI), а этот эндпоинт, наоборот, вызывается САМИМ телефоном
по Bearer-токену, вне зависимости от Studio."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import require_device
from ..device_store import update_fcm_token
from ..models import FcmTokenUpdateRequest

router = APIRouter()


@router.post("/fcm-token", status_code=204)
def post_fcm_token(
    payload: FcmTokenUpdateRequest,
    device_id: str = Depends(require_device),
) -> None:
    # device_id -- из уже проверенного Bearer-токена (require_device),
    # не из тела запроса/URL -- устройство физически не может обновить
    # чужой токен (см. докстринг device_store.update_fcm_token).
    update_fcm_token(device_id, payload.fcm_token)
