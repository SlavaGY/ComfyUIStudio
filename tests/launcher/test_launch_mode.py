"""Режим запуска на главном экране (core/launch_mode.py): список значений,
нормализация, соответствие значений прежним ключам конфига."""

from comfyui_studio.launcher.core import constants
from comfyui_studio.launcher.core.launch_mode import (
    COMFYUI,
    IMAGINE,
    IMAGINE_PONY,
    LM_STUDIO,
    LM_STUDIO_APP,
    MODES,
    RVC,
    RVC_GRADIO,
    is_comfy_family,
    is_rvc_mode,
    mode_label,
    normalize_mode,
    rvc_process_kind,
)


def test_modes_cover_the_tools_in_order_with_original_interfaces():
    assert [v for v, _ in MODES] == [COMFYUI, IMAGINE, IMAGINE_PONY, RVC, RVC_GRADIO, LM_STUDIO_APP, LM_STUDIO]


def test_old_config_values_are_still_valid():
    # Прежние config.json хранили именно эти значения -- миграция не нужна.
    assert (COMFYUI, IMAGINE, IMAGINE_PONY) == ("comfyui", "imagine", "imagine_pony")
    for old in ("comfyui", "imagine", "imagine_pony"):
        assert normalize_mode(old) == old


def test_default_config_interface_is_a_valid_mode():
    assert normalize_mode(constants.DEFAULT_CONFIG["interface"]) == constants.DEFAULT_CONFIG["interface"]


def test_normalize_falls_back_to_comfyui():
    for bad in (None, "", "gradio", "RVC", 5, ["rvc"]):
        assert normalize_mode(bad) == COMFYUI


def test_family_and_kind():
    assert is_comfy_family(IMAGINE) and not is_rvc_mode(IMAGINE)
    assert is_rvc_mode(RVC) and is_rvc_mode(RVC_GRADIO) and not is_comfy_family(RVC)
    assert rvc_process_kind(RVC) == "app" and rvc_process_kind(RVC_GRADIO) == "gradio"
    assert rvc_process_kind(COMFYUI) is None
    assert normalize_mode(LM_STUDIO) == LM_STUDIO
    assert not is_comfy_family(LM_STUDIO) and not is_rvc_mode(LM_STUDIO)


def test_mode_label():
    assert mode_label(IMAGINE_PONY) == "Imagine Pony"
    assert mode_label("weird") == "weird"
