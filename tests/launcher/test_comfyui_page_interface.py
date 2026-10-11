"""Страница настроек ComfyUI после переноса выбора режима на главный экран:
радиокнопок «Интерфейс» больше нет, порты Imagine/Pony доступны всегда, а
collect() не трогает cfg["interface"] (его пишет список на главном экране --
иначе автосохранение страницы затирало бы выбор устаревшим значением)."""

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


def test_collect_does_not_write_interface(make):
    assert "interface" not in make(interface="imagine").collect()


def test_radio_buttons_are_gone(make):
    page = make()
    for name in ("interface_comfyui_radio", "interface_imagine_radio", "interface_pony_radio"):
        assert not hasattr(page, name)


def test_ports_enabled_regardless_of_selected_mode(make):
    page = make(interface="comfyui", imagine_pony={"port": 7890})
    assert page.imagine_port_spin.isEnabled() and page.pony_port_spin.isEnabled()
    out = page.collect()
    assert out["imagine_pony"] == {"port": 7890}
    assert out["imagine"]["port"] == page.imagine_port_spin.value()


def test_ports_locked_while_comfyui_running(make):
    page = make()
    page.set_editable(False)
    assert not page.pony_port_spin.isEnabled() and not page.imagine_port_spin.isEnabled()
    assert not page.imagine_dev_mode_check.isEnabled()
    page.set_editable(True)
    assert page.pony_port_spin.isEnabled() and page.imagine_dev_mode_check.isEnabled()
