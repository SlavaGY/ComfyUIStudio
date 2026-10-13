"""
Общая основа для подпроцессов, которыми владеет лаунчер (этап R3).

До R3 три класса — ComfyProcess, ImagineProcess и RemoteProcess —
содержали почти дословные копии одного и того же: Popen с
CREATE_NO_WINDOW, is_running(), exit_code() и stop() с `taskkill /F /T`
(по ~25 строк на класс), а ImagineProcess и RemoteProcess ещё и
одинаковый _log_exit() (чтение захваченного stdout и запись хвоста в
лог при ненулевом коде выхода). Теперь это живёт здесь один раз.

ВАЖНО: модуль намеренно НЕ импортирует Qt. ImagineProcess используется
процессом Remote (remote/app_launcher.py), куда Qt затаскивать нельзя —
поэтому Qt-зависимый ComfyProcess остаётся в comfy_process.py (рядом с
ProcessLogBridge/_LogReaderThread) и только наследуется отсюда.

Публичный интерфейс потомков не менялся: конструкторы, start(),
is_running(), exit_code(), stop(), атрибуты proc/host/port/...
(см. docs/history/baseline_R0.md §3 и tests/launcher/test_main_window_flow.py).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import threading

from .logging_setup import log

# Сколько последних символов вывода подпроцесса писать в лог при
# аварийном завершении: хватает на traceback обычной длины и не
# раздувает лог-файл при зацикленных ошибках запуска.
EXIT_TAIL_CHARS = 4000


def find_missing_modules(names):
    """Имена из `names`, которых нет в текущем окружении.

    importlib.util.find_spec() вызывается в ТОМ ЖЕ интерпретаторе/exe,
    что запустит подпроцесс (при frozen=True sys.executable — сам
    собранный ComfyUIStudio.exe, он перезапускает себя со скрытым CLI-
    флагом), поэтому результат надёжно предсказывает, что будет
    доступно дочернему процессу. Используют imagine_process.py и
    remote_process.py, чтобы не запускать процесс, который заведомо
    упадёт на ImportError с голым «кодом выхода 1».
    """
    return [m for m in names if not _module_exists(m)]


def _module_exists(name):
    """find_spec() для точечного имени (`google.auth`) сначала импортирует
    родителя и бросает ModuleNotFoundError, если родителя нет вовсе, --
    для проверки «есть ли модуль» это значит «нет», а не падение."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _taskkill_tree(pid, label):
    """`taskkill /F /T /PID` — убивает процесс и всё его поддерево
    (промежуточный cmd.exe, python.exe под ним, воркеры). Ошибки только
    в лог. Единственное место в проекте, где собирается эта команда
    (R3b: раньше копии были в comfy_launcher.py, process_by_port.py и
    трёх классах процессов)."""
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        log.exception("Не удалось выполнить taskkill для PID %s (%s)", pid, label)


def terminate_pid_tree(pid, label):
    """То же по одному PID, когда объекта Popen нет (процесс запущен
    другим ОС-процессом, PID найден по порту — см. remote/
    process_by_port.py). Windows: taskkill по дереву. Иначе: terminate()
    самого процесса через psutil (импортируется лениво — модуль не должен
    требовать psutil, пока PID-вариант не нужен). Ошибки только в лог."""
    if sys.platform == "win32":
        _taskkill_tree(pid, label)
        return
    try:
        import psutil

        psutil.Process(pid).terminate()
    except Exception:
        log.exception("Не удалось остановить PID %s (%s)", pid, label)


def terminate_process_tree(proc, label, wait_timeout=5):
    """Останавливает процесс вместе со всем деревом потомков.

    Windows: `taskkill /F /T /PID` — это убивает и промежуточный
    cmd.exe, и python.exe под ним, и воркеры. Иначе: terminate() только
    самого процесса. Ошибки не пробрасываются — только в лог (остановка
    вызывается из closeEvent и откатов запуска, где исключение хуже, чем
    недобитый процесс). Возвращает True, если процесс завершился за
    `wait_timeout` секунд.
    """
    pid = proc.pid
    if sys.platform == "win32":
        _taskkill_tree(pid, label)
    else:
        try:
            proc.terminate()
        except Exception:
            log.exception("Не удалось остановить процесс PID %s (%s)", pid, label)
    try:
        proc.wait(timeout=wait_timeout)
        return True
    except Exception:
        return False


class ManagedProcess:
    """Один дочерний процесс, принадлежащий лаунчеру.

    Потомок задаёт `label` (для логов: «ComfyUI», «Imagine», «Remote»),
    в start() собирает команду и вызывает _spawn() либо
    _spawn_captured(), остальное (is_running/exit_code/stop) общее.
    """

    label = "process"

    def __init__(self):
        self.proc = None

    # -- запуск ----------------------------------------------------------

    def _spawn(self, cmd, **popen_kwargs):
        """Popen без консольного окна (Windows). Все остальные
        параметры — как у subprocess.Popen."""
        popen_kwargs.setdefault(
            "creationflags",
            subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        self.proc = subprocess.Popen(cmd, **popen_kwargs)
        return self.proc

    def _spawn_captured(self, cmd, cwd=None):
        """Popen с захватом stdout+stderr (текст, UTF-8) и фоновым
        потоком, который по завершении пишет код выхода и — при
        ненулевом — хвост вывода в лог (см. _watch_exit).

        Раньше вывод уходил в DEVNULL, и любая ошибка запуска сводилась
        к «код выхода: 1» без единой подсказки.
        """
        proc = self._spawn(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        # proc передаётся потоку явно: раньше поток читал self.proc сам,
        # и если stop() успевал обнулить его до старта потока, лог
        # завершения терялся.
        threading.Thread(target=self._watch_exit, args=(proc,), daemon=True).start()
        return proc

    def _watch_exit(self, proc):
        output = ""
        try:
            output = proc.stdout.read() if proc.stdout else ""
        except Exception:
            log.exception("Не удалось прочитать вывод процесса %s", self.label)
        finally:
            if proc.stdout:
                try:
                    proc.stdout.close()
                except Exception:
                    pass
        code = proc.wait()
        if code == 0:
            log.info("%s (PID %s) завершился, код выхода 0", self.label, proc.pid)
        else:
            tail = (
                output[-EXIT_TAIL_CHARS:]
                if output
                else "(процесс не вывел ничего в stdout/stderr)"
            )
            log.error(
                "%s (PID %s) завершился с кодом %s. Вывод процесса:\n%s",
                self.label, proc.pid, code, tail,
            )

    # -- состояние -------------------------------------------------------

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def exit_code(self):
        return self.proc.returncode if self.proc is not None else None

    # -- остановка -------------------------------------------------------

    def stop(self):
        """Останавливает процесс. Возвращает True, если его больше нет
        (или его и не было), False -- если он пережил и штатную остановку,
        и kill().

        Раньше результат terminate_process_tree() выбрасывался, а self.proc
        обнулялся безусловно: переживший таймаут процесс терял единственную
        ссылку (Popen), добить его было нечем -- «фантомный процесс».
        Теперь при неудаче штатной остановки делается kill(), а ссылка
        сбрасывается, только когда процесс действительно завершён; иначе
        is_running() остаётся True и повторный stop() может попробовать
        ещё раз."""
        proc = self.proc
        if proc is None:
            return True
        log.info("Остановка %s (PID %s)", self.label, proc.pid)
        if not terminate_process_tree(proc, self.label):
            log.warning(
                "%s (PID %s) не завершился за отведённое время -- kill()",
                self.label, proc.pid,
            )
            try:
                proc.kill()
            except OSError:
                log.exception("Не удалось выполнить kill() для %s (PID %s)", self.label, proc.pid)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            if proc.poll() is None:
                log.error(
                    "%s (PID %s) не удалось остановить; ссылка на процесс сохранена",
                    self.label, proc.pid,
                )
                return False
        self.proc = None
        return True
