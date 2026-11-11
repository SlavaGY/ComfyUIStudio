"""Optional real-browser regression with an isolated headless Chrome profile.

Set STUDIO_PLAYWRIGHT_MODULE to a Playwright Node module and NODE_BINARY when
Node is not on PATH. Regular pager and API tests need neither dependency.
"""

import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from comfyui_studio.lm_studio.backend import create_app


class Engine:
    state, error = "ready", ""

    async def start(self):
        pass

    async def close(self):
        pass

    async def request(self, *args, **kwargs):
        return {"models": []}


def test_real_page_history_pagination_and_search(tmp_path):
    node = os.environ.get("NODE_BINARY") or shutil.which("node")
    if not node or not os.environ.get("STUDIO_PLAYWRIGHT_MODULE"):
        pytest.skip("Configure STUDIO_PLAYWRIGHT_MODULE and Node for the optional browser test")
    app = create_app({}, tmp_path / "data", runtime=Engine())
    for index in range(120):
        chat = app.state.store.create()
        chat["title"] = "ЁЖИК target" if index == 0 else f"Conversation {index}"
        if index == 0:
            chat["messages"] = [
                {"role": "user" if item % 2 == 0 else "assistant", "content": f"Message {item}"}
                for item in range(240)
            ]
        app.state.store.save(chat)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", http="h11", ws="none", loop="asyncio"))
    worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started:
            assert worker.is_alive() and time.monotonic() < deadline, "Test server did not start"
            time.sleep(0.01)
        result = subprocess.run(
            [node, str(Path(__file__).with_name("history_browser.cjs")), f"http://127.0.0.1:{port}/"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        server.should_exit = True
        worker.join(timeout=5)
        listener.close()
