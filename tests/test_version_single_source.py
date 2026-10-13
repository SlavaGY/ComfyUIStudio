"""Страж: версия Python-части живёт в одном месте.

Раньше версий было три (0.1.0 в pyproject.toml, 0.1.0 в
``comfyui_studio/__init__.py`` и 0.11.1 в ``promptvault/config.py``), и ни
одна не проверялась. Теперь источник один -- ``comfyui_studio.__version__``;
pyproject.toml читает её как ``dynamic``, PromptVault использует ту же.
Тест не требует Qt: ``promptvault.config`` импортирует только stdlib.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - окружение Python 3.10
    try:
        import tomli as tomllib
    except ImportError:  # pragma: no cover
        tomllib = None

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"

pytestmark = pytest.mark.skipif(tomllib is None, reason="нет tomllib/tomli")


def _project() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_pyproject_has_no_static_version():
    project = _project()["project"]
    assert "version" not in project, "статическая version в pyproject.toml вернёт рассинхрон"
    assert "version" in project["dynamic"]


def test_pyproject_reads_version_from_package():
    dynamic = _project()["tool"]["setuptools"]["dynamic"]
    assert dynamic["version"] == {"attr": "comfyui_studio.__version__"}


def test_package_version_is_a_plain_string_literal():
    """setuptools разбирает ``attr`` статически (без импорта пакета) только
    если ``__version__`` -- простой строковый литерал в __init__.py."""
    text = (ROOT / "comfyui_studio" / "__init__.py").read_text(encoding="utf-8")
    assert re.search(r'^__version__ = "\d+\.\d+\.\d+[^"]*"$', text, re.MULTILINE), text


def test_promptvault_version_is_the_studio_version():
    import comfyui_studio
    from comfyui_studio.promptvault.config import APP_VERSION

    assert APP_VERSION == comfyui_studio.__version__


def test_no_other_hardcoded_version_in_python_package():
    """Новый ``APP_VERSION = "x.y.z"`` / ``__version__ = "x.y.z"`` где-нибудь
    ещё -- это снова второй источник."""
    pattern = re.compile(r'^\s*(?:APP_VERSION|__version__)\s*=\s*["\']\d', re.MULTILINE)
    offenders = []
    for path in (ROOT / "comfyui_studio").rglob("*.py"):
        if path == ROOT / "comfyui_studio" / "__init__.py":
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"второй источник версии: {offenders}"
