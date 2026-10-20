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
