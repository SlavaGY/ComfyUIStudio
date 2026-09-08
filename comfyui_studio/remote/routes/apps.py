"""GET /api/v1/remote/apps (§1.2 дорожной карты) и
POST /api/v1/remote/apps/{app_id}/start -- НОВОЕ, этап "Запуск
приложений через Remote" (между этапами 6 и 6.5 дорожной карты).

Этап 1 отдавал честную заглушку -- пустой список, реестра ещё не было.
Этап 4 ("Реестр апп + reverse-proxy на сервере") ввёл сам реестр
(apps_registry.py), но GET /apps по-прежнему показывал ТОЛЬКО уже
запущенные приложения (`list_available_apps()`) -- с телефона нельзя
было даже увидеть Imagine, пока его не запускали вручную через Studio.
Теперь -- список ВСЕХ зарегистрированных приложений с полем `status`
("running"/"starting"/"stopped") на каждое, и отдельный эндпоинт для
самого запуска (см. app_launcher.py про то, как запуск переиспользует
уже существующий Qt-независимый механизм лаунчера)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..apps_registry import get_app, list_all_apps
from ..auth import require_device
from ..models import AppEntry

router = APIRouter()


@router.get("/apps", response_model=list[AppEntry])
def get_apps(_device_id: str = Depends(require_device)) -> list[AppEntry]:
    return [
        AppEntry(
            id=app.id,
            name=app.name,
            path=app.path,
            icon=app.icon,
            status=app.status_fn() if app.status_fn else "stopped",
            startable=app.start_fn is not None,
            error=app.error_fn() if app.error_fn else None,
        )
        for app in list_all_apps()
    ]


@router.post("/apps/{app_id}/start", status_code=202)
def start_app(app_id: str, _device_id: str = Depends(require_device)) -> dict:
    app = get_app(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="Неизвестное приложение.")
    if app.start_fn is None:
        # Зарегистрировано только для проксирования/отображения статуса
        # -- запускать с телефона пока нечем (см. RemoteApp.start_fn).
        raise HTTPException(status_code=400, detail="Это приложение нельзя запустить через Remote.")
    try:
        app.start_fn()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    # 202 Accepted, а не 200 -- запуск асинхронный (subprocess.Popen
    # возвращается сразу же, сам процесс поднимается ещё секунды-
    # десятки секунд, см. app_launcher.py); статус сразу после этого
    # вызова почти всегда будет "starting", не "running" -- терминал
    # обязан продолжить опрашивать GET /apps, а не считать один этот
    # ответ финальным.
    return {"status": app.status_fn() if app.status_fn else "starting"}
