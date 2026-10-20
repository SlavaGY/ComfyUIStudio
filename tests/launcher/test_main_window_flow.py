"""
Характеризационные тесты MainWindow (launcher/ui/launcher_window.py) --
этап R0, после R4/R5 сокращены до того, что осталось ЗА окном: делегирование
контроллерам, переключение страниц, встроенный браузер, тема.

Цепочка запуска ComfyUI -> Imagine (откат, отмена, watcher'ы) с R5 живёт в
LaunchController и покрыта tests/launcher/test_launch_controller.py (тесты
перенесены оттуда без изменения ожиданий). Блок Remote с R4 — в
tests/launcher/test_remote_controller.py.

Как устроено. Реальное окно (QMainWindow + SettingsPage + BrowserPage +
QtWebEngine) здесь не создаётся: методы MainWindow копируются в обычный
класс-каркас (_make_window), а все зависимости -- settings_page,
browser_page, stack, контроллеры -- это MagicMock. Так тесты не требуют
QApplication. Модуль launcher_window импортируется лениво, внутри фикстуры:
он тянет QtWebEngine, а launcher/__init__.py намеренно ленивый.

Что НЕ проверяется: сам вид страниц, трей, тема, WebEngine.
"""

import inspect
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def lw():
    pytest.importorskip("PySide6.QtWebEngineWidgets")
    from comfyui_studio.launcher.ui import launcher_window

    return launcher_window


def _make_window(lw):
    # __init__ не копируем: он создаёт настоящие страницы и процессы.
    methods = {
        k: v
        for k, v in vars(lw.MainWindow).items()
        if inspect.isfunction(v) and not k.startswith("__")
    }
    win = type("MainWindowHarness", (), methods)()
    win.settings_page = MagicMock()
    win.browser_page = MagicMock()
    win.stack = MagicMock()
    win.theme_manager = MagicMock()
    win.config = MagicMock()
    win.config.cfg = {"port": 1111, "sync_comfy_theme": True}  # «живой» конфиг — НЕ для сессии
    win.launch_controller = MagicMock()
    win.launch_controller.is_comfy_running.return_value = False
    # снимок конфига текущей сессии ComfyUI (R7: окно читает его отсюда)
    win.launch_controller.cfg = {"port": 8188, "interface": "comfyui", "sync_comfy_theme": False}
    return win


@pytest.fixture
def win(lw):
    return _make_window(lw)


# -- делегирование контроллеру -------------------------------------------


def test_on_launch_delegates_to_controller(win):
    cfg = {"port": 9000}
    win._on_launch(cfg)
    win.launch_controller.launch.assert_called_once_with(cfg)


def test_get_running_port_comes_from_controller(win):
    win.launch_controller.running_port.return_value = 8188
    assert win._get_running_port() == 8188
    win.launch_controller.running_port.return_value = None
    assert win._get_running_port() is None


def test_stop_and_show_settings_unloads_stops_then_switches_page(win):
    calls = []
    win.browser_page.unload.side_effect = lambda: calls.append("unload")
    win.launch_controller.stop.side_effect = lambda: calls.append("stop")
    win.stack.setCurrentWidget.side_effect = lambda w: calls.append("show")
    win._stop_and_show_settings()
    assert calls == ["unload", "stop", "show"]
    win.stack.setCurrentWidget.assert_called_with(win.settings_page)


def test_show_settings_keep_running_reports_actual_state(win):
    win.launch_controller.is_comfy_running.return_value = True
    win._show_settings_keep_running()
    win.settings_page.set_server_running.assert_called_with(True, 8188)
    win.stack.setCurrentWidget.assert_called_with(win.settings_page)

    win.launch_controller.is_comfy_running.return_value = False
    win._show_settings_keep_running()
    win.settings_page.set_server_running.assert_called_with(False, 8188)


# -- что делает окно, когда контроллер сообщил «готово» --------------------


def test_show_comfyui_browser_opens_embedded_browser(win):
    win._show_comfyui_browser()
    win.settings_page.hide_launch_progress.assert_called_once()
    win.browser_page.load.assert_called_once_with(8188)
    win.stack.setCurrentWidget.assert_called_once_with(win.browser_page)
    win.browser_page._page.loadFinished.connect.assert_not_called()  # тема не синхронизируется


def test_show_comfyui_browser_resyncs_theme_after_page_load_when_enabled(win):
    win.launch_controller.cfg["sync_comfy_theme"] = True
    win._show_comfyui_browser()
    win.browser_page._page.loadFinished.connect.assert_called_once_with(win._sync_comfy_theme_once)


def test_show_imagine_browser_loads_given_port(win):
    win._show_imagine_browser(7870)
    win.browser_page.load.assert_called_once_with(7870)
    win.stack.setCurrentWidget.assert_called_once_with(win.browser_page)


# -- тема ComfyUI ------------------------------------------------------------


def test_app_theme_applied_only_with_sync_enabled_and_comfy_running(win):
    win._on_app_theme_applied("dark")
    win.browser_page.apply_color_palette.assert_not_called()  # sync выключен

    win.launch_controller.cfg["sync_comfy_theme"] = True
    win._on_app_theme_applied("dark")
    win.browser_page.apply_color_palette.assert_not_called()  # ComfyUI не запущен

    win.launch_controller.is_comfy_running.return_value = True
    win._on_app_theme_applied("dark")
    win.browser_page.apply_color_palette.assert_called_once()


def test_sync_comfy_theme_once_only_when_page_loaded_ok(win):
    win._on_app_theme_applied = MagicMock()
    win._sync_comfy_theme_once(False)
    win._on_app_theme_applied.assert_not_called()
    win._sync_comfy_theme_once(True)
    win._on_app_theme_applied.assert_called_once_with(win.theme_manager.current_theme())


def test_lm_studio_ready_embeds_window_and_switches_page(win):
    win.lm_studio_page = MagicMock()
    win.showNormal = MagicMock()
    win.raise_ = MagicMock()
    win._show_lm_studio(1234567890123)
    win.lm_studio_page.load.assert_called_once_with(1234567890123)
    win.stack.setCurrentWidget.assert_called_once_with(win.lm_studio_page)


@pytest.mark.parametrize("needs_reload", [True, False])
def test_lm_studio_same_port_reopens_missing_or_crashed_page(win, needs_reload):
    win.lm_studio_browser_page = MagicMock()
    win.lm_studio_browser_page.needs_reload.return_value = needs_reload
    win._lm_studio_loaded_port = 7864
    win._show_lm_studio_app(7864)
    assert win.lm_studio_browser_page.load.call_count == int(needs_reload)
    win.stack.setCurrentWidget.assert_called_once_with(win.lm_studio_browser_page)


def test_lm_studio_failed_stop_allows_reopening_the_existing_web_server(win):
    win.lm_studio_browser_page = MagicMock()
    win.lm_studio_app = MagicMock(state="failed_active")
    win._on_lm_studio_app_state_changed("failed_active", "stop failed")
    win.settings_page.lm_studio_open_btn.setEnabled.assert_called_once_with(True)
    win._open_lm_studio()
    win.lm_studio_app.start.assert_called_once_with()


def test_lm_studio_stop_returns_to_settings_without_stopping_other_services(win):
    win.lm_studio_page = MagicMock()
    win.stack.currentWidget.return_value = win.lm_studio_page
    win._on_lm_studio_state_changed("stopped", "")
    win.stack.setCurrentWidget.assert_called_once_with(win.settings_page)
    win.launch_controller.stop.assert_not_called()


def test_failed_lm_studio_shutdown_keeps_studio_and_other_services_alive(win):
    win._quitting = True
    win.lm_studio = MagicMock()
    win.lm_studio.stop.return_value = False
    win.resource_monitor = MagicMock()
    win.tray = MagicMock()
    win.showNormal = MagicMock()
    event = MagicMock()
    win.closeEvent(event)
    event.ignore.assert_called_once()
    event.accept.assert_not_called()
    win.resource_monitor.stop.assert_not_called()
    win.tray.hide.assert_not_called()
    assert not win._quitting


def test_minimizing_to_tray_leaves_lm_studio_running(win):
    win._quitting = False
    win.lm_studio = MagicMock()
    win.tray = MagicMock()
    win.tray.supportsMessages.return_value = False
    win.hide = MagicMock()
    event = MagicMock()
    win.closeEvent(event)
    win.lm_studio.stop.assert_not_called()
    event.ignore.assert_called_once()
