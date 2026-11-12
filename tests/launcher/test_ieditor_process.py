"""The same IEditor launch must work from sources and the bundled exe."""

import socket
import sys
from unittest.mock import MagicMock

import pytest

from comfyui_studio.errors import ProcessStartError
from comfyui_studio.ieditor.__main__ import IEDITOR_CLI_FLAG
from comfyui_studio.launcher.core import ieditor_process as process


def test_source_launch_passes_the_actual_comfyui_port(monkeypatch):
    monkeypatch.setattr(process, "find_missing_modules", lambda names: [])
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    command, cwd, error = process.resolve_ieditor_launch("127.0.0.1", 7865, "127.0.0.1", 8189)
    assert error is None and cwd == process.PROJECT_ROOT
    assert command == [sys.executable, "-m", "comfyui_studio.ieditor", "--host", "127.0.0.1", "--port", "7865", "--comfy-host", "127.0.0.1", "--comfy-port", "8189"]


def test_frozen_launch_reenters_the_exe_before_qt_startup(monkeypatch):
    monkeypatch.setattr(process, "find_missing_modules", lambda names: [])
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    command, cwd, error = process.resolve_ieditor_launch("127.0.0.1", 7865, "127.0.0.1", 8188)
    assert error is None and cwd is None
    assert command[:2] == [sys.executable, IEDITOR_CLI_FLAG]


def test_missing_upload_dependency_is_reported_before_start(monkeypatch):
    monkeypatch.setattr(process, "find_missing_modules", lambda names: ["multipart"])
    command, cwd, error = process.resolve_ieditor_launch("127.0.0.1", 7865, "127.0.0.1", 8188)
    assert command is None and cwd is None
    assert "multipart" in error and "IEditor" in error


def test_busy_port_fails_with_actionable_error_without_starting_another_process(monkeypatch):
    monkeypatch.setattr(process, "find_missing_modules", lambda names: [])
    with socket.socket() as listener:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        editor = process.IEditorProcess("127.0.0.1", port, "127.0.0.1", 8188)
        spawn = MagicMock()
        monkeypatch.setattr(editor, "_spawn_streamed", spawn)
        with pytest.raises(ProcessStartError, match=f"Порт IEditor {port} уже занят"):
            editor.start()
        spawn.assert_not_called()
        assert listener.getsockname()[1] == port  # Existing server is untouched.


def test_free_port_probe_releases_its_socket():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process.check_ieditor_port("127.0.0.1", port)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))
