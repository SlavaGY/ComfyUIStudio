"""
Встроенный браузер (WebEngine) со страницей ComfyUI и панелью ресурсов.

Вынесено из comfyui_launcher.py (этап 1 дорожной карты).
"""

import os
import json

from PySide6.QtCore import Qt, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView

from ..core.constants import WEBENGINE_PROFILE_DIR
from ..core.logging_setup import log
from .widgets.models_nodes_dialog import ModelsNodesDialog
from .widgets.queue_history_dialog import QueueHistoryDialog
from .widgets.resource_bar import ResourceBar

# Этап 8 дорожной карты, ВТОРАЯ попытка разблокировать
# progress/executing/executed/execution_error (первая -- см. историю
# ниже -- сломала сам встроенный браузер и была отменена).
#
# ПЕРВАЯ ПОПЫТКА (отменена): читать реальный clientId встроенного
# фронтенда (sessionStorage/window.name, см. src/scripts/api.ts
# ComfyUI_frontend) и открывать ВТОРОЕ, отдельное WS-соединение
# (ComfyWebSocketClient) с ЭТИМ ЖЕ clientId, рассчитывая, что ComfyUI
# станет слать progress/executing/executed и туда тоже, раз id
# совпадает с тем, кто ставит задания в очередь.
#
# На практике (см. отчёт пользователя после реального прогона: ноды
# выполняются, но НИЧЕГО не отображается в самой странице ComfyUI,
# пока её не перезагрузить) это ломает саму страницу браузера. Причина
# -- в обработчике WS-подключений ComfyUI (server.py,
# websocket_handler): при подключении с clientId, который УЖЕ есть в
# реестре открытых сокетов (self.sockets), сервер СНАЧАЛА удаляет
# оттуда старую запись (`if sid: self.sockets.pop(sid, None)`), и
# только потом регистрирует новую под тем же id. Поскольку и
# встроенный браузер, и наш ComfyWebSocketClient подключались с ОДНИМ
# и тем же clientId, тот, кто подключился ВТОРЫМ (обычно — наш
# клиент, реконнект случается уже после того как страница открыта),
# вытеснял из этого реестра первого. Дальнейшие progress/executing и
# бинарные превью-кадры сервер шлёт по этому реестру (self.sockets),
# так что живая страница браузера просто переставала их получать --
# отсюда "ноды работают, но ничего не отображают", и помогает только
# полная перезагрузка страницы (её собственный WS переподключается и
# сам вытесняет наш клиент обратно).
#
# ВТОРАЯ ПОПЫТКА (эта, текущая): не открывать никакого второго
# соединения вообще. Вместо этого слушаем те же события ИЗНУТРИ уже
# открытой страницы, через QWebChannel: встроенный фронтенд и так уже
# получает progress/executing/executed по СВОЕЙ ЕДИНСТВЕННОЙ живой WS
# (через window.app.api, см. ComfyUI_frontend), и просто пересылает их
# нам JS-вызовом -- ни одного нового сетевого соединения к ComfyUI это
# не создаёт, вытеснять там нечего. См. ComfyEventBridge/
# _EVENT_BRIDGE_JS ниже.
#
# ИСПРАВЛЕНО после второго реального прогона: индикатор текущей ноды
# (последний пункт этапа 8) не появлялся вообще, хотя мост в целом
# работал (status/execution_cached/progress_state/executed доходили --
# см. лог реального прогона). Причина: window.app.api.addEventListener
# отдаёт в CustomEvent.detail НЕ всегда msg.data целиком, а версии-
# зависимо разворачивает его до конкретного вложенного поля -- это
# подтвердилось прямо в логе: у события "status" detail оказался
# {exec_info: ...}, а не {sid, status}, как в сыром сообщении сервера
# (сверено -- сырой формат {sid,status} мы сами видели раньше через
# ПРЯМОЙ WS-канал, см. comfy_ws.py). У "executing" по тому же
# принципу detail на практике оказывается просто СТРОКОЙ (id ноды) или
# null, а не объектом {node, display_node, prompt_id} -- наш Python-код
# (`payload.get("node")`) требовал именно dict и на строке молча
# возвращал None, то есть КАЖДОЕ событие "executing" трактовалось как
# "выполнение закончилось". Исправлено нормализацией в attach() ниже:
# если e.detail не объект, заворачиваем в {node: e.detail} перед
# отправкой в Python -- см. комментарий прямо в JS.
_EVENT_BRIDGE_JS = """
(function() {
    if (window.__comfyStudioBridgeInstalled) { return; }
    window.__comfyStudioBridgeInstalled = true;
    var EVENTS = [
        'status', 'progress', 'progress_state', 'executing', 'executed',
        'execution_start', 'execution_success', 'execution_error',
        'execution_cached', 'execution_interrupted'
    ];
    function attach(bridge) {
        if (!window.app || !window.app.api) {
            setTimeout(function() { attach(bridge); }, 300);
            return;
        }
        EVENTS.forEach(function(evt) {
            window.app.api.addEventListener(evt, function(e) {
                try {
                    // ВАЖНО: window.app.api разворачивает "detail" по-своему
                    // для разных типов событий, а не всегда отдаёт msg.data
                    // целиком -- подтверждено на практике: у "status" detail
                    // оказался {exec_info: ...} (вложенное подполе), а не
                    // {sid, status}, как в сыром сообщении сервера. Для
                    // "executing" detail на практике оказывается СТРОКОЙ
                    // (id ноды) или null -- НЕ объектом {node, display_node,
                    // prompt_id}, как можно было бы предположить по сырому
                    // протоколу. Нормализуем: если detail не объект --
                    // заворачиваем в {node: detail}, чтобы Python-сторона
                    // (feed_ws_event) всегда получала предсказуемую форму
                    // независимо от того, что конкретно отдаёт эта версия
                    // фронтенда.
                    var raw = e.detail;
                    var normalized = (raw && typeof raw === 'object') ? raw : { node: raw };
                    bridge.onEvent(evt, JSON.stringify(normalized));
                } catch (err) {
                    console.error('ComfyUIStudio: bridge onEvent failed', err);
                }
            });
        });
    }
    function boot() {
        if (typeof QWebChannel === 'undefined') {
            var s = document.createElement('script');
            s.src = 'qrc:///qtwebchannel/qwebchannel.js';
            s.onload = boot;
            document.head.appendChild(s);
            return;
        }
        if (typeof qt === 'undefined' || !qt.webChannelTransport) {
            setTimeout(boot, 300);
            return;
        }
        new QWebChannel(qt.webChannelTransport, function(channel) {
            attach(channel.objects.pyBridge);
        });
    }
    boot();
})();
"""


class ComfyEventBridge(QObject):
    """QObject, зарегистрированный в QWebChannel и доступный из JS
    страницы ComfyUI как `pyBridge` (см. _EVENT_BRIDGE_JS выше).
    Единственная задача -- перекинуть событие, которое фронтенд УЖЕ
    получил по своей собственной WS, обратно в Python одним вызовом,
    без создания какого-либо отдельного сетевого соединения."""

    event_received = Signal(str, str)  # msg_type, JSON-строка payload

    @Slot(str, str)
    def onEvent(self, msg_type, payload_json):
        self.event_received.emit(msg_type, payload_json)


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
    # Этап 8, вторая попытка (см. комментарий у _EVENT_BRIDGE_JS) --
    # progress/executing/executed/execution_error и т.п., пойманные
    # ComfyEventBridge ИЗНУТРИ уже открытой страницы. msg_type: str,
    # payload: dict (распарсенный JSON, {} при ошибке разбора).
    # Подключается в MainWindow к ResourceMonitor.feed_ws_event (см.
    # core/system_monitor.py).
    comfy_event_received = Signal(str, dict)

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

        self.queue_history_btn = QPushButton(self._tr("\U0001F4CB Очередь и история"))
        self.queue_history_btn.setToolTip(
            self._tr("Показать очередь заданий и историю генераций ComfyUI")
        )
        self.queue_history_btn.setFlat(True)
        self.queue_history_btn.clicked.connect(self._open_queue_history_dialog)
        top_row.addWidget(self.queue_history_btn)

        self.models_nodes_btn = QPushButton(self._tr("\U0001F5A5\uFE0F Модели и ноды"))
        self.models_nodes_btn.setToolTip(
            self._tr("Показать VRAM по устройствам и список зарегистрированных нод")
        )
        self.models_nodes_btn.setFlat(True)
        self.models_nodes_btn.clicked.connect(self._open_models_nodes_dialog)
        top_row.addWidget(self.models_nodes_btn)

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

        # -- баннер ошибки выполнения (этап 8, последний пункт) ---------
        # Скрыт по умолчанию, показывается на execution_error (см.
        # show_execution_error ниже), убирается вручную (крестик) или
        # автоматически при старте следующего прогона/его прерывании
        # (см. ResourceMonitor.feed_ws_event).
        self.error_banner = QWidget()
        self.error_banner.setVisible(False)
        error_row = QHBoxLayout(self.error_banner)
        error_row.setContentsMargins(10, 4, 10, 4)
        self.error_banner_label = QLabel("")
        self.error_banner_label.setWordWrap(True)
        error_row.addWidget(self.error_banner_label, 1)
        self.error_banner_dismiss_btn = QPushButton("\u2715")
        self.error_banner_dismiss_btn.setFlat(True)
        self.error_banner_dismiss_btn.setFixedWidth(28)
        self.error_banner_dismiss_btn.setToolTip(self._tr("Скрыть"))
        self.error_banner_dismiss_btn.clicked.connect(lambda: self.error_banner.setVisible(False))
        error_row.addWidget(self.error_banner_dismiss_btn)
        self.error_banner.setStyleSheet(
            "QWidget { background-color: #b23b3b; } QLabel { color: #ffffff; }"
            "QPushButton { color: #ffffff; font-weight: 600; border: none; }"
        )
        layout.addWidget(self.error_banner)

        self.view = QWebEngineView()
        self.view.setContextMenuPolicy(Qt.NoContextMenu)
        self.view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.view)

        self._profile = None
        self._page = None
        # Текущий порт, на котором открыт ComfyUI -- нужен только для
        # QueueHistoryDialog (этап 8, см. _open_queue_history_dialog
        # ниже): очередь/история читаются по обычному HTTP, отдельно от
        # QWebEngineView, поэтому диалогу нужен порт как обычному
        # HTTP-клиенту (ComfyAPIClient), а не сама страница браузера.
        self._current_port = None
        self._queue_history_dialog = None
        self._models_nodes_dialog = None
        self._last_error_payload = None
        # True только когда встроенная страница ComfyUI реально
        # догрузилась — до этого runJavaScript() либо ничего не найдёт
        # (window.app ещё не создан фронтендом), либо выполнится в
        # контексте предыдущей/пустой страницы.
        self._page_ready = False

        # -- JS-мост событий ComfyUI (этап 8, вторая попытка, см.
        # комментарий у _EVENT_BRIDGE_JS выше) -- один и тот же
        # ComfyEventBridge переживает несколько load()/unload() (сам
        # мост ни к чему конкретному не привязан), а вот QWebChannel
        # пересоздаётся под каждую страницу в load() ниже, т.к.
        # QWebChannel привязывается к конкретному QWebEnginePage. --
        self._event_bridge = ComfyEventBridge(self)
        self._event_bridge.event_received.connect(self._on_bridge_event)
        self._channel = None

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def retranslate_ui(self):
        self.queue_history_btn.setText(self._tr("\U0001F4CB Очередь и история"))
        self.queue_history_btn.setToolTip(
            self._tr("Показать очередь заданий и историю генераций ComfyUI")
        )
        self.models_nodes_btn.setText(self._tr("\U0001F5A5\uFE0F Модели и ноды"))
        self.models_nodes_btn.setToolTip(
            self._tr("Показать VRAM по устройствам и список зарегистрированных нод")
        )
        self.settings_btn.setText(self._tr("\u2190 Настройки"))
        self.settings_btn.setToolTip(self._tr("Вернуться к настройкам, не останавливая ComfyUI"))
        self.stop_btn.setText(self._tr("\u23F9 Остановить"))
        self.stop_btn.setToolTip(self._tr("Остановить процесс ComfyUI"))
        self.resource_bar.retranslate_ui()
        self.error_banner_dismiss_btn.setToolTip(self._tr("Скрыть"))
        if self._last_error_payload is not None:
            self.show_execution_error(self._last_error_payload)
        if self._queue_history_dialog is not None:
            self._queue_history_dialog.retranslate_ui()
        if self._models_nodes_dialog is not None:
            self._models_nodes_dialog.retranslate_ui()

    def _open_queue_history_dialog(self):
        if self._queue_history_dialog is None:
            self._queue_history_dialog = QueueHistoryDialog(
                lambda: self._current_port, loc=self.loc, parent=self
            )
        self._queue_history_dialog.open_and_raise()

    def _open_models_nodes_dialog(self):
        if self._models_nodes_dialog is None:
            self._models_nodes_dialog = ModelsNodesDialog(
                lambda: self._current_port, loc=self.loc, parent=self
            )
        self._models_nodes_dialog.open_and_raise()

    def load(self, port):
        self._current_port = port
        if self._profile is None:
            os.makedirs(WEBENGINE_PROFILE_DIR, exist_ok=True)
            self._profile = QWebEngineProfile("comfyui_launcher", self.view)
            self._profile.setPersistentStoragePath(WEBENGINE_PROFILE_DIR)

        self._page = RestrictedWebPage(self._profile, "127.0.0.1", port, self.view)
        self._page_ready = False
        self._channel = QWebChannel(self._page)
        self._channel.registerObject("pyBridge", self._event_bridge)
        self._page.setWebChannel(self._channel)
        self._page.loadFinished.connect(self._on_load_finished)
        self.view.setPage(self._page)
        url = f"http://127.0.0.1:{port}/"
        self.address_label.setText(url)
        self.view.load(QUrl(url))

    def _on_load_finished(self, ok):
        self._page_ready = bool(ok)
        if self._page_ready:
            self._page.runJavaScript(_EVENT_BRIDGE_JS)

    # -- JS-мост событий ComfyUI (этап 8, вторая попытка) ------------------

    def _on_bridge_event(self, msg_type, payload_json):
        try:
            payload = json.loads(payload_json) if payload_json else {}
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        self.comfy_event_received.emit(msg_type, payload)

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

    # -- индикатор текущей ноды / баннер ошибки (этап 8, последний пункт) --

    def set_executing_node(self, node_info):
        """Подключается в MainWindow к ResourceMonitor.node_execution_changed.
        Просто пробрасывает в ResourceBar -- индикатор живёт там же, где
        и остальные чипы (CPU/RAM/GPU/очередь)."""
        self.resource_bar.set_executing_node(node_info)

    def show_execution_error(self, payload):
        """Подключается в MainWindow к ResourceMonitor.execution_error_occurred.
        payload=None -- скрыть баннер (новый прогон стартовал успешно
        или предыдущий прогон был прерван пользователем, не сломался --
        см. feed_ws_event). payload=dict -- показать сводку ошибки;
        текст должен читаться без разворачивания -- полный traceback
        уже есть в логе лаунчера (см. log.warning в feed_ws_event), тут
        только самое важное: какая нода и что за исключение."""
        self._last_error_payload = payload
        if not payload:
            self.error_banner.setVisible(False)
            return
        node_type = payload.get("node_type") or "?"
        message = (
            payload.get("exception_message")
            or payload.get("exception_type")
            or self._tr("см. лог лаунчера для подробностей")
        )
        self.error_banner_label.setText(
            f"\u26A0 {self._tr('Ошибка выполнения')} ({node_type}): {message}"
        )
        self.error_banner.setVisible(True)

    def unload(self):
        # Отвязываем страницу от вида перед уничтожением процесса,
        # чтобы не тянуть загрузку "мёртвого" сервера.
        self._page_ready = False
        self._current_port = None
        self.show_execution_error(None)
        self.set_executing_node(None)
        if self._channel is not None:
            self._channel.deregisterObject(self._event_bridge)
            self._channel.deleteLater()
            self._channel = None
        if self._profile is not None:
            self.view.setPage(QWebEnginePage(self._profile, self.view))
        if self._page is not None:
            self._page.deleteLater()
            self._page = None


# --------------------------------------------------------------------------
# Трей
# --------------------------------------------------------------------------


