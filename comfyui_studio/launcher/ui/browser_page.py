"""Встроенный WebEngine-браузер ComfyUI и панель ресурсов."""

import json
import os

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPalette
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

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
    # Имя/папка профиля WebEngine и подписи кнопок -- атрибуты класса, чтобы
    # другой сервис (Ultimate RVC, см. rvc_browser_page.py) мог показать
    # свой веб-интерфейс в такой же странице со СВОИМ профилем: два
    # QWebEngineProfile с одним именем и одной папкой хранилища мешали бы
    # друг другу. Каждый сервис использует собственный профиль.
    PROFILE_NAME = "comfyui_launcher"
    PROFILE_DIR = WEBENGINE_PROFILE_DIR
    SETTINGS_TOOLTIP = "Вернуться к настройкам, не останавливая ComfyUI"
    STOP_TOOLTIP = "Остановить процесс ComfyUI"

    # Раздельные сигналы: "Настройки" НЕ останавливает сервер,
    # "Остановить" — останавливает сервер.
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
        # Общий QSS задаёт цвет панели в соответствии с темой приложения.
        top_row = QHBoxLayout(top_bar)
        top_row.setContentsMargins(10, 0, 10, 0)

        self.address_label = QLabel("")
        top_row.addWidget(self.address_label)

        self.resource_bar = ResourceBar(loc=self.loc)
        top_row.addWidget(self.resource_bar)

        top_row.addStretch(1)

        self.settings_btn = QPushButton(self._tr("\u2190 Настройки"))
        self.settings_btn.setToolTip(self._tr(self.SETTINGS_TOOLTIP))
        self.settings_btn.setFlat(True)
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        top_row.addWidget(self.settings_btn)

        self.stop_btn = QPushButton(self._tr("\u23F9 Остановить"))
        self.stop_btn.setToolTip(self._tr(self.STOP_TOOLTIP))
        self.stop_btn.setFlat(True)
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        top_row.addWidget(self.stop_btn)

        layout.addWidget(top_bar)

        self.view = QWebEngineView()
        self.view.setContextMenuPolicy(Qt.NoContextMenu)
        self.view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._browser_stack = QStackedWidget()
        self._loading_label = QLabel(self._tr("Загрузка интерфейса..."))
        self._loading_label.setAlignment(Qt.AlignCenter)
        self._loading_label.setWordWrap(True)
        self._browser_stack.addWidget(self._loading_label)
        self._browser_stack.addWidget(self.view)
        layout.addWidget(self._browser_stack)

        self._profile = None
        self._page = None
        self._profile_ready = True
        self._pending_port = None
        self._load_failed = False
        # True только когда встроенная страница ComfyUI реально
        # догрузилась — до этого runJavaScript() либо ничего не найдёт
        # (window.app ещё не создан фронтендом), либо выполнится в
        # контексте предыдущей/пустой страницы.
        self._page_ready = False

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def retranslate_ui(self):
        self.settings_btn.setText(self._tr("\u2190 Настройки"))
        self.settings_btn.setToolTip(self._tr(self.SETTINGS_TOOLTIP))
        self.stop_btn.setText(self._tr("\u23F9 Остановить"))
        self.stop_btn.setToolTip(self._tr(self.STOP_TOOLTIP))
        self.resource_bar.retranslate_ui()
        self._update_loading_text()

    def load(self, port):
        self._pending_port = port
        self._page_ready = False
        self._load_failed = False
        self._update_loading_text()
        self._browser_stack.setCurrentWidget(self._loading_label)
        self.address_label.setText(f"http://127.0.0.1:{port}/")
        if self._profile is None:
            os.makedirs(self.PROFILE_DIR, exist_ok=True)
            self._profile = QWebEngineProfile(self.PROFILE_NAME, self.view)
            self._profile.setPersistentStoragePath(self.PROFILE_DIR)
            self._on_profile_created(self._profile)

        self._load_pending_page()

    def _load_pending_page(self):
        # Очистка профиля асинхронная: навигация до её завершения может
        # зависнуть. unload() отменяет запрос, даже если профиль ещё готовится.
        if not self._profile_ready or self._pending_port is None:
            return
        port = self._pending_port
        self._pending_port = None
        previous_page = self._page
        self._page = RestrictedWebPage(self._profile, "127.0.0.1", port, self.view)
        self._page.setBackgroundColor(self.palette().color(QPalette.Window))
        self._page.loadFinished.connect(
            lambda ok, page=self._page: self._on_page_load_finished(page, ok)
        )
        self.view.setPage(self._page)
        if previous_page is not None:
            previous_page.deleteLater()
        url = f"http://127.0.0.1:{port}/"
        self.address_label.setText(url)
        self.view.load(QUrl(url))

    def _on_profile_created(self, profile):
        """Хук для потомков: настроить свежесозданный профиль (например,
        подписаться на downloadRequested). Для ComfyUI -- ничего."""

    def _on_load_finished(self, ok):
        self._page_ready = bool(ok)
        self._load_failed = not ok
        self._update_loading_text()
        self._browser_stack.setCurrentWidget(self.view if ok else self._loading_label)

    def _on_page_load_finished(self, page, ok):
        if page is self._page:
            self._on_load_finished(ok)

    def _update_loading_text(self):
        text = (
            "Не удалось загрузить интерфейс. Остановите сервер и запустите его снова."
            if self._load_failed else "Загрузка интерфейса..."
        )
        self._loading_label.setText(self._tr(text))

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
        self._pending_port = None
        self._browser_stack.setCurrentWidget(self._loading_label)
        if self._profile is not None and self._profile_ready:
            self.view.setPage(QWebEnginePage(self._profile, self.view))
        if self._page is not None:
            self._page.deleteLater()
            self._page = None


# --------------------------------------------------------------------------
# Трей
# --------------------------------------------------------------------------


