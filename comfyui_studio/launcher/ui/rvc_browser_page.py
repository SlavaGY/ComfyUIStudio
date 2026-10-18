"""
Встроенный браузер для веб-интерфейса Ultimate RVC (этап RVC-1).

Та же страница, что у ComfyUI (BrowserPage: верхняя панель «← Настройки» /
«⏹ Остановить» + QWebEngineView), но:

  * свой профиль WebEngine (имя и папка хранилища), чтобы не делить
    хранилище с ComfyUI -- см. атрибуты класса в BrowserPage;
  * подписи кнопок про Ultimate RVC, а не ComfyUI;
  * панель ресурсов скрыта: она показывает CPU/GPU/очередь ComfyUI, а
    монитор Studio привязан к порту ComfyUI;
  * СКАЧИВАНИЕ ФАЙЛОВ. Gradio отдаёт результат (готовый кавер, голос) как
    обычную загрузку, а QWebEngineView без обработчика downloadRequested
    такие загрузки молча отменяет -- кнопка «скачать» в интерфейсе RVC
    ничего бы не делала. Для ComfyUI это не нужно, поэтому в
    BrowserPage обработчика нет. Сами результаты RVC и так сохраняет в
    audio/output внутри своей папки -- скачивание нужно для удобства.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QStandardPaths
from PySide6.QtWebEngineCore import QWebEngineProfile
from PySide6.QtWidgets import QFileDialog

from ..core.constants import APP_DIR
from ..core.logging_setup import log
from .browser_page import BrowserPage


class RvcBrowserPage(BrowserPage):
    PROFILE_NAME = "ultimate_rvc"
    PROFILE_DIR = os.path.join(APP_DIR, "webengine_profile_rvc")
    SETTINGS_TOOLTIP = "Вернуться к настройкам, не останавливая Ultimate RVC"
    STOP_TOOLTIP = "Остановить процесс Ultimate RVC"

    def __init__(self, loc=None, parent=None):
        super().__init__(loc, parent)
        self.resource_bar.setVisible(False)

    def _on_profile_created(self, profile):
        # БЕЗ HTTP-кэша. Оба режима RVC (приложение Studio и оригинальный
        # Gradio) открываются по ОДНОМУ адресу -- 127.0.0.1:<порт RVC>, то есть
        # это один и тот же origin. С кэшем (по умолчанию он на диске, в
        # папке профиля) Chromium отдавал index.html/JS приложения Studio
        # из кэша, когда по этому адресу уже сидел Gradio, -- открывалось
        # «наше» приложение поверх чужого сервера, которое, естественно, не
        # находило свой API. Кэш чистим и отключаем: страницы локальные,
        # выигрыша от кэша нет.
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
        profile.downloadRequested.connect(self._on_download_requested)
        self._profile_ready = False
        profile.clearHttpCacheCompleted.connect(self._on_cache_cleared)
        profile.clearHttpCache()

    def _on_cache_cleared(self):
        self._profile_ready = True
        self._load_pending_page()

    def _on_download_requested(self, download):
        downloads = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation)
        suggested = os.path.join(downloads or "", download.downloadFileName())
        path, _filter = QFileDialog.getSaveFileName(
            self, self._tr("Сохранить файл"), suggested
        )
        if not path:
            download.cancel()
            return
        download.setDownloadDirectory(os.path.dirname(path))
        download.setDownloadFileName(os.path.basename(path))
        download.accept()
        log.info("Скачивание из Ultimate RVC: %s", path)
