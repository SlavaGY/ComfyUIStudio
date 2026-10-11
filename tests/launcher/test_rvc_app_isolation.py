"""
Приложение comfyui_studio/rvc запускается питоном venv Ultimate RVC, где нет
PySide6 и нет остального кода Studio (в PYTHONPATH лежит только пакет). Любой
импорт Qt/launcher/imagine/... оттуда сломал бы запуск на машине пользователя,
а на машине разработчика (где всё установлено) этого не заметно -- поэтому
проверяется статически.
"""

import ast
from pathlib import Path

RVC = Path(__file__).resolve().parents[2] / "comfyui_studio" / "rvc"

# Что из comfyui_studio допустимо (и то под try/except ImportError в main.py).
ALLOWED_STUDIO_IMPORTS = {"shared_theme", "shared_language"}
FORBIDDEN_TOP = {"PySide6", "PyQt5", "PyQt6", "shiboken6"}


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, 0
        elif isinstance(node, ast.ImportFrom):
            if node.module == "comfyui_studio" and node.level == 0:
                # `from comfyui_studio import shared_theme` -- это модуль
                # comfyui_studio.shared_theme, а не импорт всего пакета.
                for alias in node.names:
                    yield f"comfyui_studio.{alias.name}", 0
            else:
                yield (node.module or ""), node.level


def test_rvc_app_does_not_import_qt_or_other_studio_code():
    problems = []
    for path in RVC.rglob("*.py"):
        for module, level in _imports(path):
            top = module.split(".")[0]
            if top in FORBIDDEN_TOP:
                problems.append((path.name, module))
            if level == 0 and top == "comfyui_studio":
                name = module.split(".")[1] if "." in module else ""
                if name not in ALLOWED_STUDIO_IMPORTS:
                    problems.append((path.name, module))
            if level > 0 and "launcher" in module:
                problems.append((path.name, module))
    assert problems == []


def test_ultimate_rvc_is_imported_only_in_engine():
    offenders = []
    for path in RVC.rglob("*.py"):
        if path.name == "engine.py":
            continue
        for module, _level in _imports(path):
            if module.split(".")[0] == "ultimate_rvc":
                offenders.append(path.name)
    assert offenders == []


def test_static_assets_use_relative_paths():
    html = (RVC / "static" / "index.html").read_text(encoding="utf-8")
    for attr in ('src="/', 'href="/'):
        assert attr not in html, "ведущий / ломает открытие через прокси"
    js = (RVC / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert 'fetch("/' not in js and "fetch('/" not in js and "fetch(`/" not in js
