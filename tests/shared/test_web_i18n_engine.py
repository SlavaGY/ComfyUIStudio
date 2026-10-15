"""Общий JS-движок переводов Imagine / Imagine Pony под Node.

Движок (comfyui_studio/web_common/static/js/i18n_engine.js) раньше жил
двумя копиями по ~150 строк, и проверить его можно было только руками в
браузере. Тесты лежат в tests/shared/js/i18n_engine_harness.js (подставной
DOM, браузер и jsdom не нужны); здесь -- только запуск и разбор результата.
Без Node.js тест пропускается: на Windows-раннере GitHub Actions Node есть
из коробки.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
PACKAGE = ROOT / "comfyui_studio"
HARNESS = ROOT / "tests" / "shared" / "js" / "i18n_engine_harness.js"
ENGINE = PACKAGE / "web_common" / "static" / "js" / "i18n_engine.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="нужен Node.js для проверки JS-движка")


def test_engine_and_app_dictionaries_behave_under_node():
    command = [
        NODE,
        str(HARNESS),
        str(ENGINE),
        f"imagine={PACKAGE / 'imagine' / 'static' / 'js' / 'i18n.js'}",
        f"imaginepony={PACKAGE / 'imagine_pony' / 'static' / 'js' / 'i18n.js'}",
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=ROOT
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASSED" in result.stdout and "FAIL" not in result.stdout


def test_engine_file_has_valid_javascript_syntax():
    result = subprocess.run(
        [NODE, "--check", str(ENGINE)], capture_output=True, text=True, encoding="utf-8", timeout=60
    )
    assert result.returncode == 0, result.stderr
