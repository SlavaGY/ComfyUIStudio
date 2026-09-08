"""GET /api/v1/remote/devices и POST /devices/{id}/revoke (§1.2 дорожной
карты) -- "админ-контекст", вызывается из Studio UI, поэтому оба
эндпоинта ограничены вызовом с самого ПК (см. local_guard.py), а не
Bearer-токеном устройства."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..device_store import list_devices, revoke_device
from ..local_guard import require_loopback
from ..models import DeviceInfo

router = APIRouter(dependencies=[Depends(require_loopback)])


@router.get("/devices", response_model=list[DeviceInfo])
def get_devices() -> list[DeviceInfo]:
    return [
        DeviceInfo(
            device_id=d.device_id,
            name=d.name,
            created_at=d.created_at,
            last_seen=d.last_seen,
            revoked=d.revoked,
        )
        for d in list_devices()
    ]


@router.post("/devices/{device_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
def post_revoke_device(device_id: str) -> None:
    if not revoke_device(device_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Устройство не найдено.")
