"""
Тесты LaunchController (launcher/ui/launch_controller.py) — этап R5.

Цепочка запуска ComfyUI -> Imagine перенесена сюда из тестов MainWindow
(R0, test_main_window_flow.py) с теми же ожиданиями: создание/старт
процессов, откат при ошибке Imagine, отмена, остановка. Окно теперь
получает «готово» сигналами comfy_ready / imagine_ready(port) — их
проверяют отдельные тесты ниже. Добавлено: проводка watcher'ов к
контроллеру (сигналы Qt по-настоящему) и инварианты, которые раньше
держались на соглашении (stop_processes не трогает вид).
"""

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6.QtCore")

from PySide6.QtCore import QObject, Signal  # noqa: E402

from comfyui_studio.launcher.ui import launch_controller as lc  # noqa: E402


class FakeProcess:
    instances = []

    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs
        self.started = self.stopped = False
        self.running = True
        self.start_error = None
        type(self).instances.append(self)

    def start(self):
        if self.start_error is not None:
            raise self.start_error
        self.started = True

    def stop(self):
        self.stopped = True
        self.running = False

    def is_running(self):
        return self.running


def _fake_class(name):
    return type(name, (FakeProcess,), {"instances": []})


class FakeWatcher(QObject):
    """Настоящий QObject с теми же сигналами, что у LaunchWatcher."""

    ready = Signal()
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self):
        super().__init__()
        self.start = MagicMock()
        self.stop = MagicMock()


class Env:
    pass


@pytest.fixture
def env(monkeypatch):
    e = Env()
    e.comfy, e.imagine = _fake_class("FakeComfy"), _fake_class("FakeImagine")
    e.pony = _fake_class("FakeImaginePony")
    monkeypatch.setattr(lc, "ComfyProcess", e.comfy)
    monkeypatch.setattr(lc, "ImagineProcess", e.imagine)
    monkeypatch.setattr(lc, "ImaginePonyProcess", e.pony)
    monkeypatch.setattr(lc, "prepare_launch_script", lambda root, script, extra: "launch.bat")
    monkeypatch.setattr(lc, "build_extra_launch_args", lambda cfg: [])
    e.sync = MagicMock()
    monkeypatch.setattr(lc, "sync_comfyui_color_palette", e.sync)

    e.view = MagicMock()
    e.view._tr.side_effect = lambda text: text
    e.bridge = MagicMock()
    e.cw, e.iw = FakeWatcher(), FakeWatcher()
    e.theme = MagicMock(return_value="dark")
    e.ctl = lc.LaunchController(
        e.view, e.bridge, e.theme, launch_watcher=e.cw, imagine_launch_watcher=e.iw
    )
    e.cfg = {
        "root_path": "C:/comfy", "script": "run.bat", "port": 8188,
        "interface": "comfyui", "sync_comfy_theme": False,
        "env_vars": {"A": "1"},
        "imagine": {"port": 7870, "dev_mode": True},
        "remote": {"enabled": False, "port": 7861, "host": "127.0.0.1"},
    }
    e.ctl.cfg = e.cfg
    e.signals = []
    e.ctl.comfy_ready.connect(lambda: e.signals.append(("comfy_ready",)))
    e.ctl.imagine_ready.connect(lambda p: e.signals.append(("imagine_ready", p)))
    return e


# -- запуск ComfyUI ------------------------------------------------------------


def test_launch_creates_starts_comfy_and_arms_watcher(env):
    env.ctl.launch(env.cfg)
    proc = env.comfy.instances[0]
    assert env.ctl.comfy_process is proc and proc.started is True
    assert proc.args == ("C:/comfy", "launch.bat", env.bridge)
    assert proc.kwargs == {"env_overrides": {"A": "1"}}
    env.view.show_launch_progress.assert_called_once()
    env.cw.start.assert_called_once_with(8188, proc)


def test_launch_remembers_passed_cfg(env):
    new_cfg = dict(env.cfg, port=9000)
    env.ctl.launch(new_cfg)
    assert env.ctl.cfg is new_cfg
    assert env.cw.start.call_args.args[0] == 9000


def test_launch_script_error_sets_status_and_does_not_start(env, monkeypatch):
    def boom(root, script, extra):
        raise OSError("нет доступа")

    monkeypatch.setattr(lc, "prepare_launch_script", boom)
    env.ctl.launch(env.cfg)
    assert env.ctl.comfy_process is None and env.comfy.instances == []
    env.cw.start.assert_not_called()
    env.sync.assert_not_called()
    status = env.view.set_status.call_args.args[0]
    assert "Не удалось подготовить скрипт запуска" in status and "нет доступа" in status


def test_launch_syncs_comfy_palette_only_when_enabled(env):
    env.ctl.launch(env.cfg)
    env.sync.assert_not_called()
    env.ctl.launch(dict(env.cfg, sync_comfy_theme=True))
    env.sync.assert_called_once_with("C:/comfy", "dark")


# -- ComfyUI поднялся -----------------------------------------------------------


def test_server_ready_comfyui_interface_signals_window_to_open_browser(env):
    env.ctl.on_server_ready()
    assert env.signals == [("comfy_ready",)]
    assert env.imagine.instances == []


def test_server_ready_imagine_interface_starts_imagine_instead(env):
    env.cfg["interface"] = "imagine"
    env.ctl.on_server_ready()
    proc = env.imagine.instances[0]
    assert env.ctl.imagine_process is proc and proc.started is True
    assert proc.kwargs == {
        "host": "127.0.0.1", "port": 7870, "comfy_host": "127.0.0.1",
        "comfy_port": 8188, "dev_mode": True, "remote_port": 7861,
    }
    env.iw.start.assert_called_once_with(7870, proc)
    assert env.signals == []  # браузер откроется после Imagine


def test_imagine_defaults_port_7860_and_no_dev_mode(env):
    env.cfg.update(interface="imagine", imagine={})
    env.cfg.pop("remote")
    env.ctl.on_server_ready()
    kw = env.imagine.instances[0].kwargs
    assert kw["port"] == 7860 and kw["dev_mode"] is False and kw["remote_port"] is None


def test_imagine_start_error_rolls_back_both_processes(env):
    env.cfg["interface"] = "imagine"
    env.ctl.comfy_process = env.comfy()
    original_init = env.imagine.__init__

    def init_failing(self, *a, **k):
        original_init(self, *a, **k)
        self.start_error = RuntimeError("порт занят")

    env.imagine.__init__ = init_failing
    env.ctl.on_server_ready()

    assert env.imagine.instances[0].stopped is True
    assert env.ctl.comfy_process.stopped is True  # откат ПОЛНЫЙ: Comfy тоже гасится
    status = env.view.set_status.call_args.args[0]
    assert "Не удалось запустить Imagine" in status and "порт занят" in status
    env.view.set_server_running.assert_called_with(False)
    env.iw.start.assert_not_called()
    assert env.signals == []


def test_imagine_ready_hides_progress_then_signals_port(env):
    order = []
    env.view.hide_launch_progress.side_effect = lambda: order.append("hide")
    env.ctl.imagine_ready.connect(lambda p: order.append(f"signal:{p}"))
    env.ctl.on_imagine_ready()
    assert order == ["hide", "signal:7870"]


def test_imagine_ready_defaults_to_7860_without_imagine_section(env):
    env.cfg.pop("imagine")
    env.ctl.on_imagine_ready()  # раньше здесь был KeyError
    assert env.signals == [("imagine_ready", 7860)]


def test_imagine_failed_stops_both_and_shows_message(env):
    env.ctl.comfy_process, env.ctl.imagine_process = env.comfy(), env.imagine()
    env.ctl.on_imagine_failed("Imagine не отвечает")
    assert env.ctl.imagine_process.stopped and env.ctl.comfy_process.stopped
    env.view.set_status.assert_called_with("Imagine не отвечает")
    env.view.set_server_running.assert_called_with(False)
    env.view.hide_launch_progress.assert_called_once()


# -- сбои, отмена, остановка ------------------------------------------------------


def test_server_failed_stops_comfy_and_reports(env):
    env.ctl.comfy_process = env.comfy()
    env.ctl.on_server_failed("ComfyUI не поднялся")
    assert env.ctl.comfy_process.stopped is True
    env.view.hide_launch_progress.assert_called_once()
    env.view.set_status.assert_called_with("ComfyUI не поднялся")
    env.view.set_server_running.assert_called_with(False)


def test_server_failed_without_process_does_not_crash(env):
    env.ctl.on_server_failed("x")
    env.view.set_status.assert_called_with("x")


def test_server_failed_does_not_touch_imagine_or_reset_objects(env):
    comfy, imagine = env.comfy(), env.imagine()
    env.ctl.comfy_process, env.ctl.imagine_process = comfy, imagine
    env.ctl.on_server_failed("x")
    assert env.ctl.comfy_process is comfy and env.ctl.imagine_process is imagine
    assert not imagine.stopped


def test_cancel_stops_watchers_and_processes(env):
    comfy, imagine = env.comfy(), env.imagine()
    env.ctl.comfy_process, env.ctl.imagine_process = comfy, imagine
    env.ctl.cancel()
    env.cw.stop.assert_called_once()
    env.iw.stop.assert_called_once()
    assert comfy.stopped and imagine.stopped
    assert env.ctl.imagine_process is None  # Imagine обнуляется, Comfy-объект остаётся
    assert env.ctl.comfy_process is comfy
    env.view.set_status.assert_called_with("Запуск отменён.")
    env.view.set_server_running.assert_called_with(False)


def test_stop_stops_processes_and_resets_status(env):
    comfy, imagine = env.comfy(), env.imagine()
    env.ctl.comfy_process, env.ctl.imagine_process = comfy, imagine
    env.ctl.stop()
    assert comfy.stopped and imagine.stopped and env.ctl.imagine_process is None
    env.view.set_status.assert_called_with("")
    env.view.set_server_running.assert_called_with(False)


def test_stop_processes_never_touches_the_view(env):
    """Используется при выходе из приложения: страницы уже не нужны."""
    env.ctl.comfy_process, env.ctl.imagine_process = env.comfy(), env.imagine()
    env.ctl.stop_processes()
    assert env.view.mock_calls == []
    env.cw.stop.assert_not_called()


def test_stop_without_any_process_is_safe(env):
    env.ctl.stop()
    env.ctl.stop_processes()


# -- состояние ---------------------------------------------------------------------


def test_running_port_only_while_comfy_is_alive(env):
    assert env.ctl.running_port() is None
    env.ctl.comfy_process = env.comfy()
    assert env.ctl.running_port() == 8188
    env.ctl.comfy_process.running = False
    assert env.ctl.running_port() is None
    assert env.ctl.is_comfy_running() is False


# -- проводка watcher'ов (настоящие сигналы Qt) ------------------------------------------


def test_comfy_watcher_ready_leads_to_comfy_ready_signal(env):
    env.cw.ready.emit()
    assert env.signals == [("comfy_ready",)]


def test_comfy_watcher_ready_with_imagine_interface_starts_imagine(env):
    env.cfg["interface"] = "imagine"
    env.cw.ready.emit()
    assert len(env.imagine.instances) == 1
    assert env.signals == []


def test_comfy_watcher_failed_is_reported(env):
    env.ctl.comfy_process = env.comfy()
    env.cw.failed.emit("ComfyUI не поднялся")
    env.view.set_status.assert_called_with("ComfyUI не поднялся")
    assert env.ctl.comfy_process.stopped


def test_imagine_watcher_ready_leads_to_imagine_ready_signal(env):
    env.iw.ready.emit()
    assert env.signals == [("imagine_ready", 7870)]


def test_imagine_watcher_failed_rolls_back(env):
    env.ctl.comfy_process, env.ctl.imagine_process = env.comfy(), env.imagine()
    env.iw.failed.emit("Imagine упал")
    assert env.ctl.comfy_process.stopped and env.ctl.imagine_process.stopped
    env.view.set_status.assert_called_with("Imagine упал")


def test_watcher_progress_goes_to_view(env):
    env.cw.progress.emit("ждём…")
    env.iw.progress.emit("ждём Imagine…")
    assert [c.args for c in env.view.update_launch_progress.call_args_list] == [
        ("ждём…",), ("ждём Imagine…",),
    ]


def test_default_watchers_are_created_when_not_injected(monkeypatch):
    created = []

    class W(FakeWatcher):
        def __init__(self, loc=None, parent=None):
            super().__init__()
            created.append((type(self).__name__, loc, parent))

    class LW(W): pass
    class IW(W): pass

    monkeypatch.setattr(lc, "LaunchWatcher", LW)
    monkeypatch.setattr(lc, "ImagineLaunchWatcher", IW)
    view = MagicMock()
    ctl = lc.LaunchController(view, MagicMock(), lambda: "dark", loc="LOC")
    assert [c[0] for c in created] == ["LW", "IW"]
    assert all(c[1] == "LOC" and c[2] is ctl for c in created)


# -- Imagine Pony ----------------------------------------------------------------


def test_server_ready_pony_interface_starts_pony_not_imagine(env):
    env.cfg["interface"] = "imagine_pony"
    env.cfg["imagine_pony"] = {"port": 7890}
    env.ctl.on_server_ready()
    proc = env.pony.instances[0]
    assert env.imagine.instances == []
    assert env.ctl.imagine_process is proc and proc.started is True
    assert proc.kwargs == {
        "host": "127.0.0.1", "port": 7890, "comfy_host": "127.0.0.1", "comfy_port": 8188,
    }
    env.iw.start.assert_called_once_with(7890, proc, "Imagine Pony")
    assert env.signals == []


def test_pony_defaults_port_7862(env):
    env.cfg.update(interface="imagine_pony")
    env.cfg.pop("imagine_pony", None)
    env.ctl.on_server_ready()
    assert env.pony.instances[0].kwargs["port"] == 7862


def test_pony_ready_signals_pony_port_not_imagine_port(env):
    env.cfg.update(interface="imagine_pony", imagine_pony={"port": 7890})
    env.ctl.on_imagine_ready()
    assert env.signals == [("imagine_ready", 7890)]


def test_pony_start_error_rolls_back_both_processes(env):
    env.cfg["interface"] = "imagine_pony"
    env.ctl.comfy_process = env.comfy()
    original_init = env.pony.__init__

    def init_failing(self, *a, **k):
        original_init(self, *a, **k)
        self.start_error = RuntimeError("порт занят")

    env.pony.__init__ = init_failing
    env.ctl.on_server_ready()

    assert env.pony.instances[0].stopped is True
    assert env.ctl.comfy_process.stopped is True
    status = env.view.set_status.call_args.args[0]
    assert "Не удалось запустить Imagine Pony" in status and "порт занят" in status
    env.iw.start.assert_not_called()
    assert env.signals == []
