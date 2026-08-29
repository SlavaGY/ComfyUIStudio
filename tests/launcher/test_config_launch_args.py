"""
Тесты comfyui_studio.launcher.core.config.build_extra_launch_args.

Регрессионный тест по репорту пользователя: настройка "Порт" в
ComfyUISettingsPage раньше ВООБЩЕ не попадала в аргументы запуска
ComfyUI -- она влияла только на то, где студия ИЩЕТ уже запущенный
ComfyUI (get_running_port/HTTP-опрос/WS), но не на то, на каком порту
сам ComfyUI реально стартует (тот всегда поднимался на 8188 по
умолчанию). Если пользователь менял порт в настройках, студия начинала
опрашивать порт, на котором ComfyUI не поднимался, и ничего не
находила. Исправлено: build_extra_launch_args теперь всегда добавляет
--port {cfg["port"]}.

Чистый Python, без Qt -- config.py не импортирует PySide6.
"""

from comfyui_studio.launcher.core.config import build_extra_launch_args


def _base_cfg(**overrides):
    cfg = {"port": 8188, "launch_args": {}}
    cfg.update(overrides)
    return cfg


def test_port_always_included_as_launch_arg():
    args = build_extra_launch_args(_base_cfg(port=8188))
    assert "--port 8188" in args


def test_custom_port_is_passed_through():
    args = build_extra_launch_args(_base_cfg(port=8765))
    assert "--port 8765" in args
    assert "--port 8188" not in args


def test_port_comes_before_other_flags():
    cfg = _base_cfg(
        port=9000,
        disable_auto_launch=True,
        launch_args={"cpu": {"enabled": True}},
    )
    args = build_extra_launch_args(cfg)
    assert args[0] == "--port 9000"
    assert "--disable-auto-launch" in args
    assert "--cpu" in args


def test_missing_port_key_is_tolerated():
    # На случай старого/повреждённого config.json без ключа "port" --
    # cfg.get("port") вернёт None, и явный --port не добавляется, но
    # build_extra_launch_args не должен падать.
    cfg = {"launch_args": {}}
    args = build_extra_launch_args(cfg)
    assert not any(a.startswith("--port") for a in args)


def test_disable_auto_launch_and_checkbox_flags_still_work():
    cfg = _base_cfg(
        disable_auto_launch=True,
        launch_args={
            "cpu": {"enabled": True},
            "reserve_vram": {"enabled": True, "value": "2"},
            "lowvram": {"enabled": False},
        },
    )
    args = build_extra_launch_args(cfg)
    assert "--disable-auto-launch" in args
    assert "--cpu" in args
    assert "--reserve-vram 2" in args
    assert not any(a.startswith("--lowvram") for a in args)


def test_value_required_flag_skipped_when_empty():
    cfg = _base_cfg(
        launch_args={"reserve_vram": {"enabled": True, "value": ""}},
    )
    args = build_extra_launch_args(cfg)
    assert not any(a.startswith("--reserve-vram") for a in args)


def test_optional_value_flag_included_without_value():
    cfg = _base_cfg(
        launch_args={"listen": {"enabled": True, "value": ""}},
    )
    args = build_extra_launch_args(cfg)
    assert "--listen" in args
