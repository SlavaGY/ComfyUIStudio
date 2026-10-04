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
    win.cfg = {"port": 8188, "interface": "comfyui", "sync_comfy_theme": False}
    win.settings_page = MagicMock()
    win.browser_page = MagicMock()
    win.stack = MagicMock()
    win.theme_manager = MagicMock()
    win.launch_controller = MagicMock()
    win.launch_controller.is_comfy_running.return_value = False
    return win


@pytest.fixture
def win(lw):
    return _make_window(lw)


# -- делегирование контроллеру -------------------------------------------


def test_on_launch_remembers_cfg_and_delegates(win):
    cfg = dict(win.cfg, port=9000)
    win._on_launch(cfg)
    assert win.cfg is cfg
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
    win.cfg["sync_comfy_theme"] = True
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

    win.cfg["sync_comfy_theme"] = True
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
