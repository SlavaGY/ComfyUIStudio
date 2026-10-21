import os
import shutil
import subprocess
from pathlib import Path

import pytest


def test_background_poll_scheduler():
    node = os.environ.get("NODE_BINARY") or shutil.which("node")
    if not node:
        pytest.skip("Node.js is required")
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [node, str(Path(__file__).with_name("js") / "background_poll.cjs"),
         str(root / "comfyui_studio/web_common/static/js/background_poll.js")],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
