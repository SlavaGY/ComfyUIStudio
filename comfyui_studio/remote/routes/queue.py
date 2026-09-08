"""GET /api/v1/remote/queue (§1.2 дорожной карты) -- грубый статус
очереди ComfyUI, тем же ComfyAPIClient, что и routes/system.py (см. его
докстринг про переиспользование без Qt-зависимостей)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...launcher.core.comfy_api import ComfyAPIClient
from ..auth import require_device
from ..models import QueueStatusResponse
from ..state import runtime

router = APIRouter()

_comfy_client = ComfyAPIClient()


@router.get("/queue", response_model=QueueStatusResponse)
def get_queue(_device_id: str = Depends(require_device)) -> QueueStatusResponse:
    state = (
        _comfy_client.get_queue(port=runtime.comfy_port) if runtime.comfy_port else None
    )
    if state is None:
        # ComfyUI не запущен (или порт неизвестен) -- честный "пустая
        # очередь", а не ошибка: с точки зрения телефона это просто
        # нечего показывать в очереди прямо сейчас (comfyui_running в
        # /status -- вот что говорит, работает ли вообще ComfyUI).
        return QueueStatusResponse(running=0, pending=0)
    return QueueStatusResponse(running=state.running, pending=state.pending)
