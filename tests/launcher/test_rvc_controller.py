"""
Тесты RvcController (этап RVC-1): переходы состояний stopped -> starting ->
running -> failed/stopped. Настоящие процессы и watcher не используются --
подставлены фейки. QApplication не нужен (только QObject/сигналы/QTimer).
"""

import pytest

QtCore = pytest.importorskip("PySide6.QtCore")

from comfyui_studio.errors import ProcessStartError  # noqa: E402
from comfyui_studio.launcher.ui.rvc_controller import (  # noqa: E402
    FAILED,
    RUNNING,
    STARTING,
    STOPPED,
    RvcController,
)


class FakeWatcher(QtCore.QObject):
    ready = QtCore.Signal()
    failed = QtCore.Signal(str)
    progress = QtCore.Signal(str)

    def __init__(self):
        super().__init__()
        self.started = None
        self.stopped = 0

    def start(self, port, process):
        self.started = (port, process)

    def stop(self):
        self.stopped += 1


class FakeProcess:
    instances = []

    def __init__(self, settings, log_path=None, **kwargs):
        self.settings = settings
        self.kwargs = kwargs
        self.running = False
        self.fail_with = None
        self.stopped = False
        FakeProcess.instances.append(self)

    def start(self):
        if FakeProcess.fail_next:
            raise ProcessStartError("порт занят")
        self.running = True

    def is_running(self):
        return self.running

    def exit_code(self):
        return None if self.running else 1

    def stop(self):
        self.running = False
        self.stopped = True


FakeProcess.fail_next = False


class Cfg:
    def __init__(self, cfg):
        self.cfg = cfg


@pytest.fixture
def valid_root(tmp_path):
    import os

    os.makedirs(tmp_path / "src" / "ultimate_rvc")
    scripts = tmp_path / "uv" / ".venv" / "Scripts"
    os.makedirs(scripts)
    (scripts / "urvc-web.exe").write_text("")  # режим gradio
    (scripts / "python.exe").write_text("")    # режим app (приложение Studio)
    return str(tmp_path)


def _make(cfg):
    FakeProcess.instances = []
    FakeProcess.fail_next = False
    watcher = FakeWatcher()
    ctl = RvcController(Cfg(cfg), watcher=watcher, process_factory=FakeProcess)
    states, ready = [], []
    ctl.state_changed.connect(lambda s, m: states.append((s, m)))
    ctl.ready.connect(ready.append)
    return ctl, watcher, states, ready


def test_invalid_install_fails_without_starting_process():
    ctl, watcher, states, _ = _make({"rvc": {"root_path": ""}})
    ctl.start()
    assert states[-1][0] == FAILED
    assert FakeProcess.instances == []
    assert watcher.started is None


def test_port_conflict_with_studio_service_fails(valid_root):
    ctl, _w, states, _ = _make({"rvc": {"root_path": valid_root, "port": 7860}})
    ctl.start()
    assert states[-1][0] == FAILED
    assert "Imagine" in states[-1][1]
    assert FakeProcess.instances == []


def test_happy_path_starting_then_running(valid_root):
    ctl, watcher, states, ready = _make({"rvc": {"root_path": valid_root, "port": 7870}})
    ctl.start()
    assert states[-1][0] == STARTING
    assert watcher.started[0] == 7870
    watcher.ready.emit()
    assert ctl.state == RUNNING
    assert ready == [7870]
    assert ctl.is_active()


def test_start_when_running_does_not_spawn_second_process(valid_root):
    ctl, watcher, _s, ready = _make({"rvc": {"root_path": valid_root, "port": 7870}})
    ctl.start()
    watcher.ready.emit()
    ctl.start()
    assert len(FakeProcess.instances) == 1
    assert ready == [7870, 7870]  # повторный «Запустить» просто просит показать браузер


def test_process_start_error_becomes_failed(valid_root):
    ctl, _w, states, _ = _make({"rvc": {"root_path": valid_root, "port": 7870}})
    FakeProcess.fail_next = True
    ctl.start()
    assert states[-1][0] == FAILED
    assert "порт занят" in states[-1][1]
    assert not ctl.is_active()


def test_watcher_failure_stops_process(valid_root):
    ctl, watcher, states, _ = _make({"rvc": {"root_path": valid_root, "port": 7870}})
    ctl.start()
    proc = FakeProcess.instances[0]
    watcher.failed.emit("таймаут")
    assert proc.stopped
    assert states[-1] == (FAILED, "таймаут")


def test_crash_after_ready_is_detected(valid_root):
    ctl, watcher, states, _ = _make({"rvc": {"root_path": valid_root, "port": 7870}})
    ctl.start()
    watcher.ready.emit()
    FakeProcess.instances[0].running = False  # RVC упал сам
    ctl._check_alive()
    assert states[-1][0] == FAILED
    assert not ctl.is_active()


def test_start_passes_kind_data_dir_and_log_sink(valid_root):
    ctl, _w, _s, _r = _make({"rvc": {"root_path": valid_root, "port": 7870}})
    ctl.start("app")
    kwargs = FakeProcess.instances[0].kwargs
    assert kwargs["mode"] == "app"
    assert kwargs["data_dir"].endswith("rvc")
    lines = []
    ctl.log_line.connect(lines.append)
    kwargs["on_line"]("строка")
    assert lines == ["строка"]
    assert ctl.kind == "app"


def test_start_other_kind_while_active_is_refused_without_changing_state(valid_root):
    ctl, _w, states, _r = _make({"rvc": {"root_path": valid_root, "port": 7870}})
    notices = []
    ctl.notice.connect(notices.append)
    ctl.start("app")
    before = list(states)
    ctl.start("gradio")
    assert len(FakeProcess.instances) == 1
    assert states == before          # состояние НЕ ушло в failed: процесс жив
    assert ctl.state == STARTING and ctl.is_active()
    assert len(notices) == 1 and "другом режиме" in notices[0]
    assert ctl.kind == "app"  # прежний процесс не тронут


def test_app_kind_validates_with_python_not_urvc_web(tmp_path):
    import os

    os.makedirs(tmp_path / "src" / "ultimate_rvc")
    scripts = tmp_path / "uv" / ".venv" / "Scripts"
    os.makedirs(scripts)
    (scripts / "python.exe").write_text("")  # urvc-web.exe нет
    ctl, _w, states, _r = _make({"rvc": {"root_path": str(tmp_path), "port": 7870}})
    ctl.start("gradio")
    assert states[-1][0] == FAILED
    ctl.start("app")
    assert states[-1][0] == STARTING


def test_stop_kills_process_and_resets_state(valid_root):
    ctl, watcher, states, _ = _make({"rvc": {"root_path": valid_root, "port": 7870}})
    ctl.start()
    watcher.ready.emit()
    ctl.stop()
    assert FakeProcess.instances[0].stopped
    assert states[-1][0] == STOPPED
    assert watcher.stopped >= 1
