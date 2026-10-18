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


def test_home_flushes_new_path_and_launches_without_comfy(home, tmp_path, monkeypatch):
    from comfyui_studio.launcher.ui import settings_page

    exe = tmp_path / "LM Studio.exe"
    exe.touch()
    home.settings_dialog.lm_studio_page.path_edit.setText(str(exe))
    launch = Mock()
    monkeypatch.setattr(settings_page, "launch_lm_studio", launch)
    comfy = Mock()
    rvc = Mock()
    home.launch_requested.connect(comfy)
    home.rvc_launch_requested.connect(rvc)
    home._comfy_busy = home._rvc_busy = True
    home._refresh_launch_btn()
    assert home.launch_btn.isEnabled()
    home.launch_btn.click()
    launch.assert_called_once_with(str(exe))
    assert home.config.cfg["lm_studio"]["executable_path"] == str(exe)
    comfy.assert_not_called()
    rvc.assert_not_called()


def test_home_launch_error_stays_in_ui(home, tmp_path, monkeypatch):
    from comfyui_studio.launcher.ui import settings_page

    exe = tmp_path / "LM Studio.exe"
    exe.touch()
    home.settings_dialog.lm_studio_page.path_edit.setText(str(exe))
    monkeypatch.setattr(settings_page, "launch_lm_studio", Mock(side_effect=ProcessStartError("denied")))
    status = Mock()
    monkeypatch.setattr(home, "set_status", status)
    home._on_launch()
    assert "denied" in status.call_args.args[0]


def test_settings_preserve_future_keys_and_retranslate(qtbot):
    from comfyui_studio.i18n import TRANSLATIONS
    from comfyui_studio.launcher.ui.settings.lm_studio_page import LmStudioSettingsPage

    loc = Mock()
    loc.tr.side_effect = lambda text: TRANSLATIONS["en"].get(text, text)
    page = LmStudioSettingsPage({"lm_studio": {"executable_path": "custom.exe", "future": 42}}, loc)
    qtbot.addWidget(page)
    assert page.collect()["lm_studio"] == {"executable_path": "custom.exe", "future": 42}
    assert page.path_label.text() == "LM Studio executable:"
    assert "original interface" in page.intro_label.text()
