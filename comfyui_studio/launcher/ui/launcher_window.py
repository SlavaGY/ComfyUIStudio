"""
Главное окно лаунчера и точка входа при самостоятельном запуске.

Вынесено из comfyui_launcher.py (этап 1 дорожной карты).
"""

import os
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QStackedWidget, QSystemTrayIcon

from themes.theme_manager import ThemeManager
from i18n import LocalizationManager

from ..core.comfy_process import ComfyProcess, ProcessLogBridge
from ..core.config import build_extra_launch_args, load_config, prepare_launch_script
from ..core.constants import APP_NAME
from ..core.logging_setup import ICON_PATH, log
from ..core.system_monitor import ResourceMonitor
from ..integration.comfy_theme import COMFY_PALETTE_MAP, sync_comfyui_color_palette
from .browser_page import BrowserPage
from .settings_page import SettingsPage
from .tray import TrayIcon
from .widgets.launch_watcher import LaunchWatcher


class MainWindow(QMainWindow):
    def __init__(self, theme_manager: ThemeManager, loc=None):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1400, 900)
        if os.path.isfile(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))

        self.theme_manager = theme_manager
        self.loc = loc
        self.cfg = load_config()
        self.comfy_process = None
        self._quitting = False

        self.log_bridge = ProcessLogBridge()
        self.log_bridge.line_received.connect(self._on_process_log_line)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.settings_page = SettingsPage(self.cfg, theme_manager, loc)
        self.browser_page = BrowserPage(loc)

        self.stack.addWidget(self.settings_page)
        self.stack.addWidget(self.browser_page)

        self.settings_page.launch_requested.connect(self._on_launch)
        self.settings_page.open_running_requested.connect(self._show_browser_page)
        self.settings_page.stop_requested.connect(self._stop_and_show_settings)
        self.settings_page.cancel_requested.connect(self._on_launch_cancelled)

        self.launch_watcher = LaunchWatcher(loc=self.loc, parent=self)
        self.launch_watcher.ready.connect(self._on_server_ready)
        self.launch_watcher.failed.connect(self._on_server_failed)
        self.launch_watcher.progress.connect(self.settings_page.update_launch_progress)

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

    # -- лог процесса ComfyUI -----------------------------------------

    def _on_process_log_line(self, line):
        self.settings_page.log_panel.append_line(line)

    # -- мониторинг ------------------------------------------------------

    def _get_running_port(self):
        if self.comfy_process is not None and self.comfy_process.is_running():
            return self.cfg.get("port")
        return None

    def _on_stats_updated(self, stats):
        self.tray.update_stats(stats)
        self.browser_page.update_stats(stats)
        self.settings_page.resource_bar.update_stats(stats)

    # -- запуск/остановка ------------------------------------------------

    def _on_launch(self, cfg):
        self.cfg = cfg
        try:
            launch_script = prepare_launch_script(
                cfg["root_path"], cfg["script"], build_extra_launch_args(cfg)
            )
        except OSError as e:
            log.exception("Не удалось подготовить скрипт запуска")
            self.settings_page.set_status(
                self.settings_page._tr("Не удалось подготовить скрипт запуска: {}").format(e)
            )
            return

        if cfg.get("sync_comfy_theme"):
            sync_comfyui_color_palette(cfg["root_path"], self.theme_manager.current_theme())

        self.comfy_process = ComfyProcess(cfg["root_path"], launch_script, self.log_bridge)
        self.comfy_process.start()

        # Остаёмся на экране настроек — виден живой лог запуска, только
        # снизу появляется индикатор прогресса вместо отдельной страницы.
        self.settings_page.show_launch_progress(
            self.settings_page._tr("Запуск ComfyUI, ожидание сервера...")
        )
        self.launch_watcher.start(cfg["port"], self.comfy_process)

    def _on_server_ready(self):
        log.info("Сервер ComfyUI поднялся, открываю встроенный браузер")
        self.settings_page.hide_launch_progress()
        self.browser_page.load(self.cfg["port"])
        self.stack.setCurrentWidget(self.browser_page)

        # Подстраховка: применяем текущую тему сразу после того, как
        # страница реально догрузится (а не только то, что уже успели
        # записать в comfy.settings.json до старта сервера) — на случай,
        # если тема приложения поменялась между сохранением конфига и
        # фактическим стартом сервера.
        if self.cfg.get("sync_comfy_theme"):
            self.browser_page._page.loadFinished.connect(self._sync_comfy_theme_once)

    def _sync_comfy_theme_once(self, ok):
        if ok:
            self._on_app_theme_applied(self.theme_manager.current_theme())

    def _on_app_theme_applied(self, theme_name):
        """Живая, без перезапуска, синхронизация палитры ComfyUI при смене
        темы приложения — см. apply_color_palette() в BrowserPage."""
        if not self.cfg.get("sync_comfy_theme"):
            return
        if self.comfy_process is None or not self.comfy_process.is_running():
            return
        palette = COMFY_PALETTE_MAP.get(theme_name, "dark")
        self.browser_page.apply_color_palette(palette)

    def _on_server_failed(self, message):
        if self.comfy_process:
            self.comfy_process.stop()
        self.settings_page.hide_launch_progress()
        self.settings_page.set_status(message)
        self.settings_page.set_server_running(False)

    def _on_launch_cancelled(self):
        self.launch_watcher.stop()
        if self.comfy_process:
            self.comfy_process.stop()
        self.settings_page.hide_launch_progress()
        self.settings_page.set_status(self.settings_page._tr("Запуск отменён."))
        self.settings_page.set_server_running(False)

    def _show_browser_page(self):
        # Возврат к уже работающему ComfyUI без перезапуска.
        self.stack.setCurrentWidget(self.browser_page)

    def _show_settings_keep_running(self):
        # "Настройки" из окна браузера — сервер продолжает работать.
        running = self.comfy_process is not None and self.comfy_process.is_running()
        self.settings_page.set_server_running(running, self.cfg.get("port"))
        self.stack.setCurrentWidget(self.settings_page)

    def _stop_and_show_settings(self):
        self.browser_page.unload()
        if self.comfy_process:
            self.comfy_process.stop()
        self.settings_page.set_status("")
        self.settings_page.set_server_running(False)
        self.stack.setCurrentWidget(self.settings_page)

    # -- трей --------------------------------------------------------

    def _retranslate_secondary_ui(self, _code):
        """Перевыставляет тексты того, что вне SettingsPage (у неё
        своя обработка смены языка): страницу браузера и меню трея.
        Вызывается и при локальной смене языка (комбобокс в
        SettingsPage), и при внешней (см. shared_language.py)."""
        self.browser_page.retranslate_ui()
        self.tray.retranslate_ui()

    def _restore_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self):
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

        if self.comfy_process and self.comfy_process.is_running():
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
            self.comfy_process.stop()

        self.resource_monitor.stop()
        self.tray.hide()

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
        # Небольшая задержка перед фактическим quit() даёт QtWebEngine
        # время дообработать deleteLater() старой страницы и сбросить
        # её хранилище на диск, прежде чем процесс будет завершён.
        QTimer.singleShot(400, QApplication.instance().quit)




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

