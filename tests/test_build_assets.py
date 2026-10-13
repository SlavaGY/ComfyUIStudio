"""Страж сборки: не-.py файлы пакета должны попадать и в exe, и в wheel.

Почему этот тест существует: собранный exe однажды ушёл пользователям без
``rvc_app_src`` (режимы Ultimate RVC падали на «Не найден пакет
comfyui_studio.rvc»), потому что нужные ``datas`` остались только в
не используемых спеках, а ни один тест не связывал ассеты на диске с тем,
что реально упаковывает ``build_exe.bat``. PyInstaller сам кладёт в сборку
только .py, всё остальное (html/js/css/json/qss/png/ts/qm) нужно
перечислить в ``datas`` руками; для wheel то же делает ``package-data``.

Тест читает файлы как текст (spec и pyproject не исполняются), поэтому
не требует ни PyInstaller, ни Qt.
"""

from __future__ import annotations

import ast
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
PKG = ROOT / "comfyui_studio"
SPEC = ROOT / "ComfyUIStudio.spec"
PYPROJECT = ROOT / "pyproject.toml"

# Файлы под comfyui_studio/, которые сознательно не упаковываются.
# Каждый пункт -- с причиной; добавлять сюда нужно осознанно.
NOT_PACKAGED_SUFFIXES = {".py", ".pyc", ".pyo"}
NOT_PACKAGED_PARTS = {"__pycache__"}


def _asset_files() -> list[Path]:
    result = []
    for path in PKG.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix in NOT_PACKAGED_SUFFIXES:
            continue
        if NOT_PACKAGED_PARTS & set(path.relative_to(ROOT).parts):
            continue
        result.append(path)
    return sorted(result)


def _spec_tree() -> ast.Module:
    return ast.parse(SPEC.read_text(encoding="utf-8"), filename=str(SPEC))


def _assigned_literal(tree: ast.Module, name: str):
    """Литерал, присвоенный верхнеуровневой переменной (последнее присваивание)."""
    value = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    value = ast.literal_eval(node.value)
    return value


def _spec_datas() -> list[tuple[str, str]]:
    datas = _assigned_literal(_spec_tree(), "datas")
    assert datas, "в ComfyUIStudio.spec не найден литерал datas = [...]"
    return [(str(src), str(dst)) for src, dst in datas]


def _spec_hiddenimports() -> list[str]:
    """Только литералы: и стартовое присваивание, и ``hiddenimports += [...]``."""
    items: list[str] = []
    for node in _spec_tree().body:
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == "hiddenimports" for t in node.targets):
                items += list(ast.literal_eval(node.value))
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "hiddenimports":
                try:
                    items += list(ast.literal_eval(node.value))
                except ValueError:
                    pass  # += _hiddenimports внутри цикла collect_all -- не литерал
    return items


def test_single_spec_only():
    """build_exe.bat собирает ровно один spec; других быть не должно."""
    specs = sorted(p.name for p in ROOT.glob("*.spec"))
    assert specs == ["ComfyUIStudio.spec"], specs
    bat = (ROOT / "build_exe.bat").read_text(encoding="utf-8", errors="ignore")
    assert 'pyinstaller --noconfirm --clean "ComfyUIStudio.spec"' in bat


def test_every_datas_source_exists():
    missing = [src for src, _ in _spec_datas() if not (ROOT / src).exists()]
    assert not missing, f"datas ссылаются на несуществующие пути: {missing}"


def test_every_asset_is_in_spec_datas():
    sources = [(ROOT / src).resolve() for src, _ in _spec_datas()]

    def covered(path: Path) -> bool:
        p = path.resolve()
        return any(p == s or s in p.parents for s in sources)

    uncovered = [str(p.relative_to(ROOT)) for p in _asset_files() if not covered(p)]
    assert not uncovered, (
        "эти файлы есть в пакете, но НЕ попадут в exe (нет в datas "
        f"ComfyUIStudio.spec): {uncovered}"
    )


@pytest.mark.skipif(tomllib is None, reason="нет tomllib/tomli для чтения pyproject.toml")
def test_every_asset_is_in_package_data():
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    patterns = config["tool"]["setuptools"]["package-data"]["comfyui_studio"]
    matched: set[Path] = set()
    for pattern in patterns:
        matched.update(p.resolve() for p in PKG.glob(pattern) if p.is_file())
    uncovered = [
        str(p.relative_to(ROOT)) for p in _asset_files() if p.resolve() not in matched
    ]
    assert not uncovered, (
        "эти файлы есть в пакете, но НЕ попадут в wheel (нет в "
        f"[tool.setuptools.package-data]): {uncovered}"
    )


def test_rvc_payload_is_bundled():
    """rvc_process.rvc_app_source_root() в exe ищет _MEIPASS/rvc_app_src."""
    dests = {dst.replace("\\", "/") for _, dst in _spec_datas()}
    assert any(d == "rvc_app_src" or d.startswith("rvc_app_src/") for d in dests)
    assert "rvc_app_src/comfyui_studio/rvc" in dests
    for needed in ("__init__.py", "app_paths.py", "shared_theme.py", "shared_language.py"):
        assert (f"comfyui_studio/{needed}", "rvc_app_src/comfyui_studio") in _spec_datas(), needed


def test_ssh_setup_script_is_bundled():
    """launcher/core/ssh_setup.find_script() в exe ищет скрипт под
    _MEIPASS/tools/ssh_setup -- без записи в datas кнопка «Настроить
    SSH-доступ» в собранной версии не находила бы скрипт."""
    assert ("tools/ssh_setup", "tools/ssh_setup") in _spec_datas()
    assert (ROOT / "tools" / "ssh_setup" / "Setup-SshAccess.ps1").is_file()


def test_hiddenimports_point_to_existing_modules():
    """Ссылки spec на удалённые модули (embedding_worker и т.п.) -- мусор."""
    broken = []
    for name in _spec_hiddenimports():
        if not name.startswith("comfyui_studio."):
            continue
        rel = Path(*name.split("."))
        if not ((ROOT / rel).with_suffix(".py").exists() or (ROOT / rel / "__init__.py").exists()):
            broken.append(name)
    assert not broken, f"hiddenimports ссылаются на отсутствующие модули: {broken}"


def test_rvc_payload_modules_import_only_bundled_modules():
    """Файлы comfyui_studio/*.py из rvc_app_src читает ОТДЕЛЬНЫЙ питон
    Ultimate RVC: всё, что они импортируют из пакета, должно лежать там же.
    Иначе, например, shared_theme -> jsonfile молча отвалится (RVC ловит
    ImportError и работает без общей темы)."""
    shipped = {
        Path(src).stem
        for src, dst in _spec_datas()
        if dst.replace("\\", "/") == "rvc_app_src/comfyui_studio" and src.endswith(".py")
    }
    missing = []
    for stem in sorted(shipped):
        tree = ast.parse((PKG / f"{stem}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                # from . import app_paths / from .jsonfile import x
                names = [node.module] if node.module else [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0 and node.module.startswith("comfyui_studio."):
                names = [node.module.split(".")[1]]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if (PKG / f"{top}.py").exists() and top not in shipped:
                    missing.append(f"{stem}.py -> {top}.py")
    assert not missing, f"не попадут в rvc_app_src: {missing}"


def test_spec_has_no_phantom_hiddenimports():
    """cachetools/pyasn1/rsa/six -- транзитивные зависимости google-auth:
    PyInstaller находит их сам (а ``six`` google-auth давно не использует),
    явные строки были мусором, который прикрывал реальную проблему."""
    phantom = {"cachetools", "pyasn1", "rsa", "six"}
    assert not phantom & set(_spec_hiddenimports())


def test_spec_collects_google_auth_with_metadata():
    """google-auth (push, remote/fcm.py) собирается целиком: collect_all для
    обычных пакетов google.auth/google.oauth2 (сам ``google`` -- namespace,
    для него collect_all не годится) + dist-info через copy_metadata."""
    tree = _spec_tree()
    collected: set[str] = set()
    metadata: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple):
            uses_collect_all = any(
                isinstance(n, ast.Call) and getattr(n.func, "id", "") == "collect_all"
                for n in ast.walk(node)
            )
            if uses_collect_all:
                collected |= {
                    e.value for e in node.iter.elts if isinstance(e, ast.Constant)
                }
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "id", "") == "copy_metadata"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            metadata.add(node.args[0].value)
    assert {"google.auth", "google.oauth2"} <= collected, collected
    assert "google-auth" in metadata, metadata
    assert "google" not in collected, "collect_all('google') для namespace-пакета ненадёжен"
