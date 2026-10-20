"""Launch Studio's chat UI as a local self-exec subprocess, like Imagine."""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

import psutil

from ...errors import ProcessStartError
from ...lm_studio.__main__ import LM_STUDIO_CLI_FLAG
from .constants import APP_DIR, CONFIG_PATH, PROJECT_ROOT
from .managed_process import ManagedProcess, find_missing_modules
from .rvc_process import is_port_in_use


def app_port(cfg):
    section = cfg.get("lm_studio") or {}
    try:
        port = int(section.get("ui_port", 7864))
        if not 1 <= port <= 65535:
            raise ValueError
        return port
    except (ValueError, TypeError) as error:
        raise ProcessStartError("Некорректный порт приложения LM Studio.") from error


def is_app_ready(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
            return json.load(response).get("app") == "studio-lm-studio"
    except (OSError, ValueError, urllib.error.URLError):
        return False


class LmStudioAppProcess(ManagedProcess):
    label = "LM Studio · Studio"

    def __init__(self, cfg):
        super().__init__()
        self.port = app_port(cfg)
        self._attached = None

    def start(self):
        missing = find_missing_modules(("fastapi", "uvicorn", "httpx", "pydantic", "pypdf"))
        if missing:
            raise ProcessStartError("Установите зависимости Studio: pip install .[imagine] (" + ", ".join(missing) + ")")
        if is_port_in_use(self.port):
            if is_app_ready(self.port):
                from ...remote.process_by_port import find_pid_listening_on_port
                pid = find_pid_listening_on_port(self.port)
                if pid is not None:
                    try:
                        self._attached = psutil.Process(pid)
                        self._attached.create_time()  # Cache identity before PID reuse.
                        return
                    except psutil.Error as error:
                        raise ProcessStartError("Не удалось подключиться к запущенному LM Studio.") from error
            raise ProcessStartError(f"Порт приложения LM Studio {self.port} уже занят.")
        args = ["--port", str(self.port), "--config", CONFIG_PATH,
                "--data-dir", os.path.join(APP_DIR, "lm_studio")]
        cmd = ([sys.executable, LM_STUDIO_CLI_FLAG] if getattr(sys, "frozen", False)
               else [sys.executable, "-m", "comfyui_studio.lm_studio"]) + args
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        return self._spawn_streamed(cmd, cwd=PROJECT_ROOT, env=env)

    def stop(self):
        process = self._attached if self._attached is not None else self.proc
        if process is not None and self.is_running():
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/shutdown", data=b"{}", method="POST",
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=2):
                    pass
                process.wait(timeout=20)
                self.proc = None
                self._attached = None
                return
            except (OSError, ValueError, subprocess.TimeoutExpired, psutil.Error):
                pass
        if self._attached is not None:
            from .managed_process import terminate_process_tree
            if not self._attached.is_running() or terminate_process_tree(self._attached, self.label):
                self._attached = None
            return
        super().stop()

    def is_running(self):
        if self._attached is not None:
            return self._attached.is_running()
        return super().is_running()

    def exit_code(self):
        return None if self._attached is not None else super().exit_code()
