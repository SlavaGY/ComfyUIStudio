"""
Тесты разбиения promptgen.py (этап R10 плана рефакторинга).

Основные поведенческие тесты генератора лежат в test_promptgen.py и R10
не менялись. Здесь проверяется то, что даёт именно разбиение:

  * фасад promptgen.py по-прежнему отдаёт каждое имя, которое раньше
    определялось в этом файле, и это те же самые объекты, что в
    новых модулях (в частности _Cancelled -- от него зависит отмена);
  * модули слоистые, без циклов: promptgen_* не импортируют фасад;
  * сужения ``except`` в promptgen_process / promptgen_stream работают.

Qt и сеть не нужны. Файл запускается и на Windows (в отличие от
test_promptgen.py, который там пропускается целиком).
"""
import ast
from pathlib import Path

import pytest

from comfyui_studio.imagine.backend import (
    promptgen as pg,
    promptgen_base,
    promptgen_diag,
    promptgen_job,
    promptgen_process,
    promptgen_stream,
)

BACKEND = Path(pg.__file__).parent

# Все имена верхнего уровня, которые определял promptgen.py до R10
# (кроме изменяемого состояния логирования, см. ниже).
ORIGINAL_NAMES = [
    "MAX_TOKENS", "LOAD_TIMEOUT_S", "STREAM_TIMEOUT_S", "MAX_IMAGE_DATA_URL_LEN",
    "PromptGenError", "_Cancelled", "Job", "PromptGenerator", "generator",
    "clean_output", "estimate_vram_mb", "gpu_memory", "analyze_load_timeline",
    "build_command", "build_env", "sweep_stale",
    "_LoadProbe", "_FIRST_REPORT_S", "_REPORT_EVERY_S", "_MIB",
    "_ensure_file_logging", "_jlog", "_with_log_hint", "_psutil",
    "_max_free_mb", "_fmt_gpus", "_system_snapshot",
    "_line_seconds", "_compute_cache_dir", "_dir_size_mb",
    "_free_port", "_split_extra_args", "_llama_log_path", "_spawn",
    "_kill_tree", "_stop_process", "_exit_message",
    "_registry_path", "_registry_read", "_registry_write",
    "_register_process", "_unregister_process",
    "_read_log_tail", "_read_log_text", "_attach_kill_on_close_job",
    "_clean_image_name", "_file_mb", "_error_text", "_opener",
    "_KEY_LINE_RE", "_TS_RE", "_OFFLOAD_RE", "_CLIP_BACKEND_RE",
    "_THINK_BLOCK_RE", "_THINK_OPEN_RE", "_STATUS_DLL_NOT_FOUND",
    "log", "shared_promptgen", "promptgen_history",
]


@pytest.mark.parametrize("name", ORIGINAL_NAMES)
def test_facade_still_exposes_original_name(name):
    assert hasattr(pg, name), f"promptgen.{name} пропало после R10"


def test_facade_names_are_the_same_objects_as_in_submodules():
    assert pg._Cancelled is promptgen_base._Cancelled
    assert pg.PromptGenError is promptgen_base.PromptGenError
    assert pg.Job is promptgen_job.Job
    assert pg.clean_output is promptgen_job.clean_output
    assert pg._LoadProbe is promptgen_diag._LoadProbe
    assert pg.gpu_memory is promptgen_diag.gpu_memory
    assert pg.log is promptgen_diag.log
    assert pg._kill_tree is promptgen_process._kill_tree
    assert pg._opener is promptgen_stream._opener


def test_logger_name_is_unchanged():
    # по имени логгера настроены файл promptgen.log и фильтры в логе Imagine
    assert pg.log.name == "imagine.promptgen"


def test_cancelled_is_not_a_promptgenerror():
    # Поведение отмены: _Cancelled ловится отдельной веткой раньше общего
    # Exception и не должен быть PromptGenError (иначе отмена стала бы ошибкой).
    assert not issubclass(pg._Cancelled, pg.PromptGenError)
    assert issubclass(pg._Cancelled, Exception)


def test_mutable_logging_state_is_not_reexported():
    # намеренно: копия имени разошлась бы с настоящим значением
    for name in ("_file_handler", "_file_handler_path", "_console_handler", "_job_handle"):
        assert not hasattr(pg, name), name


def test_all_lists_only_existing_names():
    for name in pg.__all__:
        assert hasattr(pg, name), name


# -- слои, без циклов --------------------------------------------------------

_PKG = "comfyui_studio.imagine.backend."
# модуль -> от каких promptgen_*-модулей он вправе зависеть (рантайм-импорты)
ALLOWED_DEPS = {
    "promptgen_base": set(),
    "promptgen_deps": set(),
    "promptgen_diag": {"promptgen_base", "promptgen_deps"},
    "promptgen_job": set(),
    "promptgen_process": {"promptgen_base", "promptgen_deps", "promptgen_diag"},
    "promptgen_stream": {"promptgen_base", "promptgen_process"},
}


def _runtime_sibling_imports(path):
    """promptgen_*-модули, импортируемые ВНЕ блоков ``if TYPE_CHECKING``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    typing_only = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.dump(node.test):
            typing_only.update(id(n) for n in ast.walk(node))
    found = set()
    for node in ast.walk(tree):
        if id(node) in typing_only:
            continue
        if isinstance(node, ast.ImportFrom) and node.module:
            mod = node.module
            if mod.startswith(_PKG):
                found.add(mod[len(_PKG):].split(".")[0])
            elif mod == _PKG.rstrip("."):
                found.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith(_PKG):
                    found.add(a.name[len(_PKG):].split(".")[0])
    return {m for m in found if m.startswith("promptgen")}


@pytest.mark.parametrize("module", sorted(ALLOWED_DEPS))
def test_submodule_dependencies_stay_layered(module):
    deps = _runtime_sibling_imports(BACKEND / f"{module}.py")
    assert deps <= ALLOWED_DEPS[module], f"{module} импортирует лишнее: {deps - ALLOWED_DEPS[module]}"
    assert "promptgen" not in deps, f"{module} импортирует фасад promptgen (цикл)"


@pytest.mark.parametrize("module", sorted(ALLOWED_DEPS))
def test_submodules_do_not_import_qt(module):
    text = (BACKEND / f"{module}.py").read_text(encoding="utf-8")
    assert "PySide6" not in text


# -- сужения except ----------------------------------------------------------


def test_registry_read_returns_empty_for_corrupt_file(tmp_path, monkeypatch):
    path = tmp_path / "reg.json"
    monkeypatch.setattr(promptgen_process, "_registry_path", lambda: str(path))
    assert promptgen_process._registry_read() == []  # файла нет
    path.write_text("{ не json", encoding="utf-8")
    assert promptgen_process._registry_read() == []  # битый JSON
    path.write_bytes(b"\xff\xfe\x00bad")
    assert promptgen_process._registry_read() == []  # не UTF-8
    path.write_text('{"pid": 1}', encoding="utf-8")
    assert promptgen_process._registry_read() == []  # JSON не список


def test_registry_read_keeps_only_valid_entries(tmp_path, monkeypatch):
    path = tmp_path / "reg.json"
    monkeypatch.setattr(promptgen_process, "_registry_path", lambda: str(path))
    path.write_text('[{"pid": 5, "created": 1.5}, 7, {"x": 1}]', encoding="utf-8")
    assert promptgen_process._registry_read() == [{"pid": 5, "created": 1.5}]


def test_register_process_for_dead_pid_does_not_raise(tmp_path, monkeypatch):
    path = tmp_path / "reg.json"
    monkeypatch.setattr(promptgen_process, "_registry_path", lambda: str(path))

    class _Proc:
        pid = 2 ** 22 + 12345  # заведомо нет такого процесса

    promptgen_process._register_process(_Proc(), "job1")  # psutil.NoSuchProcess глотается
    assert promptgen_process._registry_read() == []


def test_register_process_registers_live_process(tmp_path, monkeypatch):
    import os
    path = tmp_path / "reg.json"
    monkeypatch.setattr(promptgen_process, "_registry_path", lambda: str(path))

    class _Proc:
        pid = os.getpid()  # живой процесс -- этот самый

    promptgen_process._register_process(_Proc(), "job1")
    entries = promptgen_process._registry_read()
    assert [e["pid"] for e in entries] == [os.getpid()]
    assert entries[0]["job"] == "job1"
    promptgen_process._unregister_process(os.getpid())
    assert promptgen_process._registry_read() == []


def test_stream_http_error_with_garbage_body_still_raises_promptgenerror(monkeypatch):
    import io
    import urllib.error

    class _Opener:
        def open(self, req, timeout=None):
            raise urllib.error.HTTPError(
                "http://x", 500, "boom", {}, io.BytesIO(b"\xff not json"))

    monkeypatch.setattr(promptgen_stream, "_opener", _Opener())
    job = promptgen_job.Job()
    with pytest.raises(pg.PromptGenError, match="500"):
        promptgen_stream.stream_completion(job, 1, "p", None, _FakeProc(), "log")


def test_stream_http_error_with_non_object_json_body_is_tolerated(monkeypatch):
    import io
    import urllib.error

    class _Opener:
        def open(self, req, timeout=None):
            raise urllib.error.HTTPError("http://x", 400, "bad", {}, io.BytesIO(b"[1, 2]"))

    monkeypatch.setattr(promptgen_stream, "_opener", _Opener())
    job = promptgen_job.Job()
    with pytest.raises(pg.PromptGenError, match="400"):
        promptgen_stream.stream_completion(job, 1, "p", None, _FakeProc(), "log")


class _FakeProc:
    returncode = None

    def poll(self):
        return None


def test_kill_tree_survives_missing_taskkill(monkeypatch):
    """Если taskkill не запускается (OSError) или виснет (таймаут), остановка
    не падает: сообщение попадает в отчёт."""
    import os

    if os.name != "nt":
        pytest.skip("ветка taskkill только для Windows")

    def boom(*a, **k):
        raise FileNotFoundError("taskkill")

    monkeypatch.setattr(promptgen_process, "_psutil", lambda: None)  # ветка без psutil
    monkeypatch.setattr(promptgen_process.subprocess, "run", boom)

    class _P:
        pid = 2 ** 22 + 999

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0  # процесс уже завершён

        def kill(self):
            pass

    report = promptgen_process._kill_tree(_P())
    assert "taskkill" in report
