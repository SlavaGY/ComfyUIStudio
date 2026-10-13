"""Страж точек входа: все пути запуска ведут в ``comfyui_studio.app.run``.

``python main.py``, ``python -m comfyui_studio`` и консольная команда из
``[project.gui-scripts]`` -- одно и то же. Корневой ``main.py`` обязан
остаться (на него указывают ComfyUIStudio.spec, автозапуск и перезапуск из
исходников). Импорт идёт в подпроцессе: ``comfyui_studio.app`` при импорте
выставляет QTWEBENGINE_CHROMIUM_FLAGS, и тестовому процессу это ни к чему.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    try:
        import tomli as tomllib
    except ImportError:  # pragma: no cover
        tomllib = None

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(tomllib is None, reason="нет tomllib/tomli")
def test_gui_script_target_is_app_run():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["project"]["gui-scripts"] == {"comfyui-studio": "comfyui_studio.app:run"}


def test_app_module_imports_without_qt_and_exposes_run():
    code = (
        "import sys; import comfyui_studio.app as a; "
        "assert callable(a.run) and callable(a.main) and callable(a.dispatch_subprocess_mode); "
        "assert not any(m.startswith('PySide6') for m in sys.modules), 'Qt импортирован при import app'"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def test_dispatch_does_nothing_for_ordinary_arguments():
    code = (
        "import sys; sys.argv = ['main.py', '--ordinary-flag']; "
        "import comfyui_studio.app as a; a.dispatch_subprocess_mode(); print('returned')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "returned"


def test_root_main_is_thin_wrapper_over_run():
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    imports = [
        n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "comfyui_studio.app"
    ]
    assert imports and [a.name for a in imports[0].names] == ["run"]
    assert not [n for n in tree.body if isinstance(n, ast.FunctionDef)], "логика должна жить в app.py"


def test_python_dash_m_entry_exists():
    text = (ROOT / "comfyui_studio" / "__main__.py").read_text(encoding="utf-8")
    assert "from comfyui_studio.app import run" in text
