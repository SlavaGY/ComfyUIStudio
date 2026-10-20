"""Native LM Studio launch: paths, errors and independent home-screen routing."""

from unittest.mock import Mock

import pytest

from comfyui_studio.errors import ProcessStartError
from comfyui_studio.launcher.core import lm_studio as core


def test_discovery_and_explicit_path_precedence(tmp_path, monkeypatch):
    exe = tmp_path / "Programs" / "LM Studio" / "LM Studio.exe"
    exe.parent.mkdir(parents=True)
    exe.touch()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert core.lm_studio_executable({}) == str(exe)
    assert core.lm_studio_executable({"lm_studio": None}) == str(exe)
    assert core.lm_studio_executable({"lm_studio": {"executable_path": " missing.exe "}}) == "missing.exe"


def test_launch_preserves_spaces_and_unicode_without_shell(tmp_path, monkeypatch):
    exe = tmp_path / "Модели и чаты" / "LM Studio.exe"
    exe.parent.mkdir()
    exe.touch()
    popen = Mock(return_value=Mock(pid=123, wait=Mock(return_value=0)))
    monkeypatch.setattr(core.subprocess, "Popen", popen)
    core.launch_lm_studio(str(exe))
    assert popen.call_args.args == ([str(exe.resolve())],)
    assert popen.call_args.kwargs["cwd"] == str(exe.parent.resolve())
    assert not popen.call_args.kwargs.get("shell", False)


def test_launch_missing_executable_never_spawns(tmp_path, monkeypatch):
    popen = Mock()
    monkeypatch.setattr(core.subprocess, "Popen", popen)
    with pytest.raises(ProcessStartError):
        core.launch_lm_studio(str(tmp_path / "missing.exe"))
    popen.assert_not_called()


def test_launch_os_failure_becomes_service_error(tmp_path, monkeypatch):
    exe = tmp_path / "LM Studio.exe"
    exe.touch()
    monkeypatch.setattr(core.subprocess, "Popen", Mock(side_effect=PermissionError("denied")))
    with pytest.raises(ProcessStartError, match="denied"):
        core.launch_lm_studio(str(exe))


@pytest.fixture
def home(qtbot, monkeypatch, tmp_path):
    from comfyui_studio import shared_promptgen
    from comfyui_studio.launcher.core.config_store import ConfigStore
    from comfyui_studio.launcher.core.constants import DEFAULT_CONFIG
    from comfyui_studio.launcher.ui.settings_page import SettingsPage
    from comfyui_studio.themes.theme_manager import ThemeManager

    monkeypatch.setattr(shared_promptgen, "SHARED_DIR", str(tmp_path))
    monkeypatch.setattr(shared_promptgen, "SHARED_PROMPTGEN_PATH", str(tmp_path / "promptgen.json"))
    config = ConfigStore(initial=dict(DEFAULT_CONFIG), save=lambda cfg: None)
    page = SettingsPage(config, ThemeManager())
    qtbot.addWidget(page)
    qtbot.addWidget(page.settings_dialog)
    page.mode_combo.setCurrentIndex(page.mode_combo.findData("lm_studio"))
    return page


def test_home_flushes_new_path_and_launches_without_comfy(home, tmp_path):
    exe = tmp_path / "LM Studio.exe"
    exe.touch()
    home.settings_dialog.lm_studio_page.path_edit.setText(str(exe))
    launch = Mock()
    home.lm_studio_launch_requested.connect(launch)
    comfy = Mock()
    rvc = Mock()
    home.launch_requested.connect(comfy)
    home.rvc_launch_requested.connect(rvc)
    home._comfy_busy = home._rvc_busy = True
    home._refresh_launch_btn()
    assert home.launch_btn.isEnabled()
    home.launch_btn.click()
    launch.assert_called_once_with()
    assert home.config.cfg["lm_studio"]["executable_path"] == str(exe)
    comfy.assert_not_called()
    rvc.assert_not_called()


def test_home_controls_launch_and_stop_independently(home):
    stopped = Mock()
    opened = Mock()
    home.lm_studio_stop_requested.connect(stopped)
    home.lm_studio_open_requested.connect(opened)
    home.set_lm_studio_state("starting", "waiting")
    assert not home.launch_btn.isEnabled()
    assert not home.lm_studio_open_btn.isEnabled()
    assert not home.lm_studio_running_bar.isHidden()
    home.set_lm_studio_state("running", "running")
    home.lm_studio_open_btn.click()
    home.lm_studio_stop_btn.click()
    opened.assert_called_once_with()
    stopped.assert_called_once_with()
    home.set_lm_studio_state("stopped")
    assert home.launch_btn.isEnabled()
    assert home.lm_studio_running_bar.isHidden()
    home.set_lm_studio_state("failed_active", "stop failed")
    assert home.lm_studio_stop_btn.isEnabled()
    assert not home.launch_btn.isEnabled()


def test_settings_preserve_future_keys_and_retranslate(qtbot):
    from comfyui_studio.i18n import TRANSLATIONS
    from comfyui_studio.launcher.ui.settings.lm_studio_page import LmStudioSettingsPage

    loc = Mock()
    loc.tr.side_effect = lambda text: TRANSLATIONS["en"].get(text, text)
    page = LmStudioSettingsPage({"lm_studio": {"executable_path": "custom.exe", "future": 42}}, loc)
    qtbot.addWidget(page)
    section = page.collect()["lm_studio"]
    assert section["executable_path"] == "custom.exe" and section["future"] == 42
    assert section["api_port"] == 1234 and section["ui_port"] == 7864
    assert page.path_label.text() == "LM Studio executable:"
    assert "inside Studio" in page.intro_label.text()


@pytest.fixture
def controller(qtbot):
    from types import SimpleNamespace

    from comfyui_studio.launcher.ui.lm_studio_controller import LmStudioController

    process = Mock(hwnd=1234567890123)
    process.poll_window.return_value = None
    instance = LmStudioController(SimpleNamespace(cfg={}), process_factory=Mock(return_value=process))
    yield instance, process
    instance.stop()


def test_controller_waits_for_window_and_reuses_existing_instance(controller):
    instance, process = controller
    ready = Mock()
    instance.ready.connect(ready)
    instance.start()
    assert instance.state == "starting"
    ready.assert_not_called()
    process.poll_window.return_value = process.hwnd
    instance._check()
    assert instance.state == "running"
    ready.assert_called_once_with(process.hwnd)
    instance.start()
    process.start.assert_called_once_with()
    assert ready.call_count == 2


def test_controller_stop_releases_native_window_before_termination(controller):
    instance, process = controller
    calls = []
    instance.releasing.connect(lambda: calls.append("release"))
    process.stop.side_effect = lambda: calls.append("stop")
    instance.start()
    assert instance.stop()
    assert calls == ["release", "stop"]
    assert instance.state == "stopped"
    assert not instance._timer.isActive()


def test_controller_timeout_stops_process_and_reports_failure(controller):
    instance, process = controller
    instance.start()
    instance._deadline = 0
    instance._check()
    assert instance.state == "failed"
    process.stop.assert_called_once_with()
    assert not instance._timer.isActive()


def test_controller_window_close_cleans_up_process(controller):
    instance, process = controller
    process.poll_window.return_value = process.hwnd
    instance.start()
    process.poll_window.return_value = None
    instance._check()
    assert instance.state == "failed"
    process.stop.assert_called_once_with()


def test_controller_failed_stop_can_be_retried(controller):
    instance, process = controller
    instance.start()
    process.stop.side_effect = ProcessStartError("denied")
    assert not instance.stop()
    assert instance.state == "failed_active"
    assert instance._process is process
    process.stop.side_effect = None
    assert instance.stop()
    assert instance.state == "stopped"


def test_controller_launch_failure_reports_error(controller):
    instance, process = controller
    process.start.side_effect = ProcessStartError("missing")
    instance.start()
    assert instance.state == "failed"
    assert not instance._timer.isActive()


def test_process_adopts_existing_window_without_second_launch(tmp_path, monkeypatch):
    exe = tmp_path / "LM Studio.exe"
    exe.touch()
    monkeypatch.setattr(core, "find_app_window", Mock(return_value=(111, 222)))
    identity = Mock()
    monkeypatch.setattr(core.psutil, "Process", Mock(return_value=identity))
    launch = Mock()
    monkeypatch.setattr(core, "launch_lm_studio", launch)
    process = core.LmStudioProcess(str(exe))
    process.start()
    assert process.hwnd == 111
    assert process.identity is identity
    launch.assert_not_called()


def test_process_stop_uses_bound_identity_and_never_reused_pid(monkeypatch):
    terminate = Mock(return_value=True)
    monkeypatch.setattr(core, "terminate_process_tree", terminate)
    process = core.LmStudioProcess("test.exe")
    identity = Mock()
    process.identity = identity
    identity.is_running.return_value = False  # Includes psutil's PID reuse check.
    process.stop()
    terminate.assert_not_called()
    process.identity = identity
    identity.is_running.return_value = True
    process.stop()
    terminate.assert_called_once_with(identity, "LM Studio")


def test_renderer_header_is_clipped_and_content_keeps_full_height(qtbot):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QWidget

    from comfyui_studio.launcher.ui.lm_studio_page import LmStudioViewport

    viewport = LmStudioViewport()
    qtbot.addWidget(viewport)
    container = QWidget(viewport)
    viewport.set_container(container)
    viewport.resize(800, 600)
    viewport.show()
    container.show()
    qtbot.waitUntil(lambda: container.width() == 800)
    # Regression: hiding WS_CAPTION alone left LM Studio's blue renderer header
    # and its Close button visible. The entire header must be outside the host.
    assert container.y() == -30
    assert container.height() == 630
    assert not container.visibleRegion().contains(QPoint(10, 10))
    assert container.visibleRegion().contains(QPoint(10, 30))
    assert container.geometry().bottom() == viewport.rect().bottom()
    viewport.resize(1050, 720)
    qtbot.waitUntil(lambda: container.width() == 1050)
    assert container.y() == -30
    assert container.height() == 750
    viewport.set_container(None)


def test_foreign_window_uses_native_container_inside_clipping_viewport(qtbot, monkeypatch):
    from types import SimpleNamespace

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QWindow

    from comfyui_studio.launcher.ui import lm_studio_page as mod

    # Use a real Qt window without launching LM Studio. This exercises the
    # createWindowContainer path; a plain QWidget would miss Qt's native-parent
    # optimization that otherwise bypasses the viewport and exposes the header.
    window = QWindow()
    monkeypatch.setattr(mod, "QWindow", SimpleNamespace(fromWinId=lambda hwnd: window))
    monkeypatch.setattr(mod, "hide_window_frame", lambda hwnd: None)
    page = mod.LmStudioPage()
    qtbot.addWidget(page)
    page.resize(800, 600)
    page.show()
    page.load(123)
    assert page._container.parentWidget() is page._viewport
    assert page._container.testAttribute(Qt.WA_NativeWindow)
    assert page._container.y() == -30
    assert not page._container.isHidden()
    page.unload()
    assert page._viewport._container is None
