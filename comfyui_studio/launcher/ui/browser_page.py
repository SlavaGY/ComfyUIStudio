"""
Встроенный браузер (WebEngine) со страницей ComfyUI и панелью ресурсов.

Вынесено из comfyui_launcher.py (этап 1 дорожной карты).
"""

import os
import json

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView

from ..core.constants import WEBENGINE_PROFILE_DIR
from .widgets.resource_bar import ResourceBar


class RestrictedWebPage(QWebEnginePage):
    """
    - Переход по ссылке на другой хост/порт (документация, GitHub и т.п.)
      отменяется и открывается в системном браузере.
    - Любая попытка открыть "новое окно" (window.open, target=_blank,
      Ctrl+клик) не создаёт нового окна: если итоговый адрес — тот же
      ComfyUI, страница просто переходит на него в этом же окне; если
      сторонний домен — уходит в системный браузер.
    """

    def __init__(self, profile, allowed_host, allowed_port, parent=None):
        super().__init__(profile, parent)
        self._allowed_host = allowed_host
        self._allowed_port = allowed_port

    def _is_external(self, url: QUrl) -> bool:
        return not (
            url.host() == self._allowed_host and url.port(80) == self._allowed_port
        )

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if is_main_frame and self._is_external(url):
            QDesktopServices.openUrl(url)
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)

    def createWindow(self, _window_type):
        temp_page = QWebEnginePage(self.profile(), self)

        def handle(url):
            if self._is_external(url):
                QDesktopServices.openUrl(url)
            else:
                self.setUrl(url)
            temp_page.deleteLater()

        temp_page.urlChanged.connect(handle)
        return temp_page


# --------------------------------------------------------------------------
# Мониторинг ресурсов (CPU/RAM/GPU/температура/очередь ComfyUI)
# --------------------------------------------------------------------------



class BrowserPage(QWidget):
    # Раздельные сигналы: "Настройки" НЕ останавливает сервер,
    # "Остановить" — останавливает. Раньше обе кнопки делали одно и то же.
    settings_requested = Signal()
    stop_requested = Signal()

    def __init__(self, loc=None, parent=None):
        super().__init__(parent)
        self.loc = loc
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        top_bar = QWidget()
        top_bar.setFixedHeight(40)
        # Раньше цвет был захардкожен (тёмная "шторка браузера" поверх
        # любой темы) — теперь панель красится тем же QSS, что и весь
        # остальной интерфейс, и меняется вместе с темой оформления.
        top_row = QHBoxLayout(top_bar)
        top_row.setContentsMargins(10, 0, 10, 0)

        self.address_label = QLabel("")
        top_row.addWidget(self.address_label)

        self.resource_bar = ResourceBar(loc=self.loc)
        top_row.addWidget(self.resource_bar)

        top_row.addStretch(1)

        self.settings_btn = QPushButton(self._tr("\u2190 Настройки"))
        self.settings_btn.setToolTip(self._tr("Вернуться к настройкам, не останавливая ComfyUI"))
        self.settings_btn.setFlat(True)
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        top_row.addWidget(self.settings_btn)

        self.stop_btn = QPushButton(self._tr("\u23F9 Остановить"))
        self.stop_btn.setToolTip(self._tr("Остановить процесс ComfyUI"))
        self.stop_btn.setFlat(True)
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        top_row.addWidget(self.stop_btn)

        layout.addWidget(top_bar)

        self.view = QWebEngineView()
        self.view.setContextMenuPolicy(Qt.NoContextMenu)
        self.view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.view)

        self._profile = None
        self._page = None
        # True только когда встроенная страница ComfyUI реально
        # догрузилась — до этого runJavaScript() либо ничего не найдёт
        # (window.app ещё не создан фронтендом), либо выполнится в
        # контексте предыдущей/пустой страницы.
        self._page_ready = False

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def retranslate_ui(self):
        self.settings_btn.setText(self._tr("\u2190 Настройки"))
        self.settings_btn.setToolTip(self._tr("Вернуться к настройкам, не останавливая ComfyUI"))
        self.stop_btn.setText(self._tr("\u23F9 Остановить"))
        self.stop_btn.setToolTip(self._tr("Остановить процесс ComfyUI"))
        self.resource_bar.retranslate_ui()

    def load(self, port):
        if self._profile is None:
            os.makedirs(WEBENGINE_PROFILE_DIR, exist_ok=True)
            self._profile = QWebEngineProfile("comfyui_launcher", self.view)
            self._profile.setPersistentStoragePath(WEBENGINE_PROFILE_DIR)

        self._page = RestrictedWebPage(self._profile, "127.0.0.1", port, self.view)
        self._page_ready = False
        self._page.loadFinished.connect(self._on_load_finished)
        self.view.setPage(self._page)
        url = f"http://127.0.0.1:{port}/"
        self.address_label.setText(url)
        self.view.load(QUrl(url))

    def _on_load_finished(self, ok):
        self._page_ready = bool(ok)

    def apply_color_palette(self, palette_id):
        """Переключает встроенную палитру ComfyUI в УЖЕ открытой странице —
        через тот же JS-вызов, который выполняется, когда пользователь сам
        меняет тему в диалоге настроек ComfyUI. Это применяет палитру
        мгновенно и параллельно сохраняет её на бэкенде — перезапуск
        сервера не нужен, в отличие от правки comfy.settings.json на диске.

        Пробуем новый API фронтенда (app.extensionManager.setting.set),
        и, если его нет в этой сборке фронтенда, откатываемся на legacy
        (app.ui.settings.setSettingValue) — оба существуют для обратной
        совместимости в разных версиях ComfyUI_frontend.
        """
        if self._page is None or not self._page_ready:
            return

        js = f"""
        (function() {{
            try {{
                var value = {json.dumps(palette_id)};
                if (window.app && window.app.extensionManager
                        && window.app.extensionManager.setting) {{
                    window.app.extensionManager.setting.set('Comfy.ColorPalette', value);
                }} else if (window.app && window.app.ui && window.app.ui.settings) {{
                    window.app.ui.settings.setSettingValue('Comfy.ColorPalette', value);
                }}
            }} catch (e) {{
                console.error('ComfyUIStudio: не удалось применить палитру', e);
            }}
        }})();
        """
        self._page.runJavaScript(js)

    def update_stats(self, stats: dict):
        self.resource_bar.update_stats(stats)

    def unload(self):
        # Отвязываем страницу от вида перед уничтожением процесса,
        # чтобы не тянуть загрузку "мёртвого" сервера.
        self._page_ready = False
        if self._profile is not None:
            self.view.setPage(QWebEnginePage(self._profile, self.view))
        if self._page is not None:
            self._page.deleteLater()
            self._page = None


# --------------------------------------------------------------------------
# Трей
# --------------------------------------------------------------------------


