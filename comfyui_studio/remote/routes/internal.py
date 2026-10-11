"""POST /api/v1/remote/internal/generation-progress (этап 3 дорожной
карты, "Пошаговый прогресс").

Не часть публичного контракта Remote API для телефона -- это
внутренний канал приёма от ДРУГОГО Studio-процесса (Imagine, см.
comfyui_studio/imagine/backend/progress_forwarder.py), поэтому защищён
той же проверкой "только с 127.0.0.1", что и pairing/devices (см.
local_guard.py), а не Bearer-токеном устройства: вызывающая сторона —
не сопряжённый телефон, а сам Imagine на этом же ПК."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..local_guard import require_loopback
from ..models import SshConfigPush, SshConfigPushResult
from ..ssh_config_store import ssh_config_store
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


@router.post("/internal/ssh-config", response_model=SshConfigPushResult)
def post_ssh_config(payload: SshConfigPush) -> SshConfigPushResult:
    """Studio присылает результат Setup-SshAccess.ps1 (этап 3 аудита).
    Только с loopback (зависимость роутера). Приватный ключ нигде не
    логируется и не попадает в тексты ошибок."""
    if "PRIVATE KEY" not in payload.private_key:
        # Живой тест 2026-09-27: однажды вместо ключа пришёл дамп
        # PowerShell-объекта. Отклоняем с понятным текстом, не повторяя
        # присланное значение в ответе.
        raise HTTPException(
            status_code=422,
            detail="private_key не похож на приватный ключ (нет строки PRIVATE KEY).",
        )
    version = ssh_config_store.push(
        username=payload.username.strip(),
        ssh_port=payload.ssh_port,
        host=payload.host.strip(),
        private_key=payload.private_key,
    )
    return SshConfigPushResult(key_version=version)
