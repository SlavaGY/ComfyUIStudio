"""GET /api/v1/remote/ssh/config -- телефон дёргает это при каждом
подключении к Remote (см. TerminalActivity.kt), чтобы забрать/обновить
профиль SSH-туннеля "вне дома" тем же уже аутентифицированным каналом,
которым идёт весь остальной Remote API -- отдельного пейринга под это
заводить не пришлось (см. переписку про "тот же код, что и для
синхронизации"). Данные приходят с Bearer-токеном устройства (Depends
(require_device)), как и у status/apps -- в отличие от internal.py,
это часть публичного контракта телефона."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import require_device
from ..models import SshConfigResponse
from ..ssh_config_store import ssh_config_store

router = APIRouter()


@router.get("/ssh/config", response_model=SshConfigResponse)
def get_ssh_config(_device_id: str = Depends(require_device)) -> SshConfigResponse:
    config = ssh_config_store.current()
    # Пустой host -- значит PS1-скрипт не нашёл ни одного глобального
    # IPv6-адреса (см. routes/internal.py::post_ssh_config) -- без
    # хоста телефону всё равно нечего сохранять, поэтому это тоже
    # available=False, а не "available=True с host=None" (проще для
    # Kotlin-стороны: одна проверка available вместо двух).
    if config is None or not config.host:
        return SshConfigResponse(available=False)
    return SshConfigResponse(
        available=True,
        key_version=config.key_version,
        username=config.username,
        host=config.host,
        ssh_port=config.ssh_port,
        private_key=config.private_key,
    )
