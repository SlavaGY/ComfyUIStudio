"""
promptgen_process.py
Процесс llama-server: сборка команды и окружения, запуск с выводом в файл,
остановка вместе с деревом потомков, Job Object (Windows) и реестр
запущенных процессов для уборки «осиротевших» после аварии Imagine.

Вынесено из promptgen.py на этапе R10 плана рефакторинга без изменения
кода функций, кроме сужения ``except`` (см. docs/R10_APPLY.md).
"""

from __future__ import annotations

import datetime
import json
import os
import shlex
import socket
import subprocess
import tempfile
import time
from typing import Optional

from comfyui_studio.imagine.backend.promptgen_base import PromptGenError
from comfyui_studio.imagine.backend.promptgen_deps import shared_promptgen
from comfyui_studio.imagine.backend.promptgen_diag import _psutil, _read_log_tail, log

# ---------------------------------------------------------------------------
# Windows: Job Object с KILL_ON_JOB_CLOSE
# ---------------------------------------------------------------------------

_job_handle = None


def _attach_kill_on_close_job(proc: subprocess.Popen) -> None:
    """Best-effort: ассоциирует процесс с Job Object'ом, который убивает всех
    своих участников, когда закрывается последний дескриптор -- то есть
    когда умирает сам Imagine (даже аварийно). Любая ошибка здесь только
    логируется: это подстраховка, а не необходимое условие работы."""
    global _job_handle
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class BASIC_LIMIT(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class EXTENDED_LIMIT(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC_LIMIT),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

        if _job_handle is None:
            handle = kernel32.CreateJobObjectW(None, None)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            info = EXTENDED_LIMIT()
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(
                handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info), ctypes.sizeof(info),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            _job_handle = handle

        if not kernel32.AssignProcessToJobObject(_job_handle, int(proc._handle)):
            raise ctypes.WinError(ctypes.get_last_error())
    except Exception:
        log.warning("Не удалось привязать llama-server к Job Object", exc_info=True)


# ---------------------------------------------------------------------------
# Процесс llama-server
# ---------------------------------------------------------------------------

# 0xC0000135 (STATUS_DLL_NOT_FOUND) -- Windows не нашла нужную DLL.
_STATUS_DLL_NOT_FOUND = 0xC0000135


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _split_extra_args(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    try:
        parts = shlex.split(text, posix=(os.name != "nt"))
    except ValueError as exc:
        raise PromptGenError(
            f"Не удалось разобрать «Доп. аргументы» llama-server: {exc}", 400
        ) from exc
    # при posix=False shlex оставляет кавычки внутри токенов
    cleaned = []
    for part in parts:
        if len(part) >= 2 and part[0] == part[-1] and part[0] in "\"'":
            part = part[1:-1]
        cleaned.append(part)
    return cleaned


def build_command(cfg: dict, port: int) -> list[str]:
    exe = shared_promptgen.find_server_exe(cfg["llama_dir"].strip())
    if exe is None:  # validate() уже проверил, но состояние диска могло измениться
        raise PromptGenError("llama-server не найден в папке llama.cpp.", 400)
    ctx = cfg.get("ctx_size") or 0
    cmd = [
        exe,
        "-m", cfg["model_path"].strip(),
        "--host", "127.0.0.1",
        "--port", str(port),
        # одна «ячейка» обработки: не выделять память под несколько
        # параллельных запросов, нужный ровно один
        "-np", "1",
        # шаблон чата из самой модели + chat_template_kwargs в запросе
        "--jinja",
    ]
    if ctx > 0:
        cmd += ["-c", str(ctx)]
    mmproj = cfg.get("mmproj_path", "").strip()
    if mmproj:
        cmd += ["--mmproj", mmproj]
    gpu_layers = cfg.get("gpu_layers", "").strip()
    if gpu_layers:
        cmd += ["-ngl", gpu_layers]
    cmd += _split_extra_args(cfg.get("extra_args", ""))
    return cmd


def build_env(cfg: dict) -> dict:
    env = os.environ.copy()
    dirs = shared_promptgen.dll_dirs(cfg)
    if dirs:
        env["PATH"] = os.pathsep.join(dirs + [env.get("PATH", "")])
    return env


def _llama_log_path(job_id: str) -> str:
    """Файл для вывода llama-server этого запуска; старые файлы удаляются
    (остаются последние LLAMA_LOGS_KEEP)."""
    directory = shared_promptgen.llama_log_dir()
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        directory = tempfile.gettempdir()
    name = f"{datetime.datetime.now():%Y%m%d-%H%M%S}-{job_id}.log"
    path = os.path.join(directory, name)
    try:
        old = sorted(
            (os.path.join(directory, f) for f in os.listdir(directory) if f.endswith(".log")),
            key=os.path.getmtime,
        )
        for stale in old[: max(0, len(old) - shared_promptgen.LLAMA_LOGS_KEEP + 1)]:
            try:
                os.remove(stale)
            except OSError:
                pass
    except OSError:
        pass
    return path


def _spawn(cmd: list[str], cwd: str, env: dict, log_path: str):
    """Запускает llama-server, перенаправляя ВЕСЬ его вывод в файл (а не в
    pipe): нет потока-читателя, который мог бы зависнуть на закрытии pipe,
    если у процесса остался живой потомок, и вывод сохраняется целиком для
    разбора. Возвращает (Popen, открытый файл лога)."""
    log_fh = open(log_path, "ab")
    log_fh.write(
        f"# {datetime.datetime.now().isoformat(timespec='seconds')}\n# cmd: {cmd}\n\n".encode("utf-8")
    )
    log_fh.flush()
    kwargs = dict(
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.Popen(cmd, **kwargs)
    except OSError as exc:
        log_fh.close()
        raise PromptGenError(f"Не удалось запустить llama-server: {exc}", 500) from exc
    _attach_kill_on_close_job(proc)
    return proc, log_fh


def _kill_tree(proc: Optional[subprocess.Popen]) -> str:
    """Убивает процесс и ВСЕХ его потомков; ждёт завершения (с таймаутом) и
    возвращает краткий отчёт для лога. Безопасно вызывать повторно и из
    нескольких потоков (cancel() и завершающий код задачи)."""
    if proc is None:
        return "нет процесса"
    pid = proc.pid
    psutil = _psutil()
    report = ""
    if psutil is not None:
        try:
            try:
                parent = psutil.Process(pid)
                victims = parent.children(recursive=True) + [parent]
            except psutil.NoSuchProcess:
                victims = []
            for victim in victims:
                try:
                    victim.terminate()
                except psutil.NoSuchProcess:
                    pass
            _gone, alive = psutil.wait_procs(victims, timeout=5)
            for victim in alive:
                try:
                    victim.kill()
                except psutil.NoSuchProcess:
                    pass
            if alive:
                _gone2, alive = psutil.wait_procs(alive, timeout=5)
            report = f"убито процессов: {len(victims)}"
            if alive:
                report += f", НЕ УДАЛОСЬ убить: {[p.pid for p in alive]}"
        except Exception as exc:
            report = f"ошибка psutil: {exc}"
    else:
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except (OSError, subprocess.SubprocessError) as exc:  # нет taskkill / таймаут
                report = f"ошибка taskkill: {exc}"
        elif proc.poll() is None:
            proc.terminate()
        report = report or "без psutil"
    try:
        proc.wait(timeout=5)  # забираем код выхода, не оставляя зомби
        if proc.poll() is None:
            proc.kill()
    except subprocess.TimeoutExpired:
        proc.kill()
    except OSError:
        pass
    return report


def _stop_process(proc: Optional[subprocess.Popen], log_fh=None) -> Optional[str]:
    """Останавливает llama-server и закрывает файл его лога. Возвращает
    отчёт для лога (None, если останавливать было нечего)."""
    if proc is None:
        return None
    t0 = time.monotonic()
    try:
        report = _kill_tree(proc)
    except Exception as exc:
        log.exception("Не удалось остановить llama-server")
        report = f"ошибка: {exc}"
    finally:
        if log_fh is not None:
            try:
                log_fh.close()
            except OSError:
                pass
    return f"pid {proc.pid}: {report}, {time.monotonic() - t0:.1f} с"


def _exit_message(proc: subprocess.Popen, log_path: str, phase: str = "при запуске") -> str:
    code = proc.returncode
    lines = _read_log_tail(log_path, 15)
    msg = f"llama-server завершился {phase} (код {code})."
    if code is not None and (code & 0xFFFFFFFF) == _STATUS_DLL_NOT_FOUND:
        msg += (
            " Windows не нашла нужную DLL (0xC0000135): для llama-server из "
            "LM Studio нужна папка vendor с CUDA-библиотеками — укажите её в "
            "«Доп. папка с DLL» в настройках Studio, либо используйте обычный "
            "релиз llama.cpp."
        )
    if lines:
        msg += "\n" + lines
    return msg


# ---------------------------------------------------------------------------
# Реестр запущенных процессов (уборка «осиротевших» после аварии Imagine)
# ---------------------------------------------------------------------------

def _registry_path() -> str:
    return os.path.join(shared_promptgen.SHARED_DIR, "promptgen_running.json")


def _registry_read() -> list[dict]:
    try:
        with open(_registry_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return [e for e in data if isinstance(e, dict) and "pid" in e] if isinstance(data, list) else []
    except (OSError, ValueError):  # нет файла / не читается / битый JSON (в т. ч. UnicodeDecodeError)
        return []


def _registry_write(entries: list[dict]) -> None:
    try:
        os.makedirs(shared_promptgen.SHARED_DIR, exist_ok=True)
        with open(_registry_path(), "w", encoding="utf-8") as f:
            json.dump(entries, f)
    except OSError:
        pass


def _register_process(proc: subprocess.Popen, job_id: str) -> None:
    psutil = _psutil()
    if psutil is None:
        return
    try:
        created = psutil.Process(proc.pid).create_time()
    except (psutil.Error, OSError):
        return
    entries = [e for e in _registry_read() if e.get("pid") != proc.pid]
    entries.append({"pid": proc.pid, "create_time": created, "job": job_id})
    _registry_write(entries)


def _unregister_process(pid: int) -> None:
    entries = _registry_read()
    remaining = [e for e in entries if e.get("pid") != pid]
    if len(remaining) != len(entries):
        _registry_write(remaining)


def sweep_stale() -> int:
    """Добивает llama-server'ы, оставшиеся от прошлых запусков Imagine
    (аварийное завершение, где не сработали ни хуки, ни Job Object).
    Убивает ТОЛЬКО процессы из нашего реестра, у которых совпали pid, время
    создания и «llama» в имени -- переиспользованный ОС pid чужого процесса
    не тронет. Возвращает число убитых."""
    psutil = _psutil()
    if psutil is None or shared_promptgen is None:
        return 0
    entries = _registry_read()
    if not entries:
        return 0
    killed = 0
    for entry in entries:
        try:
            proc = psutil.Process(int(entry["pid"]))
            same = abs(proc.create_time() - float(entry.get("create_time", 0))) < 2.0
            if same and "llama" in proc.name().lower():
                victims = proc.children(recursive=True) + [proc]
                for victim in victims:
                    try:
                        victim.kill()
                    except psutil.NoSuchProcess:
                        pass
                psutil.wait_procs(victims, timeout=5)
                killed += 1
                log.warning(
                    "Убит «осиротевший» llama-server pid=%s (задача %s) — остался с прошлого запуска",
                    entry["pid"], entry.get("job"),
                )
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError, KeyError):
            pass
        except Exception:
            log.exception("Ошибка при уборке процесса из реестра: %s", entry)
    _registry_write([])
    return killed
