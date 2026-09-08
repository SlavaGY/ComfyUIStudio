"""POST /api/v1/remote/pair/start и /pair/confirm (§1.2/§1.3 дорожной
карты)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..local_guard import require_loopback
from ..models import DeviceToken, PairConfirmRequest, PairingCodeResponse
from ..pairing import PairingError, pairing_manager

router = APIRouter()


@router.post(
    "/pair/start",
    response_model=PairingCodeResponse,
    dependencies=[Depends(require_loopback)],
)
def pair_start(request: Request) -> PairingCodeResponse:
    session = pairing_manager.start()
    return PairingCodeResponse(
        code=session.code,
        expires_at=session.expires_at,
        attempts_left=session.attempts_left,
    )


@router.post("/pair/confirm", response_model=DeviceToken)
def pair_confirm(payload: PairConfirmRequest) -> DeviceToken:
    # НЕТ require_loopback здесь -- этот вызов делает сам телефон (ещё
    # без токена, у него его пока нет, см. §1.3), а не Studio UI, поэтому
    # именно этот эндпоинт обязан работать из LAN, а не только с
    # 127.0.0.1. Защита от подбора кода -- TTL + ограниченное число
    # попыток внутри PairingManager, а не адрес клиента.
    try:
        device_id, access_token = pairing_manager.confirm(
            payload.code, payload.device_name, fcm_token=payload.fcm_token
        )
    except PairingError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return DeviceToken(device_id=device_id, access_token=access_token)
