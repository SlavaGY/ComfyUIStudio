"""Общие части веб-приложений комплекта на FastAPI (этап 6.5 роадмапа аудита).

Imagine и Imagine Pony были написаны «по образцу» друг друга, и несколько
кусков в их бэкендах совпадали дословно. Здесь осталась одна копия:

* ``NoCacheMiddleware`` -- отключение кэша браузера для статики;
* ``static_dir()`` -- каталог статики приложения (и из исходников, и из
  собранного exe, где он лежит под ``sys._MEIPASS``);
* ``register_ui_prefs()`` -- ``GET/PUT /api/ui-prefs``: тема и язык общие с
  остальным комплектом (``shared_theme.py`` / ``shared_language.py``);
* ``mount_common_static()`` -- раздача ``web_common/static`` по ``/common``:
  там лежит общий JS-движок переводов ``js/i18n_engine.js``, который
  подключают страницы обоих приложений.

Ultimate RVC сюда сознательно не подключён: у него отдельное окружение и
копия нужных модулей Studio в ``rvc_app_src`` (см. tests/launcher/
test_rvc_app_isolation.py), а движок переводов проще и свой.

Модуль тянет fastapi/starlette, поэтому импортируют его только бэкенды веб-
приложений, а не код с Qt.
"""

import sys
from pathlib import Path

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.staticfiles import StaticFiles

try:
    # Необязательно: доступно только внутри ComfyUIStudio. Если приложение
    # запущено самостоятельно (venv без остального комплекта), /api/ui-prefs
    # отдаёт {"theme": null, "language": null}, а переключатели темы/языка в
    # интерфейсе остаются чисто локальными (localStorage).
    from comfyui_studio import shared_language, shared_theme
except ImportError:  # pragma: no cover - запущено отдельно от Studio
    shared_theme = None  # type: ignore[assignment]
    shared_language = None  # type: ignore[assignment]

#: URL-префикс общей статики; в index.html страниц он пишется ОТНОСИТЕЛЬНО
#: (``common/js/...`` без ведущего «/»), иначе за reverse-proxy Remote
#: (/api/v1/remote/apps/<id>/...) путь уйдёт мимо приложения.
COMMON_STATIC_URL = "/common"


class NoCacheMiddleware(BaseHTTPMiddleware):
    """Инструмент локальный, файлы меняются между запусками dev-сессии
    чаще, чем у любого обычного сайта -- кэш браузера здесь только
    вредит (см. как рассинхронизировались index.html и app.js после
    обновления: браузер получил 304 и продолжил использовать старый
    app.js). Отключаем кэш статики целиком, а не разбираемся с
    ETag/Last-Modified вручную."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        return response


def static_dir(package: str) -> Path:
    """Каталог ``static`` пакета ``comfyui_studio.<package>``.

    Из исходников -- рядом с пакетом; из собранного exe -- под
    ``sys._MEIPASS`` (туда его кладёт ComfyUIStudio.spec). Относительный
    путь вроде ``"static"`` использовать нельзя: он зависит от cwd, а Studio
    запускает приложения из корня проекта, а не из их папок."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "comfyui_studio" / package / "static"
    return Path(__file__).resolve().parent.parent / package / "static"


def mount_common_static(app: FastAPI) -> None:
    """Раздаёт общую статику по ``/common``. Вызывать ДО монтирования ``/``
    (корневой StaticFiles перехватил бы и этот путь)."""
    app.mount(COMMON_STATIC_URL, StaticFiles(directory=str(static_dir("web_common"))), name="common")


def register_ui_prefs(app: FastAPI) -> None:
    """``GET/PUT /api/ui-prefs`` -- тема и язык, общие для всего комплекта.

    Читает/пишет те же ``%APPDATA%\\ComfyUIStudio\\theme.json`` и
    ``language.json``, что и Qt-приложения (shared_theme.py /
    shared_language.py), поэтому выбор, сделанный в одном месте,
    подхватывается остальными. У веб-страницы нет файлового watcher'а, как у
    QFileSystemWatcher, -- вместо него страница опрашивает этот эндпоинт
    (см. js/i18n_engine.js)."""

    @app.get("/api/ui-prefs")
    def get_ui_prefs():
        theme = shared_theme.read_shared_theme() if shared_theme else None
        language = shared_language.read_shared_language() if shared_language else None
        return {"theme": theme, "language": language}

    @app.put("/api/ui-prefs")
    def put_ui_prefs(prefs: dict):
        theme = prefs.get("theme")
        language = prefs.get("language")
        if theme and shared_theme:
            shared_theme.write_shared_theme(theme)
        if language and shared_language:
            shared_language.write_shared_language(language)
        return {"ok": True}
