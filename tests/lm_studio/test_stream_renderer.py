"""Exercise the real JavaScript scheduler and DOM updates with deterministic timers."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest


def test_batched_stream_renderer():
    node = os.environ.get("NODE_BINARY") or shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the JavaScript renderer regression test")
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [node, str(Path(__file__).with_name("stream_renderer.cjs")),
         str(root / "comfyui_studio/lm_studio/static/js/stream_renderer.js")],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
