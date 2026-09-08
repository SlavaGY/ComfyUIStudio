"""GET /api/v1/remote/status (§1.2 дорожной карты). Требует Bearer-
токен -- это первый эндпоинт, который реально дёргает уже сопряжённый
телефон (см. §1.5, критерий готовности), а не сам Studio UI."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...launcher.core.comfy_api import ComfyAPIClient
from ...launcher.core.imagine_process import is_imagine_available
from ..auth import require_device
from ..gpu_stats import get_gpu_stats
from ..models import RemoteStatus
from ..state import runtime

router = APIRouter()

# ComfyAPIClient -- чистый HTTP-клиент без Qt-зависимостей (см. его
# докстринг в launcher/core/comfy_api.py), поэтому свободно
# переиспользуется здесь напрямую, без дублирования логики опроса --
# ровно то, что требует принцип "не дублировать существующую логику"
# (§0 дорожной карты).
_comfy_client = ComfyAPIClient()


def _studio_version() -> str:
    from comfyui_studio import __version__
    return __version__


@router.get("/status", response_model=RemoteStatus)
def get_status(_device_id: str = Depends(require_device)) -> RemoteStatus:
    comfy_running = (
        _comfy_client.is_available(port=runtime.comfy_port)
        if runtime.comfy_port
        else False
    )
    imagine_running = (
        is_imagine_available(runtime.imagine_port) if runtime.imagine_port else False
    )
    gpu_name, vram_used_mb, vram_total_mb = get_gpu_stats()
    return RemoteStatus(
        studio_version=_studio_version(),
        comfyui_running=comfy_running,
        comfyui_port=runtime.comfy_port,
        imagine_running=imagine_running,
        gpu_name=gpu_name,
        vram_used_mb=vram_used_mb,
        vram_total_mb=vram_total_mb,
    )
