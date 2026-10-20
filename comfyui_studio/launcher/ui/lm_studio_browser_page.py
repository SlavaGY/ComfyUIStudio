"""Studio chat UI in its own WebEngine profile, separate from original LM Studio."""

import os

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QPushButton

from ..core.constants import APP_DIR
from .browser_page import BrowserPage


class LmStudioBrowserPage(BrowserPage):
    PROFILE_NAME = "studio_lm_studio"
    PROFILE_DIR = os.path.join(APP_DIR, "webengine_profile_lm_studio")
    SETTINGS_TOOLTIP = "Вернуться к настройкам, не останавливая LM Studio"
    STOP_TOOLTIP = "Остановить приложение LM Studio"

    def __init__(self, loc=None, parent=None):
        super().__init__(loc, parent)
        self.resource_bar.hide()
        self._port = None
        self._retry_btn = QPushButton(self._tr("Повторить загрузку"), self)
        self._retry_btn.clicked.connect(self._retry)
        self.layout().addWidget(self._retry_btn)
        self._retry_btn.hide()
        self._load_timer = QTimer(self)
        self._load_timer.setSingleShot(True)
        self._load_timer.setInterval(30000)
        self._load_timer.timeout.connect(lambda: self._on_load_finished(False))
        self.view.renderProcessTerminated.connect(self._renderer_stopped)

    def needs_reload(self):
        return self._page is None or self._load_failed

    def load(self, port):
        self._port = port
        self._retry_btn.hide()
        self._load_timer.start()
        super().load(port)

    def _on_load_finished(self, ok):
        self._load_timer.stop()
        super()._on_load_finished(ok)
        self._retry_btn.setVisible(not ok and self._port is not None)

    def _renderer_stopped(self, _status, _exit_code):
        if self._port is not None:
            self._on_load_finished(False)

    def _retry(self):
        if self._port is not None:
            # A fresh QWebEnginePage starts a new renderer after a crash.
            self.load(self._port)

    def _update_loading_text(self):
        if self._load_failed:
            self._loading_label.setText(self._tr("Не удалось загрузить интерфейс LM Studio. Повторите загрузку страницы."))
        else:
            super()._update_loading_text()

    def retranslate_ui(self):
        super().retranslate_ui()
        self._retry_btn.setText(self._tr("Повторить загрузку"))

    def unload(self):
        self._port = None
        self._load_timer.stop()
        self._retry_btn.hide()
        super().unload()
