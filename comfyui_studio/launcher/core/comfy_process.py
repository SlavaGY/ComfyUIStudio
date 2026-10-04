"""
Управление процессом ComfyUI.

Вынесено из comfyui_launcher.py (этап 1 дорожной карты): ComfyProcess,
ProcessLogBridge, _LogReaderThread (жизненный цикл процесса ComfyUI и
потоковое чтение его stdout).

R3: общая часть (is_running/exit_code/stop с taskkill) переехала в
ManagedProcess (core/managed_process.py); запуск внешних приложений
комплекта (ExternalApp, launch_external_app, resolve_external_launch) —
в core/external_apps.py.
"""

import os
import threading
import subprocess

from PySide6.QtCore import Signal, QObject

from .constants import COMFY_LOG_PATH
from .logging_setup import log
from .managed_process import ManagedProcess


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




class ComfyProcess(ManagedProcess):
    """Запускает ComfyUI, читает его stdout/stderr в фоне и умеет
    корректно убить всё дерево процессов (stop() — из ManagedProcess)."""

    label = "ComfyUI"

    def __init__(self, root_path, launch_script_abs, bridge: ProcessLogBridge, env_overrides=None):
        super().__init__()
        self.root_path = root_path
        self.launch_script_abs = launch_script_abs
        self.bridge = bridge
        self._reader = None
        # НОВОЕ (этап 4 дорожной карты, "Единое дерево настроек" ->
        # ComfyUI -> Environment): переменные окружения, заданные
        # пользователем в ui/settings/comfyui_page.py (cfg["env_vars"]) --
        # накладываются ПОВЕРХ os.environ (см. start() ниже), не заменяют
        # его целиком, иначе пропало бы PATH и прочее необходимое для
        # самого запуска python_embeded.
        self.env_overrides = dict(env_overrides) if env_overrides else {}

    def start(self):
        env = os.environ.copy()
        if self.env_overrides:
            env.update(self.env_overrides)
            log.info(
                "Запуск ComfyUI с доп. переменными окружения: %s",
                ", ".join(sorted(self.env_overrides)),
            )
        log.info("Запуск ComfyUI: %s (cwd=%s)", self.launch_script_abs, self.root_path)
        self._spawn(
            ["cmd.exe", "/c", self.launch_script_abs],
            cwd=self.root_path,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self._reader = _LogReaderThread(self.proc.stdout, self.bridge, COMFY_LOG_PATH)
        self._reader.start()
        return self.proc
