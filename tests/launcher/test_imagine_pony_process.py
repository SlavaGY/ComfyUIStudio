"""Сборка команды запуска Imagine Pony (core/imagine_pony_process.py)."""

import sys

from comfyui_studio.imagine_pony.__main__ import IMAGINE_PONY_CLI_FLAG
from comfyui_studio.launcher.core import imagine_pony_process as ipp


def test_source_launch_uses_module_and_project_root(monkeypatch):
    monkeypatch.setattr(ipp, "find_missing_modules", lambda names: [])
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    cmd, cwd, err = ipp.resolve_imagine_pony_launch("127.0.0.1", 7862, "127.0.0.1", 8188)
    assert err is None and cwd == ipp.PROJECT_ROOT
    assert cmd == [sys.executable, "-m", "comfyui_studio.imagine_pony",
                   "--host", "127.0.0.1", "--port", "7862",
                   "--comfy-host", "127.0.0.1", "--comfy-port", "8188"]


def test_frozen_launch_reinvokes_exe_with_hidden_flag(monkeypatch):
    monkeypatch.setattr(ipp, "find_missing_modules", lambda names: [])
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    cmd, cwd, err = ipp.resolve_imagine_pony_launch("127.0.0.1", 7862, "127.0.0.1", 8188)
    assert err is None and cwd is None
    assert cmd[:2] == [sys.executable, IMAGINE_PONY_CLI_FLAG]
    assert "--port" in cmd and "7862" in cmd


def test_missing_dependencies_fail_fast_with_hint(monkeypatch):
    monkeypatch.setattr(ipp, "find_missing_modules", lambda names: ["fastapi"])
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    cmd, cwd, err = ipp.resolve_imagine_pony_launch("127.0.0.1", 7862, "127.0.0.1", 8188)
    assert cmd is None and "fastapi" in err and "pip install .[imagine]" in err
