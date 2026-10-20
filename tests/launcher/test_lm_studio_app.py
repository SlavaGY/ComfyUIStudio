"""Two separate LM Studio launch modes and local UI subprocess lifecycle."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from comfyui_studio.launcher.core import lm_studio_app_process as core
from comfyui_studio.launcher.core.launch_mode import LM_STUDIO, LM_STUDIO_APP, is_lm_studio_mode
from comfyui_studio.launcher.ui.lm_studio_app_controller import LmStudioAppController


def test_original_config_value_is_preserved():
    assert LM_STUDIO == "lm_studio"
    assert is_lm_studio_mode(LM_STUDIO) and is_lm_studio_mode(LM_STUDIO_APP)
    assert LM_STUDIO_APP != LM_STUDIO


def test_process_launch_supports_sources_and_frozen_executable(monkeypatch):
    monkeypatch.setattr(core, "find_missing_modules", lambda modules: [])
    monkeypatch.setattr(core, "is_port_in_use", lambda port: False)
    process = core.LmStudioAppProcess({})
    monkeypatch.setattr(process, "_spawn_streamed", Mock())
    process.start()
    args = process._spawn_streamed.call_args.args[0]
    assert args[1:3] == ["-m", "comfyui_studio.lm_studio"]
    assert args[args.index("--port") + 1] == "7864"
    monkeypatch.setattr(core.sys, "frozen", True, raising=False)
    process.start()
    assert process._spawn_streamed.call_args.args[0][1] == "--studio-lm-studio"


@pytest.mark.parametrize("port", [0, 65536, "broken"])
def test_invalid_ui_port_is_rejected(port):
    from comfyui_studio.errors import ProcessStartError
    with pytest.raises(ProcessStartError):
        core.LmStudioAppProcess({"lm_studio": {"ui_port": port}})


def test_desktop_reuses_chat_started_by_remote(monkeypatch):
    monkeypatch.setattr(core, "find_missing_modules", lambda modules: [])
    monkeypatch.setattr(core, "is_port_in_use", lambda port: True)
    monkeypatch.setattr(core, "is_app_ready", lambda port: True)
    monkeypatch.setattr("comfyui_studio.remote.process_by_port.find_pid_listening_on_port", lambda port: 123)
    attached = Mock()
    attached.is_running.return_value = True
    monkeypatch.setattr(core.psutil, "Process", lambda pid: attached)
    process = core.LmStudioAppProcess({})
    spawn = Mock()
    monkeypatch.setattr(process, "_spawn_streamed", spawn)
    process.start()
    assert process.is_running()
    spawn.assert_not_called()
    response = Mock()
    response.__enter__ = Mock()
    response.__exit__ = Mock()
    monkeypatch.setattr(core.urllib.request, "urlopen", lambda *args, **kwargs: response)
    process.stop()
    attached.wait.assert_called_once_with(timeout=20)
    assert process._attached is None


def test_app_controller_only_opens_its_own_ready_endpoint(qtbot):
    process = Mock(port=7864)
    process.is_running.return_value = True
    process.stop.side_effect = lambda: setattr(process.is_running, "return_value", False)
    controller = LmStudioAppController(SimpleNamespace(cfg={}), process_factory=Mock(return_value=process))
    controller._task.run = Mock()  # Drive readiness deterministically without networking.
    opened = Mock()
    controller.ready.connect(opened)
    controller.start()
    assert controller.state == "starting"
    opened.assert_not_called()
    controller._probed(True, None)
    assert controller.state == "running"
    opened.assert_called_once_with(7864)
    controller.start()
    process.start.assert_called_once()
    controller.stop()
    process.stop.assert_called_once()
    assert controller.state == "stopped" and not controller._timer.isActive()


def test_failed_stop_can_reopen_surviving_server_and_detect_late_exit(qtbot):
    process = Mock(port=7864)
    process.is_running.return_value = True
    controller = LmStudioAppController(SimpleNamespace(cfg={}), process_factory=Mock(return_value=process))
    controller._task.run = Mock()
    opened = Mock()
    controller.ready.connect(opened)
    controller.start()
    controller._probed(True, None)
    assert not controller.stop()
    assert controller.state == "failed_active" and controller._timer.isActive()
    controller.start()
    controller._probed(True, None)
    assert controller.state == "running" and opened.call_count == 2
    process.start.assert_called_once()
    controller.stop()
    process.is_running.return_value = False
    controller._check()
    assert controller.state == "stopped" and controller._process is None
    assert not controller._timer.isActive()


def test_start_replaces_process_that_exited_between_checks(qtbot):
    old = Mock()
    old.is_running.return_value = False
    process = Mock(port=7864)
    process.is_running.return_value = True
    factory = Mock(return_value=process)
    controller = LmStudioAppController(SimpleNamespace(cfg={}), process_factory=factory)
    controller._task.run = Mock()
    controller._process, controller.state = old, "failed_active"
    controller.start()
    assert controller._process is process and controller.state == "starting"
    process.start.assert_called_once()
    process.is_running.return_value = False
    controller.stop()
