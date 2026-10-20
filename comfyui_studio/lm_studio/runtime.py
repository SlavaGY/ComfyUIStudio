"""LM Studio's documented local HTTP API and optional headless CLI startup."""

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
import psutil

log = logging.getLogger(__name__)


class EngineError(RuntimeError):
    pass


def cli_status(output):
    # The desktop build prints startup progress before its final JSON line.
    for line in reversed(output.splitlines()):
        try:
            value = json.loads(line)
            if isinstance(value, dict) and "status" in value:
                return value
        except ValueError:
            continue
    raise EngineError("CLI lms не вернул состояние движка: " + output[-500:])


def find_cli(config):
    explicit = config.get("cli_path", "").strip()
    if explicit:
        return explicit
    candidate = Path.home() / ".lmstudio" / "bin" / ("lms.exe" if sys.platform == "win32" else "lms")
    return str(candidate) if candidate.is_file() else shutil.which("lms") or ""


class Runtime:
    def __init__(self, config, client=None):
        self.config = config
        port = int(config.get("api_port", 1234))
        if not 1 <= port <= 65535:
            raise ValueError("Invalid LM Studio API port")
        self.port = port
        token = config.get("api_token", "") or os.environ.get("LM_API_TOKEN", "")
        self.client = client or httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", trust_env=False,
            headers={"Authorization": f"Bearer {token}"} if token else {},
            timeout=httpx.Timeout(600, connect=5),
        )
        self.state = "starting"
        self.error = ""
        self.owns_server = False
        self.daemon_pid = None
        self.owned_desktop = None
        self.owned_models = set()

    async def command(self, *args):
        cli = find_cli(self.config)
        if not cli or not Path(cli).is_file():
            raise EngineError("CLI lms не найден. Укажите путь к lms в настройках LM Studio.")

        def run():
            try:
                result = subprocess.run(
                    [cli, *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    encoding="utf-8", errors="replace", timeout=90,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise EngineError(str(error)) from error
            if result.returncode:
                raise EngineError(result.stdout[-1500:] or f"lms: {result.returncode}")
            return result.stdout

        return await asyncio.to_thread(run)

    async def request(self, method, path, **kwargs):
        try:
            response = await self.client.request(method, path, **kwargs)
            self.check_response(response)
            return response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise EngineError(str(error)) from error

    @staticmethod
    def check_response(response):
        if response.is_success:
            return
        if response.status_code in (401, 403):
            raise EngineError("LM Studio требует API-токен. Укажите его в настройках LM Studio.")
        try:
            data = response.json()
            message = data.get("error", data.get("detail", data))
            if isinstance(message, dict):
                message = message.get("message", message)
        except ValueError:
            message = response.text[:1000]
        raise EngineError(f"LM Studio ({response.status_code}): {message}")

    async def start(self):
        try:
            try:
                await self.request("GET", "/api/v1/models", timeout=3)
            except EngineError as error:
                # Authentication and protocol errors must not restart an existing server.
                if not isinstance(error.__cause__, httpx.ConnectError):
                    raise
                if not self.config.get("auto_start_server", True):
                    raise EngineError("Запустите локальный сервер LM Studio или включите его автозапуск.") from error
                status = cli_status(await self.command("daemon", "status", "--json"))
                if status.get("status") != "running":
                    started = cli_status(await self.command("daemon", "up", "--json"))
                    if started.get("isDaemon"):
                        self.daemon_pid = started.get("pid")
                    elif started.get("pid"):
                        # Desktop-only LM Studio installations host the engine
                        # in their GUI process. Keep this app-owned instance
                        # hidden; never hide an instance that was already open.
                        self.owned_desktop = psutil.Process(started["pid"])
                        self.owned_desktop.create_time()
                        await self._hide_owned_desktop()
                await self.command("server", "start", "--bind", "127.0.0.1", "--port", str(self.port))
                self.owns_server = True
                await self.request("GET", "/api/v1/models", timeout=10)
            self.state = "ready"
            self.error = ""
        except (EngineError, ValueError, psutil.Error) as error:
            self.state, self.error = "error", str(error)
            log.error("LM Studio startup: %s", error)

    async def _hide_owned_desktop(self):
        if sys.platform != "win32" or self.owned_desktop is None:
            return
        import ctypes
        from ctypes import wintypes

        from ..launcher.core.native_window import find_app_window

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        executable = self.owned_desktop.exe()
        for _ in range(50):
            window = find_app_window(executable, {self.owned_desktop.pid})
            if window:
                user32.ShowWindow(window[0], 0)  # SW_HIDE
                return
            await asyncio.sleep(0.1)

    async def close(self):
        # Only resources started by this application are released.
        for instance in list(self.owned_models):
            try:
                await self.request("POST", "/api/v1/models/unload", json={"instance_id": instance}, timeout=10)
            except EngineError:
                log.warning("Could not unload app-owned LM Studio model")
        try:
            if self.owns_server:
                await self.command("server", "stop")
            if self.daemon_pid:
                status = cli_status(await self.command("daemon", "status", "--json"))
                if status.get("pid") == self.daemon_pid and status.get("isDaemon"):
                    await self.command("daemon", "down")
        except (EngineError, ValueError):
            log.exception("Could not release LM Studio runtime")
        if self.owned_desktop is not None and self.owned_desktop.is_running():
            from ..launcher.core.managed_process import terminate_process_tree
            await asyncio.to_thread(terminate_process_tree, self.owned_desktop, "LM Studio")
        await self.client.aclose()
