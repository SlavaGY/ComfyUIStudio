"""
Управление процессом ComfyUI и запуском внешних приложений комплекта.

Вынесено из comfyui_launcher.py (этап 1 дорожной карты): ComfyProcess,
ProcessLogBridge, _LogReaderThread (жизненный цикл процесса ComfyUI и
потоковое чтение его stdout) и ExternalApp/launch_external_app/
resolve_external_launch (запуск Prompt Builder и PromptVault как
отдельных процессов, когда лаунчер не в монолитном режиме).
"""

import os
import sys
import threading
import subprocess

from PySide6.QtCore import Signal, QObject

from .constants import COMFY_LOG_PATH, PROJECT_ROOT, TOOLS_DIR
from .logging_setup import log


class ExternalApp:
    """Описание одного внешнего инструмента комплекта: где искать
    собранный exe (фиксированная подпапка в tools/<subdir>/dist/...,
    туда его кладут build_windows.bat/build.bat самих инструментов —
    это НЕ затронуто переносом исходников под comfyui_studio/, см.
    ниже) и как запустить его из исходников как пакет, если лаунчер
    сам не заморожен PyInstaller-ом."""

    def __init__(self, label, subdir, exe_name, module_name):
        self.label = label
        self.subdir = subdir  # подпапка внутри tools/ — ТОЛЬКО для поиска dist/<exe_name>.exe
        self.exe_name = exe_name
        # Пакет для запуска из исходников: `python -m <module_name>` —
        # начиная с этапа 2 дорожной карты (перенос prompt_builder/
        # promptvault под общее пространство имён comfyui_studio/) это
        # comfyui_studio.prompt_builder / comfyui_studio.promptvault, а
        # НЕ tools/<subdir> — там (см. свойство root ниже) с этапа 2
        # исходников инструмента больше нет, только служебные файлы
        # сборки (build.spec/README/requirements.txt) и, если собран,
        # dist/ с exe.
        self.module_name = module_name

    @property
    def root(self):
        """Папка со СБОРОЧНЫМИ артефактами инструмента (tools/<subdir>/,
        там же dist/<exe_name>/<exe_name>.exe после сборки) — НЕ папка
        с исходниками; те теперь под comfyui_studio/<subdir>/, см.
        source_entry_abs."""
        return os.path.join(TOOLS_DIR, self.subdir)

    @property
    def source_entry_abs(self):
        """Путь к __main__.py пакета в исходниках (comfyui_studio/<subdir>/
        __main__.py), которым можно проверить, что "python -m
        <module_name>" вообще имеет смысл пробовать — путь считается
        от PROJECT_ROOT (корень проекта), а не от app.root (tools/<subdir>/),
        т.к. это разные папки после переноса под comfyui_studio/ (этап 2
        дорожной карты)."""
        return os.path.join(PROJECT_ROOT, "comfyui_studio", self.subdir, "__main__.py")


EXTERNAL_APPS = [
    ExternalApp(
        label="Character / Prompt Builder Config Editor",
        subdir="prompt_builder",
        exe_name="PromptConfigEditor",
        module_name="comfyui_studio.prompt_builder",
    ),
    ExternalApp(
        label="PromptVault",
        subdir="promptvault",
        exe_name="PromptVault",
        module_name="comfyui_studio.promptvault",
    ),
]


# --------------------------------------------------------------------------
# Монолитный режим (ComfyUIStudio): когда лаунчер запущен как часть общего
# однопроцессного приложения (см. корневой main.py), остальные инструменты
# комплекта открываются как окна ЭТОГО ЖЕ процесса, а не отдельные
# подпроцессы -- корневой main.py регистрирует здесь фабрику окна для
# каждого app.subdir через register_in_process_app() ДО того, как
# показывается это окно лаунчера. Если фабрика для данного subdir не
# зарегистрирована (лаунчер запущен сам по себе, как раньше, через
# `python comfyui_launcher.py` или отдельно собранный exe) -- поведение не
# меняется: launch_external_app() ниже по-прежнему пробует отдельный
# процесс/exe.
# --------------------------------------------------------------------------


def resolve_external_launch(app: "ExternalApp"):
    """Определяет, как запустить внешнее приложение комплекта в отдельном
    процессе:

      1. Если рядом лежит собранный PyInstaller-exe
         (tools/<subdir>/dist/<exe_name>/<exe_name>.exe) — запускаем его
         напрямую. Работает независимо от того, запущен ли сам лаунчер из
         исходников или тоже собран в exe.
      2. Иначе, если лаунчер запущен из исходников (не заморожен), пробуем
         запустить пакет тем же интерпретатором Python: `python -m
         <module_name>` (comfyui_studio.prompt_builder /
         comfyui_studio.promptvault) с рабочей папкой PROJECT_ROOT — так
         же, как их запускает монолитный main.py, только отдельным
         процессом вместо окна в этом же. До этапа 2 дорожной карты
         (перенос исходников под comfyui_studio/) здесь был путь вида
         `tools/<subdir>/main.py` — устарел вместе с самой структурой.
      3. Иначе — понятная ошибка вместо тихого "ничего не произошло".

    Возвращает (cmd: list[str], cwd: str, error: None) либо
    (None, None, error: str).
    """
    app_root = app.root
    exe_path = os.path.join(app_root, "dist", app.exe_name, app.exe_name + ".exe")
    if os.path.isfile(exe_path):
        return [exe_path], os.path.dirname(exe_path), None

    if getattr(sys, "frozen", False):
        return None, None, (
            f"Не найден собранный {app.exe_name}.exe ({exe_path}).\n"
            "Соберите приложение сборочным скриптом в его папке — запуск "
            "исходников из собранного лаунчера невозможен."
        )

    if not os.path.isfile(app.source_entry_abs):
        return None, None, (
            f"Не найден пакет {app.module_name} ({app.source_entry_abs}) — "
            "похоже, исходники комплекта повреждены или неполные."
        )

    return [sys.executable, "-m", app.module_name], PROJECT_ROOT, None



def launch_external_app(app: "ExternalApp"):
    """Запускает внешнее приложение комплекта как независимый,
    самостоятельный процесс (не дочерний в смысле логики приложения —
    лаунчер за ним не следит и не останавливает при своём закрытии).
    Возвращает (ok: bool, message: str)."""
    cmd, cwd, error = resolve_external_launch(app)
    if error:
        return False, error

    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except OSError as e:
        log.exception("Не удалось запустить %s", app.label)
        return False, f"Не удалось запустить {app.label}: {e}"

    log.info("Запущен %s (PID %s, cmd=%s, cwd=%s)", app.label, proc.pid, cmd, cwd)

    # Раньше лаунчер вообще не отслеживал, что стало с этим процессом
    # дальше — при жалобах "закрыл инструмент, а память не освободилась"
    # не было даже лога, чтобы проверить, действительно ли процесс
    # завершился. Здесь только логируем сам факт и время завершения —
    # ничего не останавливаем и не мониторим активно (см. докстринг выше).
    watcher = threading.Thread(
        target=_log_external_app_exit,
        args=(app.label, proc),
        daemon=True,
    )
    watcher.start()

    return True, ""



def _log_external_app_exit(label, proc):
    exit_code = proc.wait()
    log.info("%s (PID %s) завершился, код выхода %s", label, proc.pid, exit_code)


class ProcessLogBridge(QObject):
    """Мост из потока чтения stdout процесса в GUI-поток (Qt сам
    маршализует сигнал в основной поток, т.к. bridge создаётся там)."""

    line_received = Signal(str)
    # Отдельно от line_received: ComfyUI/tqdm перерисовывают прогресс-бар
    # через "\r" БЕЗ "\n" на каждый шаг, а readline() блокируется до
    # первого настоящего "\n" -- то есть весь бар может прийти одним
    # куском с кучей "\r" внутри. Здесь -- каждый такой кусок отдельно
    # (см. _LogReaderThread.run и ResourceMonitor.feed_log_line), чтобы
    # разобрать реальную скорость шага из строк вида
    # "74%|███████▍ | 26/35 [00:24<00:07, 1.21it/s]".
    progress_chunk_received = Signal(str)



class _LogReaderThread(threading.Thread):
    def __init__(self, stream, bridge: ProcessLogBridge, log_file_path):
        super().__init__(daemon=True)
        self.stream = stream
        self.bridge = bridge
        self.log_file_path = log_file_path

    def run(self):
        # ВАЖНО: читаем сырыми кусками (stream.read(N)), а НЕ построчно
        # (iter(readline, b"")) -- readline() блокируется, пока не
        # встретит настоящий "\n". Пока рядом печаталось что-то ещё со
        # своими "\n" (например периодические строки ComfyUI-Manager
        # "FETCH ComfyRegistry Data: N/164"), это давало нам частые
        # "проблески" и весь буфер с "\r"-тиками tqdm вовремя
        # вытеснялся наружу -- эффект был похож на то, что прогресс
        # обновляется вживую. Но как только рядом печатать перестаёт
        # что-либо ещё (например после того как Manager закончил свою
        # фоновую синхронизацию при старте), единственный настоящий "\n"
        # -- это конец самого прогресс-бара, и readline() просто ждёт
        # его, копя ВСЕ промежуточные "\r"-перерисовки во внутреннем
        # буфере -- они долетают до нас все разом только в момент,
        # когда бар уже закрылся (или рядом наконец что-то ещё
        # напечаталось). Со стороны выглядит как "прогресс не
        # обновляется до самого конца генерации".
        # read(N) на пайпе возвращает данные, как только они появились
        # (не ждёт заполнения N байт) -- а сам tqdm делает flush() после
        # каждой перерисовки, так что байты в пайпе действительно
        # появляются вживую, нам просто нужно их вовремя забирать.
        buf = b""
        try:
            with open(self.log_file_path, "w", encoding="utf-8", errors="ignore") as f:
                while True:
                    # read1(), а НЕ read() -- read() на BufferedReader
                    # может сделать НЕСКОЛЬКО системных чтений, пытаясь
                    # набрать полные 4096 байт, и в худшем случае снова
                    # подвиснет так же, как readline() ждал "\n".
                    # read1() гарантированно возвращает то, что уже
                    # пришло в пайп, максимум за одно системное чтение --
                    # именно то, что нужно для реального времени.
                    chunk = self.stream.read1(4096)
                    if not chunk:
                        break  # EOF -- процесс закрыл stdout
                    buf += chunk

                    while True:
                        idx_r = buf.find(b"\r")
                        idx_n = buf.find(b"\n")
                        if idx_r == -1 and idx_n == -1:
                            break
                        if idx_n != -1 and (idx_r == -1 or idx_n < idx_r):
                            idx, is_newline = idx_n, True
                            consumed = idx + 1
                        else:
                            idx = idx_r
                            consumed = idx + 1
                            # "\r\n" -- ОДНА граница обычной строки (не
                            # перерисовка бара) -- обязательно помечаем
                            # is_newline=True и здесь тоже, иначе такая
                            # строка уйдёт только в progress_chunk_received,
                            # а в файл лога/панель -- нет (именно так
                            # ломался лог: "\r\n" распознавался и склеивался
                            # правильно, но не как настоящий перевод строки).
                            if buf[idx + 1:idx + 2] == b"\n":
                                consumed += 1
                                is_newline = True
                            else:
                                is_newline = False

                        piece = buf[:idx]
                        buf = buf[consumed:]

                        text = piece.decode("utf-8", errors="ignore")
                        if text:
                            # КАЖДЫЙ кусок (включая промежуточные "\r"-
                            # перерисовки) -- для разбора ETA в реальном
                            # времени.
                            self.bridge.progress_chunk_received.emit(text)

                        if is_newline:
                            # В файл лога и в line_received (панель лога
                            # в UI), как и раньше, уходят только
                            # настоящие, полные строки -- не каждая
                            # промежуточная перерисовка бара.
                            f.write(text + "\n")
                            f.flush()
                            self.bridge.line_received.emit(text)

                # Если процесс закрыл stdout, а в буфере остался хвост
                # без завершающего "\n"/"\r" -- всё равно публикуем его
                # (иначе последняя строка перед закрытием терялась бы).
                if buf:
                    text = buf.decode("utf-8", errors="ignore")
                    if text:
                        self.bridge.progress_chunk_received.emit(text)
                    f.write(text + "\n")
                    f.flush()
                    self.bridge.line_received.emit(text)
        except Exception:
            log.exception("Ошибка чтения вывода процесса ComfyUI")




class ComfyProcess:
    """Запускает ComfyUI, читает его stdout/stderr в фоне и умеет
    корректно убить всё дерево процессов."""

    def __init__(self, root_path, launch_script_abs, bridge: ProcessLogBridge):
        self.root_path = root_path
        self.launch_script_abs = launch_script_abs
        self.bridge = bridge
        self.proc = None
        self._reader = None

    def start(self):
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        log.info("Запуск ComfyUI: %s (cwd=%s)", self.launch_script_abs, self.root_path)
        self.proc = subprocess.Popen(
            ["cmd.exe", "/c", self.launch_script_abs],
            cwd=self.root_path,
            creationflags=creationflags,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self._reader = _LogReaderThread(self.proc.stdout, self.bridge, COMFY_LOG_PATH)
        self._reader.start()
        return self.proc

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def exit_code(self):
        return self.proc.returncode if self.proc is not None else None

    def stop(self):
        if self.proc is None:
            return
        pid = self.proc.pid
        log.info("Остановка ComfyUI (PID %s)", pid)
        if sys.platform == "win32":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                log.exception("Не удалось выполнить taskkill для PID %s", pid)
        else:
            try:
                self.proc.terminate()
            except Exception:
                log.exception("Не удалось остановить процесс PID %s", pid)
        try:
            self.proc.wait(timeout=5)
        except Exception:
            pass
        self.proc = None


# --------------------------------------------------------------------------
# Ограниченная веб-страница: без внешних доменов и без новых окон
# --------------------------------------------------------------------------


