"""
Главное окно лаунчера и точка входа при самостоятельном запуске.

Вынесено из comfyui_launcher.py (этап 1 дорожной карты).
"""

import os
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QStackedWidget, QSystemTrayIcon

from comfyui_studio.themes.theme_manager import ThemeManager
from comfyui_studio.i18n import LocalizationManager

from ..core.comfy_process import ProcessLogBridge
from ..core.config_store import ConfigStore
from ..core.constants import APP_NAME, PROJECT_ROOT
from ..core.logging_setup import ICON_PATH, log, set_console_log_level
from ..core.system_monitor import ResourceMonitor
from ..integration.comfy_theme import COMFY_PALETTE_MAP
from .browser_page import BrowserPage
from .launch_controller import LaunchController
from .remote_controller import RemoteController
from .settings_page import SettingsPage
from .tray import TrayIcon


class MainWindow(QMainWindow):
    def __init__(self, theme_manager: ThemeManager, loc=None):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1400, 900)
        if os.path.isfile(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))

        self.theme_manager = theme_manager
        self.loc = loc
        # Единственный источник правды по конфигурации (R7): им же
        # пользуются страница настроек, диалог и контроллеры.
        self.config = ConfigStore()
        # применяем сохранённый уровень консольного логирования как можно
        # раньше -- см. ui/settings/advanced_page.py и
        # core/logging_setup.set_console_log_level (этап 4 дорожной карты)
        set_console_log_level(self.config.get("log_level", "INFO"))
        self._quitting = False
        # НОВОЕ: см. restart_studio()/closeEvent() ниже -- этап 4
        # дорожной карты, доработка по замечанию пользователя (кнопки
        # выхода/перезапуска ВСЕЙ Studio вместо тех же кнопок только для
        # PromptVault внутри его собственных настроек, см.
        # comfyui_studio/promptvault/ui/settings_window.py, параметр
        # standalone).
        self._pending_restart = False

        self.log_bridge = ProcessLogBridge()
        self.log_bridge.line_received.connect(self._on_process_log_line)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.settings_page = SettingsPage(self.config, theme_manager, loc)
        self.browser_page = BrowserPage(loc)

        self.stack.addWidget(self.settings_page)
        self.stack.addWidget(self.browser_page)

        self.settings_page.open_running_requested.connect(self._show_browser_page)
        self.settings_page.stop_requested.connect(self._stop_and_show_settings)
        self.settings_page.quit_studio_requested.connect(self.quit_studio)
        self.settings_page.restart_studio_requested.connect(self.restart_studio)

        # Remote (R4): процесс, опрос готовности и вызовы его локального
        # API живут в RemoteController (ui/remote_controller.py). Remote
        # независим от ComfyUI/Imagine (см. §0.1 дорожной карты Remote).
        self.remote = RemoteController(self.settings_page, parent=self, config=self.config)
        self.settings_page.remote_enable_toggled.connect(self.remote.on_enable_toggled)
        self.settings_page.remote_pairing_requested.connect(self.remote.request_pairing)
        self.settings_page.remote_refresh_devices_requested.connect(self.remote.refresh_devices)
        self.settings_page.remote_revoke_requested.connect(self.remote.revoke)

        # Цепочка запуска ComfyUI -> Imagine (R5): процессы, watcher'ы,
        # откат и отмена живут в LaunchController (ui/launch_controller.py).
        # За окном — только переключение страниц, браузер и тема.
        self.launch_controller = LaunchController(
            self.settings_page,
            self.log_bridge,
            self.theme_manager.current_theme,
            loc=self.loc,
            parent=self,
        )
        self.launch_controller.comfy_ready.connect(self._show_comfyui_browser)
        self.launch_controller.imagine_ready.connect(self._show_imagine_browser)
        self.settings_page.launch_requested.connect(self._on_launch)
        self.settings_page.cancel_requested.connect(self.launch_controller.cancel)

        self.browser_page.settings_requested.connect(self._show_settings_keep_running)
        self.browser_page.stop_requested.connect(self._stop_and_show_settings)

        self.theme_manager.theme_applied.connect(self._on_app_theme_applied)

        self.stack.setCurrentWidget(self.settings_page)

        tray_icon = QIcon(ICON_PATH) if os.path.isfile(ICON_PATH) else self.windowIcon()
        self.tray = TrayIcon(tray_icon, loc)
        self.tray.show_window_requested.connect(self._restore_from_tray)
        self.tray.stop_requested.connect(self._stop_and_show_settings)
        self.tray.quit_requested.connect(self._quit_from_tray)
        self.tray.show()

        if self.loc is not None:
            self.loc.language_changed_externally.connect(self._retranslate_secondary_ui)
        self.settings_page.language_changed.connect(self._retranslate_secondary_ui)

        # Трей должен существовать до первого срабатывания монитора —
        # start() сразу делает один опрос, а не ждёт первый тик таймера.
        self.resource_monitor = ResourceMonitor(self._get_running_port)
        self.resource_monitor.stats_updated.connect(self._on_stats_updated)
        self.log_bridge.progress_chunk_received.connect(
            self.resource_monitor.feed_log_line
        )
        self.resource_monitor.start()

        # НОВОЕ (Remote, этап 1): поднимаем Remote сразу при старте
        # лаунчера, если он был включён в прошлый раз -- НЕ дожидаясь
        # запуска ComfyUI (в отличие от Imagine, который стартует только
        # вслед за готовым ComfyUI-сервером, см. LaunchController.on_server_ready();
        # у Remote нет такой зависимости, см. §0.1 дорожной карты).
        if self.config.get("remote", {}).get("enabled"):
            self.remote.start()

    # -- лог процесса ComfyUI -----------------------------------------

    def _on_process_log_line(self, line):
        self.settings_page.log_panel.append_line(line)

    # -- мониторинг ------------------------------------------------------

    def _get_running_port(self):
        return self.launch_controller.running_port()

    def _on_stats_updated(self, stats):
        self.tray.update_stats(stats)
        self.browser_page.update_stats(stats)
        self.settings_page.resource_bar.update_stats(stats)

    # -- запуск/остановка ------------------------------------------------

    def _session_cfg(self):
        """Конфиг ТЕКУЩЕЙ сессии ComfyUI — снимок на момент нажатия
        «Запустить» (порт, на котором он реально поднят, флаг синхронизации
        темы). Изменения в настройках после запуска сюда не попадают, как и
        раньше; до первого запуска — актуальный конфиг из хранилища."""
        return self.launch_controller.cfg or self.config.cfg

    def _on_launch(self, cfg):
        self.launch_controller.launch(cfg)

    def _show_comfyui_browser(self):
        log.info("Открываю встроенный браузер на ComfyUI")
        self.settings_page.hide_launch_progress()
        self.browser_page.load(self._session_cfg()["port"])
        self.stack.setCurrentWidget(self.browser_page)

        # Подстраховка: применяем текущую тему сразу после того, как
        # страница реально догрузится (а не только то, что уже успели
        # записать в comfy.settings.json до старта сервера) — на случай,
        # если тема приложения поменялась между сохранением конфига и
        # фактическим стартом сервера.
        if self._session_cfg().get("sync_comfy_theme"):
            self.browser_page._page.loadFinished.connect(self._sync_comfy_theme_once)

    def _show_imagine_browser(self, port):
        self.browser_page.load(port)
        self.stack.setCurrentWidget(self.browser_page)

    def _sync_comfy_theme_once(self, ok):
        if ok:
            self._on_app_theme_applied(self.theme_manager.current_theme())

    def _on_app_theme_applied(self, theme_name):
        """Живая, без перезапуска, синхронизация палитры ComfyUI при смене
        темы приложения — см. apply_color_palette() в BrowserPage."""
        if not self._session_cfg().get("sync_comfy_theme"):
            return
        if not self.launch_controller.is_comfy_running():
            return
        palette = COMFY_PALETTE_MAP.get(theme_name, "dark")
        self.browser_page.apply_color_palette(palette)

    def _show_browser_page(self):
        # Возврат к уже работающему ComfyUI без перезапуска.
        self.stack.setCurrentWidget(self.browser_page)

    def _show_settings_keep_running(self):
        # "Настройки" из окна браузера — сервер продолжает работать.
        running = self.launch_controller.is_comfy_running()
        self.settings_page.set_server_running(running, self._session_cfg().get("port"))
        self.stack.setCurrentWidget(self.settings_page)

    def _stop_and_show_settings(self):
        self.browser_page.unload()
        self.launch_controller.stop()
        self.stack.setCurrentWidget(self.settings_page)

    # -- трей --------------------------------------------------------

    def _retranslate_secondary_ui(self, _code):
        """Перевыставляет тексты того, что вне SettingsPage.settings_dialog
        (у него своя обработка смены языка, см. AppSettingsDialog.
        _on_language_changed/_on_language_changed_externally): страницу
        браузера, меню трея, и сам домашний экран SettingsPage (лог,
        статус, кнопки запуска/остановки, "Другие инструменты").
        Вызывается и при локальной смене языка (комбобокс в General
        внутри AppSettingsDialog), и при внешней (см. shared_language.py
        — например, язык сменили из уже открытого окна PromptVault).

        До этой правки здесь ретранслировались только browser_page/tray
        -- SettingsPage.retranslate_ui() был определён, но не вызывался
        НИОТКУДА после переноса языка/темы в AppSettingsDialog (этап 4
        дорожной карты рефакторинга): раньше комбобокс языка жил прямо в
        SettingsPage и сам себя ретранслировал сразу же при переключении,
        и этот метод было незачем трогать -- после переноса он остался
        обрабатывать только "всё остальное", забыв про сам домашний
        экран. Из-за этого лог/статус/кнопки на домашнем экране оставались
        на прежнем языке до перезапуска приложения, хотя дерево настроек и
        окна остальных инструментов обновлялись сразу же."""
        self.browser_page.retranslate_ui()
        self.tray.retranslate_ui()
        self.settings_page.retranslate_ui()

    def _restore_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self):
        self._quitting = True
        self.close()

    def quit_studio(self):
        """Полностью закрывает ВСЮ ComfyUI Studio (лаунчер + окна
        остальных инструментов, если открыты) -- то же самое, что пункт
        «Выход» в трее (см. _quit_from_tray выше), просто вызывается из
        единого дерева настроек (Advanced -> Application, см.
        ui/settings/advanced_page.py). Подтверждение уже было запрошено
        в AdvancedSettingsPage — сюда попадаем, только если пользователь
        согласился."""
        log.info("Завершение работы ComfyUI Studio по запросу пользователя (Настройки)")
        self._quitting = True
        self.close()

    def restart_studio(self):
        """Перезапускает ВСЮ ComfyUI Studio целиком -- закрывает текущий
        процесс так же аккуратно, как обычный выход (closeEvent ниже:
        останавливает ComfyUI, если запущен, копит ResourceMonitor/трей/
        QtWebEngine), а затем заменяет процесс новым запуском. Подтверждение
        уже было запрошено в AdvancedSettingsPage.

        До этой доработки в едином дереве настроек была только кнопка
        Restart САМОГО PromptVault (см. comfyui_studio/promptvault/ui/
        settings_window.py) — в монолитной сборке она была не просто
        бесполезной, а опасной: PromptVault перезапускает себя через
        os.execv() безусловно, что в общем процессе означало бы убить
        ВЕСЬ процесс (лаунчер + управление ComfyUI) и поднять вместо
        него ТОЛЬКО PromptVault. См. MainWindow.__init__(standalone=...)
        и SettingsWindow(standalone=...) в PromptVault — кнопки
        Restart/Quit там теперь скрыты, когда PromptVault открыт в этом
        же процессе, а полноценные Studio-wide аналоги — вот эти два
        метода."""
        log.info("Перезапуск ComfyUI Studio по запросу пользователя (Настройки)")
        self._pending_restart = True
        self._quitting = True
        self.close()

    def closeEvent(self, event):
        if not self._quitting:
            # По умолчанию сворачиваем в трей, а не завершаем работу —
            # ComfyUI (если запущен) продолжает работать в фоне.
            event.ignore()
            self.hide()
            if self.tray.supportsMessages():
                self.tray.showMessage(
                    APP_NAME,
                    "Приложение свёрнуто в трей. ComfyUI продолжает работать, "
                    "если был запущен. Чтобы выйти полностью — пункт «Выход» в трее.",
                    QSystemTrayIcon.Information,
                    4000,
                )
            return

        if self.launch_controller.is_comfy_running():
            reply = QMessageBox.question(
                self,
                APP_NAME,
                "ComfyUI ещё запущен. Остановить процесс и выйти?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                self._quitting = False
                return
            self.launch_controller.stop_processes()

        self.resource_monitor.stop()
        self.tray.hide()
        # Remote независим от ComfyUI (см. §0.1 дорожной карты) -- всегда
        # останавливается при настоящем выходе, независимо от того, был
        # ли запущен ComfyUI (ветка выше это решение уже приняла).
        self.remote.stop()

        # Явно отвязываем страницу ComfyUI от вида перед выходом (как и
        # при возврате в настройки без остановки, см. unload() выше) --
        # иначе при резком завершении процесса QtWebEngine может не
        # успеть сбросить persistent-хранилище (IndexedDB/localStorage)
        # фронтенда ComfyUI на диск. Именно в этом сторадже фронтенд
        # держит список открытых вкладок/воркфлоу -- без сброса на диск
        # он "теряется", и после перезапуска лаунчера ComfyUI поднимается
        # с чистого листа, а сами воркфлоу приходится открывать заново.
        self.browser_page.unload()
        event.accept()

        # setQuitOnLastWindowClosed(False) держит цикл событий живым,
        # пока мы явно не попросим его завершиться — иначе процесс
        # остаётся висеть в диспетчере задач после закрытия окна.
        # Небольшая задержка перед фактическим quit()/перезапуском даёт
        # QtWebEngine время дообработать deleteLater() старой страницы и
        # сбросить её хранилище на диск, прежде чем процесс будет
        # завершён (или заменён новым, см. restart_studio() выше).
        if self._pending_restart:
            QTimer.singleShot(400, self._relaunch_process)
        else:
            QTimer.singleShot(400, QApplication.instance().quit)

    def _relaunch_process(self):
        """Замещает текущий процесс новым запуском -- см. restart_studio()
        выше. В собранном виде (PyInstaller, sys.frozen) перезапускает
        сам .exe; при запуске из исходников -- тем же интерпретатором
        Python корневой main.py (НЕ `-m`, в отличие от того, как
        перезапускается сам по себе PromptVault -- корневой main.py это
        обычный скрипт, а не член пакета, см. PROJECT_ROOT в
        core/constants.py)."""
        python = sys.executable
        if getattr(sys, "frozen", False):
            os.execv(python, [python] + sys.argv[1:])
        else:
            main_py = os.path.join(PROJECT_ROOT, "main.py")
            os.execv(python, [python, main_py] + sys.argv[1:])




def create_window(app: QApplication) -> "MainWindow":
    """Готовит тему/язык/иконку и возвращает главное окно лаунчера, не
    запуская цикл событий -- используется как при самостоятельном запуске
    (main() ниже), так и из монолитного ComfyUIStudio (см. корневой
    main.py), где QApplication уже создан заранее и общий на все три
    инструмента комплекта."""
    if os.path.isfile(ICON_PATH):
        app.setWindowIcon(QIcon(ICON_PATH))

    if not QSystemTrayIcon.isSystemTrayAvailable():
        log.warning("Системный трей недоступен — иконка трея не будет показана")

    theme_manager = ThemeManager()
    theme_manager.apply_theme(theme_manager.current_theme(), app)

    loc = LocalizationManager()
    loc.apply_language(loc.current_language())

    log.info("=== Запуск %s ===", APP_NAME)
    return MainWindow(theme_manager, loc)



def main():
    # См. подробный комментарий у аналогичного блока в main.py (корень
    # проекта) — тот же флаг нужен и здесь, если лаунчер запускается как
    # самостоятельный процесс (напрямую этим файлом), а не через
    # монолитный main.py.
    _EXTRA_CHROMIUM_FLAGS = "--disable-features=CalculateNativeWinOcclusion"
    _existing_flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    if _EXTRA_CHROMIUM_FLAGS not in _existing_flags:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
            f"{_existing_flags} {_EXTRA_CHROMIUM_FLAGS}".strip()
        )

    if hasattr(Qt, "AA_ShareOpenGLContexts"):
        QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)

    window = create_window(app)
    window.show()

    exit_code = app.exec()
    log.info("=== Выход, код %s ===", exit_code)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

