"""
DTO-модели Remote API — §1.1 дорожной карты (ComfyUIStudio_Remote_Roadmap.md).

Поля и названия — 1:1 по дорожной карте, с минимальными уточнениями,
понадобившимися при реализации (см. комментарии у отдельных полей).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class RemoteStatus(BaseModel):
    studio_version: str
    comfyui_running: bool
    comfyui_port: Optional[int] = None
    imagine_running: bool
    gpu_name: Optional[str] = None
    vram_used_mb: Optional[int] = None
    vram_total_mb: Optional[int] = None


class PairingCodeResponse(BaseModel):
    code: str  # "482-731"
    expires_at: datetime
    attempts_left: int


class PairConfirmRequest(BaseModel):
    """Тело POST /pair/confirm — с телефона: {code, device_name} (§1.3).
    fcm_token — НОВОЕ (§Этап 6.5, "Push-уведомления"): опционален --
    Android передаёт его, если Firebase успел выдать токен к моменту
    pairing; если нет (или push вообще не настроен на телефоне) --
    None, устройство просто не получает push, WS-доставка (§2)
    продолжает работать как обычно."""
    code: str
    device_name: str = "Телефон"
    fcm_token: Optional[str] = None


class FcmTokenUpdateRequest(BaseModel):
    """Тело POST /fcm-token (§Этап 6.5) -- FCM-токены иногда переиздаются
    (см. докстринг Device.fcm_token в device_store.py), Android обязан
    прислать новый через этот эндпоинт, а не только один раз при pairing."""
    fcm_token: str


class DeviceToken(BaseModel):
    device_id: str
    access_token: str  # отдаётся ОДИН раз, при pairing


class DeviceInfo(BaseModel):
    device_id: str
    name: str
    created_at: datetime
    last_seen: Optional[datetime] = None
    revoked: bool


class AppEntry(BaseModel):
    """См. §4 дорожной карты — apps_registry.py появился на этапе 4;
    модель заведена уже сейчас (§1.1 перечисляет её в DTO этапа 1),
    GET /apps этапа 1 просто всегда отдаёт пустой список (см.
    routes/apps.py) — честно, пока реестра нет, вместо заглушки.

    `status` — НОВОЕ (этап "Запуск приложений через Remote", между
    этапами 6 и 6.5): "running" | "starting" | "stopped" (см.
    apps_registry.RemoteApp.status_fn / app_launcher.py). До этого поля
    GET /apps вообще не включал остановленные приложения (см. историю
    list_available_apps в apps_registry.py) — с телефона нельзя было
    увидеть Imagine, пока его не запустили вручную через Studio."""
    id: str  # "imagine"
    name: str  # "Imagine"
    path: str  # "/apps/imagine/" — префикс проксирования
    icon: Optional[str] = None
    status: str = "stopped"
    # True, если это приложение можно запустить через
    # POST /apps/{id}/start (см. routes/apps.py) — не все
    # зарегистрированные приложения обязаны это уметь (см.
    # RemoteApp.start_fn), терминал по этому полю решает, показывать ли
    # кнопку "Запустить" вообще, или просто статус без действия.
    startable: bool = False
    # НОВОЕ (автозапуск ComfyUI): текст последней ошибки АСИНХРОННОЙ
    # части запуска (см. RemoteApp.error_fn) -- напр. "ComfyUI не
    # поднялся за N секунд". None, пока ошибок не было или приложение
    # их не сообщает. Отдельно от 500-ответа POST /start -- та ошибка
    # синхронная (видна сразу), эта обнаруживается позже, уже после
    # того как /start успешно ответил 202.
    error: Optional[str] = None


class QueueStatusResponse(BaseModel):
    """Грубый статус очереди ComfyUI (см. §1.2 — GET /queue). Более
    подробная модель (running_ids/step_totals) сознательно не
    прокидывается на Remote API этапа 1 — телефону это пока не нужно
    (см. §2/§3 дорожной карты про realtime и пошаговый прогресс)."""
    running: int
    pending: int
