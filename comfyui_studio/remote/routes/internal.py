"""POST /api/v1/remote/internal/generation-progress (этап 3 дорожной
карты, "Пошаговый прогресс").

Не часть публичного контракта Remote API для телефона -- это
внутренний канал приёма от ДРУГОГО Studio-процесса (Imagine, см.
comfyui_studio/imagine/backend/progress_forwarder.py), поэтому защищён
той же проверкой "только с 127.0.0.1", что и pairing/devices (см.
local_guard.py), а не Bearer-токеном устройства: вызывающая сторона —
не сопряжённый телефон, а сам Imagine на этом же ПК."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..local_guard import require_loopback
from ..ws_hub import hub

router = APIRouter(dependencies=[Depends(require_loopback)])


class GenerationProgressPayload(BaseModel):
    prompt_id: str
    value: int
    max: int


@router.post("/internal/generation-progress", status_code=204)
async def post_generation_progress(payload: GenerationProgressPayload) -> None:
    await hub.broadcast(
        {
            "type": "generation.progress",
            "prompt_id": payload.prompt_id,
            "value": payload.value,
            "max": payload.max,
        }
    )
