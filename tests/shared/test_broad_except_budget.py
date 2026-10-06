"""
Бюджет широких обработчиков исключений (этап R11.2 плана рефакторинга).

`except Exception` допустим как граница подсистемы (диагностика, верх
рабочего потока, внешний колбэк), но число таких обработчиков не должно
расти. В ruff это правило BLE001; здесь то же самое проверяется без ruff
и дополнительно держит число по каждому файлу:

  * новый файл с `except Exception` -- тест падает: сузьте исключение или
    сознательно добавьте файл в BASELINE и в per-file-ignores (pyproject.toml);
  * в файле стало больше обработчиков -- падает;
  * в файле стало МЕНЬШЕ -- тоже падает, чтобы число в BASELINE уменьшали
    вместе с правкой («храповик»: сужения не откатываются незаметно).

Считаются обработчики с типом Exception/BaseException (в т. ч. в кортеже) и
голые `except:`; скан только по comfyui_studio/ и main.py.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# файл (от корня репозитория) -> число широких обработчиков
BASELINE = {
    "comfyui_studio/imagine/backend/comfy_client.py": 1,
    "comfyui_studio/imagine/backend/progress_forwarder.py": 1,
    "comfyui_studio/imagine/backend/promptgen.py": 5,
    "comfyui_studio/imagine/backend/promptgen_diag.py": 12,
    "comfyui_studio/imagine/backend/promptgen_process.py": 4,
    "comfyui_studio/launcher/core/background.py": 1,
    "comfyui_studio/launcher/core/comfy_api.py": 2,
    "comfyui_studio/launcher/core/comfy_process.py": 1,
    "comfyui_studio/launcher/core/comfy_ws.py": 1,
    "comfyui_studio/launcher/core/config.py": 2,
    "comfyui_studio/launcher/core/managed_process.py": 6,
    "comfyui_studio/launcher/core/remote_net.py": 1,
    "comfyui_studio/launcher/core/system_monitor.py": 3,
    "comfyui_studio/launcher/integration/comfy_theme.py": 1,
    "comfyui_studio/launcher/ui/remote_controller.py": 1,
    "comfyui_studio/launcher/ui/settings/prompt_history_page.py": 5,
    "comfyui_studio/launcher/ui/settings/promptvault_page.py": 1,
    "comfyui_studio/launcher/ui/settings_page.py": 2,
    "comfyui_studio/mem_diagnostics.py": 4,
    "comfyui_studio/promptgen_history.py": 1,
    "comfyui_studio/promptvault/main.py": 2,
    "comfyui_studio/promptvault/utils.py": 1,
    "comfyui_studio/remote/app_launcher.py": 1,
    "comfyui_studio/remote/comfy_launcher.py": 2,
    "comfyui_studio/remote/device_store.py": 1,
    "comfyui_studio/remote/fcm.py": 2,
    "comfyui_studio/remote/generation_watcher.py": 1,
    "comfyui_studio/remote/gpu_stats.py": 3,
    "comfyui_studio/remote/mdns.py": 2,
    "comfyui_studio/remote/process_by_port.py": 1,
    "comfyui_studio/remote/ssh_config_store.py": 1,
    "comfyui_studio/remote/ws_hub.py": 1,
    "comfyui_studio/shared_promptgen.py": 3,
}

_BROAD = {"Exception", "BaseException"}


def _is_broad(node):
    if node is None:  # голый except:
        return True
    if isinstance(node, ast.Name):
        return node.id in _BROAD
    if isinstance(node, ast.Attribute):
        return node.attr in _BROAD
    if isinstance(node, ast.Tuple):
        return any(_is_broad(e) for e in node.elts)
    return False


def _scan():
    found = {}
    files = list((ROOT / "comfyui_studio").rglob("*.py")) + [ROOT / "main.py"]
    for path in files:
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        count = sum(
            1 for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler) and _is_broad(n.type)
        )
        if count:
            found[path.relative_to(ROOT).as_posix()] = count
    return found


def test_no_new_broad_except_handlers():
    found = _scan()
    grown = {f: (BASELINE.get(f, 0), n) for f, n in found.items() if n > BASELINE.get(f, 0)}
    assert grown == {}, (
        "Появились широкие `except Exception` (файл: было, стало). Сузьте исключение "
        "(OSError, ValueError, ...) или оставьте только как границу с логом и "
        f"обновите BASELINE/per-file-ignores: {grown}"
    )


def test_baseline_is_tight():
    found = _scan()
    shrunk = {f: (n, found.get(f, 0)) for f, n in BASELINE.items() if found.get(f, 0) < n}
    assert shrunk == {}, (
        "Широких обработчиков стало меньше -- уменьшите число в BASELINE "
        f"(и уберите файл из per-file-ignores, если там 0): {shrunk}"
    )


def test_baseline_matches_ruff_per_file_ignores():
    import tomllib

    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    ignores = config["tool"]["ruff"]["lint"]["per-file-ignores"]
    with_ble = {f for f, rules in ignores.items() if "BLE001" in rules}
    assert set(BASELINE) <= with_ble, sorted(set(BASELINE) - with_ble)
