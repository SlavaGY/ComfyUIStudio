"""Переключатель «Интерфейс» на странице настроек ComfyUI: ComfyUI / Imagine / Imagine Pony."""

import pytest

from comfyui_studio.launcher.core import constants
from comfyui_studio.launcher.ui.settings.comfyui_page import ComfyUISettingsPage


def _cfg(**over):
    import copy
    cfg = copy.deepcopy(constants.DEFAULT_CONFIG)
    cfg.update(over)
    return cfg


@pytest.fixture
def make(qtbot):
    def _make(**over):
        page = ComfyUISettingsPage(_cfg(**over))
        qtbot.addWidget(page)
        return page
    return _make


def test_default_is_comfyui_with_all_extra_fields_disabled(make):
    page = make()
    assert page.collect()["interface"] == "comfyui"
    assert not page.imagine_port_spin.isEnabled()
    assert not page.pony_port_spin.isEnabled()


def test_pony_selected_from_config_enables_only_its_port(make):
    page = make(interface="imagine_pony", imagine_pony={"port": 7890})
    out = page.collect()
    assert out["interface"] == "imagine_pony"
    assert out["imagine_pony"] == {"port": 7890}
    assert page.pony_port_spin.isEnabled()
    assert not page.imagine_port_spin.isEnabled() and not page.imagine_dev_mode_check.isEnabled()


def test_radios_are_mutually_exclusive(make):
    page = make(interface="imagine")
    page.interface_pony_radio.setChecked(True)
    assert page.collect()["interface"] == "imagine_pony"
    assert not page.interface_imagine_radio.isChecked()
    assert page.pony_port_spin.isEnabled() and not page.imagine_port_spin.isEnabled()
    page.interface_imagine_radio.setChecked(True)
    assert page.collect()["interface"] == "imagine"
    assert not page.pony_port_spin.isEnabled()


def test_locked_while_running_disables_pony_port(make):
    page = make(interface="imagine_pony")
    page.set_editable(False)
    assert not page.pony_port_spin.isEnabled() and not page.interface_pony_radio.isEnabled()
    page.set_editable(True)
    assert page.pony_port_spin.isEnabled()
