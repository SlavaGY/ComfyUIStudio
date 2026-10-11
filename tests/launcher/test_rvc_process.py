"""
Тесты процессного слоя Ultimate RVC (этап RVC-1): core/rvc_process.py.

Qt не нужен (модуль его не импортирует). Установка RVC изображается
фейковым деревом каталогов во временной папке; реальный urvc-web не
запускается -- вместо него подставляется скрипт на sys.executable.
"""

import logging
import os
import socket
import sys
import time

import pytest

from comfyui_studio.errors import ProcessStartError
from comfyui_studio.launcher.core import rvc_process
from comfyui_studio.launcher.core.rvc_process import (
    DEFAULT_RVC_PORT,
    MODE_APP,
    RvcProcess,
    build_rvc_env,
    find_rvc_python,
    find_rvc_web_entry,
    is_port_in_use,
    parse_launch_args,
    port_conflict,
    resolve_rvc_launch,
    rvc_settings,
    validate_rvc_root,
)

LOGGER = "comfyui_launcher"


def _make_install(root, venv="uv/.venv", with_entry=True, with_src=True):
    """Минимальная раскладка Ultimate RVC: src/ultimate_rvc + venv со
    скриптами. Возвращает путь к созданному urvc-web (или None)."""
    if with_src:
        os.makedirs(os.path.join(root, "src", "ultimate_rvc"))
    bin_dir = os.path.join(root, *venv.split("/"), "Scripts")
    os.makedirs(bin_dir)
    for name in ("python.exe", "python"):
        open(os.path.join(bin_dir, name), "w").close()
    if not with_entry:
        return None
    entry = os.path.join(bin_dir, "urvc-web.exe")
    open(entry, "w").close()
    return entry


def _wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# --------------------------------------------------------------------------
# Настройки


def test_rvc_settings_fills_defaults_for_old_config():
    s = rvc_settings({"port": 8188})  # config.json без секции "rvc"
    assert s == {
        "root_path": "", "port": DEFAULT_RVC_PORT, "launch_args": "", "auto_start": False,
    }


def test_rvc_settings_partial_section_keeps_missing_keys_default():
    s = rvc_settings({"rvc": {"root_path": "  D:\\AI\\ultimate-rvc ", "port": "7900"}})
    assert s["root_path"] == "D:\\AI\\ultimate-rvc"
    assert s["port"] == 7900
    assert s["auto_start"] is False


def test_rvc_settings_bad_port_falls_back():
    assert rvc_settings({"rvc": {"port": "abc"}})["port"] == DEFAULT_RVC_PORT
    assert rvc_settings({"rvc": {"port": None}})["port"] == DEFAULT_RVC_PORT


def test_rvc_settings_ignores_non_dict_section():
    assert rvc_settings({"rvc": "oops"})["port"] == DEFAULT_RVC_PORT
    assert rvc_settings(None)["port"] == DEFAULT_RVC_PORT


def test_default_port_does_not_collide_with_studio_services():
    # Именно ради этого RVC получил 7863, а не порт Gradio по умолчанию.
    assert port_conflict({}, DEFAULT_RVC_PORT) is None
    assert port_conflict({}, 7860) == "Imagine"
    assert port_conflict({}, 7861) == "Remote"
    assert port_conflict({}, 7862) == "Imagine Pony"
    assert port_conflict({"port": 8188}, 8188) == "ComfyUI"


def test_port_conflict_uses_configured_ports():
    cfg = {"imagine": {"port": 9000}, "remote": {"port": 9001}}
    assert port_conflict(cfg, 9000) == "Imagine"
    assert port_conflict(cfg, 9001) == "Remote"
    assert port_conflict(cfg, 7860) is None


# --------------------------------------------------------------------------
# Разбор аргументов


def test_parse_launch_args_empty():
    assert parse_launch_args("") == []
    assert parse_launch_args("   ") == []
    assert parse_launch_args(None) == []


def test_parse_launch_args_splits_and_respects_quotes():
    assert parse_launch_args("--share --foo bar") == ["--share", "--foo", "bar"]
    assert parse_launch_args('--cookie-file "a b.txt"') == ["--cookie-file", "a b.txt"]


def test_parse_launch_args_unbalanced_quote_does_not_raise():
    assert parse_launch_args('--foo "bar') == ["--foo", '"bar']


# --------------------------------------------------------------------------
# Поиск установки


def test_validate_rvc_root_ok_with_uv_venv(tmp_path):
    _make_install(str(tmp_path))
    ok, message = validate_rvc_root(str(tmp_path))
    assert ok, message


def test_validate_rvc_root_accepts_plain_dot_venv(tmp_path):
    _make_install(str(tmp_path), venv=".venv")
    assert validate_rvc_root(str(tmp_path))[0]


def test_validate_rvc_root_rejects_missing_or_empty():
    assert validate_rvc_root("")[0] is False
    assert validate_rvc_root(os.path.join("no", "such", "dir"))[0] is False


def test_validate_rvc_root_requires_ultimate_rvc_sources(tmp_path):
    _make_install(str(tmp_path), with_src=False)
    ok, message = validate_rvc_root(str(tmp_path))
    assert not ok and "ultimate_rvc" in message


def test_validate_rvc_root_requires_urvc_web(tmp_path):
    _make_install(str(tmp_path), with_entry=False)
    ok, message = validate_rvc_root(str(tmp_path))
    assert not ok and "urvc-web" in message


def test_find_entry_and_python(tmp_path):
    entry = _make_install(str(tmp_path))
    assert find_rvc_web_entry(str(tmp_path)) == entry
    assert find_rvc_python(str(tmp_path)).endswith("python.exe")
    assert find_rvc_web_entry(str(tmp_path / "nope")) is None
    assert find_rvc_python("") is None


def test_uv_venv_is_preferred_over_dot_venv(tmp_path):
    entry = _make_install(str(tmp_path), venv="uv/.venv")
    _make_install(str(tmp_path), venv=".venv", with_src=False)
    assert find_rvc_web_entry(str(tmp_path)) == entry


# --------------------------------------------------------------------------
# Команда и окружение


def test_build_rvc_env_sets_gradio_and_utf8_over_base():
    env = build_rvc_env({"port": 7863}, base_env={"PATH": "x", "GRADIO_SERVER_PORT": "1"})
    assert env["PATH"] == "x"
    assert env["GRADIO_SERVER_PORT"] == "7863"  # перекрывает чужое значение
    assert env["GRADIO_SERVER_NAME"] == "127.0.0.1"
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_resolve_rvc_launch_builds_command(tmp_path):
    entry = _make_install(str(tmp_path))
    settings = rvc_settings({"rvc": {
        "root_path": str(tmp_path), "port": 7870, "launch_args": "--foo bar",
    }})
    cmd, cwd, env, error = resolve_rvc_launch(settings, base_env={})
    assert error is None
    assert cmd == [entry, "--foo", "bar"]
    assert cwd == str(tmp_path)
    assert env["GRADIO_SERVER_PORT"] == "7870"


def test_resolve_rvc_launch_reports_invalid_root():
    cmd, cwd, env, error = resolve_rvc_launch(rvc_settings({}))
    assert (cmd, cwd, env) == (None, None, None)
    assert error


# --------------------------------------------------------------------------
# Сеть


def test_is_port_in_use():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        assert is_port_in_use(port)
    finally:
        s.close()
    assert not is_port_in_use(port)


# --------------------------------------------------------------------------
# RvcProcess (настоящие короткие подпроцессы)


def _patch_launch(monkeypatch, code):
    """Подменяет резолв запуска: вместо urvc-web -- python -c <code>."""
    def fake(settings, base_env=None, **_kwargs):
        return [sys.executable, "-c", code], None, dict(os.environ), None

    monkeypatch.setattr(rvc_process, "resolve_rvc_launch", fake)


def test_start_raises_on_invalid_install():
    proc = RvcProcess(rvc_settings({}))
    with pytest.raises(ProcessStartError):
        proc.start()
    assert not proc.is_running()


def test_start_refuses_busy_port(monkeypatch, tmp_path):
    _patch_launch(monkeypatch, "import time; time.sleep(60)")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    try:
        proc = RvcProcess(rvc_settings({"rvc": {"port": s.getsockname()[1]}}))
        with pytest.raises(ProcessStartError) as exc:
            proc.start()
        assert str(s.getsockname()[1]) in str(exc.value)
        assert proc.proc is None
    finally:
        s.close()


def test_start_runs_and_stop_kills(monkeypatch, tmp_path):
    _patch_launch(monkeypatch, "import time; print('hello', flush=True); time.sleep(60)")
    log_path = str(tmp_path / "logs" / "rvc.log")
    proc = RvcProcess(rvc_settings({"rvc": {"port": 17863}}), log_path=log_path)
    proc.start()
    assert proc.is_running()
    # вывод попадает в лог-файл построчно, ещё ПОКА процесс жив
    assert _wait_for(lambda: os.path.isfile(log_path) and "hello" in open(log_path, encoding="utf-8").read())
    proc.stop()
    assert not proc.is_running()
    assert proc.proc is None


def test_failed_process_logs_output_tail(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    _patch_launch(monkeypatch, "import sys; print('boom: no module named x'); sys.exit(3)")
    proc = RvcProcess(rvc_settings({"rvc": {"port": 17864}}))
    proc.start()
    assert _wait_for(lambda: not proc.is_running())
    assert proc.exit_code() == 3
    assert _wait_for(lambda: any(
        "Ultimate RVC" in r.getMessage() and "boom: no module named x" in r.getMessage()
        for r in caplog.records
    ))


def test_tail_is_bounded(monkeypatch):
    _patch_launch(monkeypatch, "for i in range(1000): print(i)")
    proc = RvcProcess(rvc_settings({"rvc": {"port": 17865}}))
    proc.start()
    assert _wait_for(lambda: not proc.is_running())
    assert _wait_for(lambda: len(proc.tail) > 0 and proc.tail[-1] == "999")
    assert len(proc.tail) == rvc_process.TAIL_LINES


def test_log_file_is_truncated_not_unbounded(monkeypatch, tmp_path):
    monkeypatch.setattr(rvc_process, "MAX_LOG_BYTES", 2000)
    _patch_launch(monkeypatch, "for i in range(2000): print('x' * 50)")
    log_path = str(tmp_path / "rvc.log")
    proc = RvcProcess(rvc_settings({"rvc": {"port": 17866}}), log_path=log_path)
    proc.start()
    assert _wait_for(lambda: not proc.is_running())
    time.sleep(0.3)  # дать читателю дописать хвост
    assert os.path.getsize(log_path) < 2000 + 200


# --------------------------------------------------------------------------
# Режим «app»: собственное приложение Studio питоном venv RVC


def test_validate_app_mode_needs_python_not_urvc_web(tmp_path):
    _make_install(str(tmp_path), with_entry=False)
    assert validate_rvc_root(str(tmp_path), MODE_APP)[0]
    assert not validate_rvc_root(str(tmp_path))[0]  # gradio-режиму urvc-web нужен


def test_validate_app_mode_requires_venv_python(tmp_path):
    os.makedirs(tmp_path / "src" / "ultimate_rvc")
    ok, message = validate_rvc_root(str(tmp_path), MODE_APP)
    assert not ok and "python" in message


def test_app_mode_command_uses_rvc_python_and_port_flag(tmp_path):
    _make_install(str(tmp_path), with_entry=False)
    settings = rvc_settings({"rvc": {"root_path": str(tmp_path), "port": 7871,
                                     "launch_args": "--foo"}})
    cmd, cwd, env, error = resolve_rvc_launch(
        settings, base_env={"PYTHONPATH": "X"}, mode=MODE_APP, data_dir="D"
    )
    assert error is None
    assert cmd[0] == find_rvc_python(str(tmp_path))
    assert cmd[1:3] == ["-m", "comfyui_studio.rvc"]
    assert cmd[cmd.index("--port") + 1] == "7871"
    assert cmd[cmd.index("--host") + 1] == "127.0.0.1"
    assert cmd[cmd.index("--data-dir") + 1] == "D"
    assert cmd[-1] == "--foo"
    assert cwd == str(tmp_path)
    # пакет Studio виден питону RVC, чужой PYTHONPATH не затёрт
    assert env["PYTHONPATH"].split(os.pathsep) == [rvc_process.rvc_app_source_root(), "X"]
    assert "GRADIO_SERVER_PORT" not in env


def test_app_package_exists_where_the_launcher_points():
    entry = os.path.join(
        rvc_process.rvc_app_source_root(), "comfyui_studio", "rvc", "__main__.py"
    )
    assert os.path.isfile(entry)


def test_on_line_receives_output_and_survives_callback_errors(monkeypatch):
    _patch_launch(monkeypatch, "print('a'); print('b')")
    got = []

    def sink(line):
        got.append(line)
        if len(got) == 1:
            raise RuntimeError("окно закрыто")

    proc = RvcProcess(rvc_settings({"rvc": {"port": 17867}}), on_line=sink)
    proc.start()
    assert _wait_for(lambda: not proc.is_running())
    assert _wait_for(lambda: list(proc.tail) == ["a", "b"])  # чтение не оборвалось
    assert got == ["a"]  # после ошибки приёмник отключён


def test_manual_stop_is_not_logged_as_crash(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    _patch_launch(monkeypatch, "import time; time.sleep(60)")
    proc = RvcProcess(rvc_settings({"rvc": {"port": 17868}}))
    proc.start()
    proc.stop()
    assert _wait_for(lambda: any("остановлен пользователем" in r.getMessage() for r in caplog.records))
    assert not any(r.levelno >= logging.ERROR and "завершился с кодом" in r.getMessage()
                   for r in caplog.records)
