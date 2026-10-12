"""
Контроллер запуска Ultimate RVC (этап RVC-1).

Аналог LaunchController для ComfyUI->Imagine (launch_controller.py), но
проще: у RVC нет цепочки «сначала сервер, потом надстройка» -- один
процесс (core/rvc_process.py), один watcher готовности. Независим от
ComfyUI: запускается и останавливается сам по себе.

Владеет процессом и watcher'ом; с видом (страница настроек RVC, окно)
общается сигналами:

    state_changed(state, message)  -- state: stopped|starting|running|failed
    ready(port)                    -- сервер отвечает, можно открывать браузер

Состояние `running` держится, пока жив процесс: «сторож» раз в 2 секунды
проверяет is_running() и, если RVC упал уже ПОСЛЕ запуска (нехватка VRAM,
исключение), переводит состояние в failed -- иначе кнопки остались бы
«Остановить/Открыть» при мёртвом сервере.

Только GUI-поток. Для тестов подменяются watcher и фабрика процесса.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QObject, QTimer, Signal

from ...errors import ProcessStartError
from ..core.background import BackgroundTask
from ..core.constants import APP_DIR, RVC_LOG_PATH
from ..core.logging_setup import log
from ..core.rvc_process import (
    MODE_GRADIO,
    RvcProcess,
    is_rvc_available,
    port_conflict,
    rvc_settings,
    validate_rvc_root,
)

STOPPED = "stopped"
STARTING = "starting"
RUNNING = "running"
FAILED = "failed"


class RvcLaunchWatcher(QObject):
    """Опрос готовности Gradio-сервера RVC. Таймаут больше, чем у Imagine
    (60 с): первый запуск RVC тянет torch/CUDA и ленивые импорты --
    на холодном диске это заметно дольше минуты."""

    ready = Signal()
    failed = Signal(str)
    progress = Signal(str)

    TIMEOUT_SECONDS = 180

    def __init__(self, loc=None, parent=None, probe=is_rvc_available, log_path=RVC_LOG_PATH):
        super().__init__(parent)
        self.loc = loc
        self._probe_fn = probe
        self._log_path = log_path
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._check)
        self._task = BackgroundTask(self)
        self._port = None
        self._process = None
        self._elapsed = 0

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def start(self, port, process):
        self._task.cancel()
        self._port = port
        self._process = process
        self._elapsed = 0
        self.progress.emit(self._tr("Запуск Ultimate RVC, ожидание сервера..."))
        self._timer.start()

    def stop(self):
        self._timer.stop()
        self._task.cancel()

    def _check(self):
        if self._process is not None and not self._process.is_running():
            self.stop()
            code = self._process.exit_code()
            log.error("Процесс Ultimate RVC завершился раньше времени, код выхода: %s", code)
            self.failed.emit(
                self._tr(
                    "Процесс Ultimate RVC неожиданно завершился (код выхода: {}). "
                    "Вывод процесса — в файле: {}"
                ).format(code, self._log_path)
            )
            return
        port = self._port
        self._task.run(lambda: self._probe_fn(port), self._on_probe_done)

    def _on_probe_done(self, available, error):
        if error is not None:
            log.warning("Ошибка проверки готовности Ultimate RVC: %s", error)
            available = False
        if available:
            self.stop()
            self.ready.emit()
            return
        self._elapsed += 1
        if self._elapsed >= self.TIMEOUT_SECONDS:
            self.stop()
            log.error("Таймаут ожидания Ultimate RVC (%s сек)", self.TIMEOUT_SECONDS)
            self.failed.emit(
                self._tr("Ultimate RVC не поднялся за {} секунд. Вывод процесса — в файле: {}").format(
                    self.TIMEOUT_SECONDS, self._log_path
                )
            )
            return
        self.progress.emit(
            self._tr("Запуск Ultimate RVC, ожидание сервера... ({}с)").format(self._elapsed)
        )


class RvcController(QObject):
    state_changed = Signal(str, str)
    ready = Signal(int)
    # Строки вывода процесса RVC -- для живого лога на главном экране.
    # Эмитится из потока-читателя; Qt доставит в GUI-поток сам (queued).
    log_line = Signal(str)
    # Сообщение пользователю, НЕ меняющее состояние (например, отказ запустить
    # второй режим, пока работает первый). Состояние при этом остаётся прежним:
    # старый процесс жив, и полоса «запущен» с кнопкой «Остановить» должна
    # остаться на месте.
    notice = Signal(str)

    ALIVE_CHECK_MS = 2000

    def __init__(
        self,
        config,
        loc=None,
        parent=None,
        watcher=None,
        process_factory=None,
        log_path=RVC_LOG_PATH,
        data_dir=None,
    ):
        """config -- ConfigStore (нужен только `.cfg`). watcher/
        process_factory -- подмена в тестах. data_dir -- где приложение
        Studio для RVC держит свои загрузки (по умолчанию APP_DIR/rvc)."""
        super().__init__(parent)
        self._config = config
        self.loc = loc
        self._log_path = log_path
        self._factory = process_factory or RvcProcess
        self._data_dir = data_dir or os.path.join(APP_DIR, "rvc")
        self._process = None
        self._port = None
        self._kind = None
        self._state = STOPPED

        self.watcher = watcher or RvcLaunchWatcher(loc=loc, parent=self, log_path=log_path)
        self.watcher.ready.connect(self._on_ready)
        self.watcher.failed.connect(self._on_failed)
        self.watcher.progress.connect(self._on_progress)

        self._alive_timer = QTimer(self)
        self._alive_timer.setInterval(self.ALIVE_CHECK_MS)
        self._alive_timer.timeout.connect(self._check_alive)

    # -- состояние -------------------------------------------------------

    @property
    def state(self):
        return self._state

    @property
    def port(self):
        return self._port

    @property
    def kind(self):
        """'app' (приложение Studio) или 'gradio' (оригинал) -- что сейчас
        запущено/запускается; None, если ничего."""
        return self._kind if self._process is not None else None

    def is_active(self):
        """Процесс RVC запущен (стартует или уже работает)."""
        return self._process is not None and self._process.is_running()

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def _set_state(self, state, message=""):
        self._state = state
        self.state_changed.emit(state, message)

    # -- запуск / остановка ------------------------------------------------

    def start(self, kind=MODE_GRADIO):
        """kind -- 'app' (собственное приложение Studio) или 'gradio'."""
        if self.is_active():
            if kind != self._kind:
                # Порт один на оба режима: второй процесс рядом не поднять.
                # Переводить контроллер в FAILED здесь нельзя: процесс-то
                # работает, и окно убрало бы полосу с кнопкой «Остановить» --
                # останавливать было бы нечем.
                self.notice.emit(
                    self._tr(
                        "Ultimate RVC уже запущен в другом режиме. "
                        "Остановите его (кнопка «Остановить» выше) и запустите заново."
                    )
                )
                return
            # Повторное «Запустить» не плодит второй процесс: если уже
            # работает -- просто просим окно показать браузер.
            if self._state == RUNNING:
                self.ready.emit(self._port)
            return

        cfg = self._config.cfg
        settings = rvc_settings(cfg)

        ok, message = validate_rvc_root(settings["root_path"], kind)
        if not ok:
            self._set_state(FAILED, self._tr(message))
            return

        conflict = port_conflict(cfg, settings["port"])
        if conflict:
            self._set_state(
                FAILED,
                self._tr("Порт {} уже назначен для {}. Выберите другой порт Ultimate RVC.").format(
                    settings["port"], conflict
                ),
            )
            return

        process = self._factory(
            settings,
            log_path=self._log_path,
            mode=kind,
            data_dir=self._data_dir,
            on_line=self.log_line.emit,
        )
        try:
            process.start()
        except ProcessStartError as e:
            log.error("Не удалось запустить Ultimate RVC: %s", e)
            self._set_state(FAILED, self._tr("Не удалось запустить Ultimate RVC: {}").format(e))
            return

        self._process = process
        self._port = settings["port"]
        self._kind = kind
        self._set_state(STARTING, self._tr("Запуск Ultimate RVC, ожидание сервера..."))
        self.watcher.start(self._port, process)

    def stop(self):
        self.watcher.stop()
        self._alive_timer.stop()
        if self._process is not None:
            self._process.stop()
            self._process = None
        self._set_state(STOPPED, "")

    # -- реакции watcher'а ----------------------------------------------------

    def _on_progress(self, text):
        if self._state == STARTING:
            self.state_changed.emit(STARTING, text)

    def _on_ready(self):
        log.info("Сервер Ultimate RVC поднялся (порт %s)", self._port)
        self._set_state(RUNNING, self._tr("Ultimate RVC работает на порту {}").format(self._port))
        self._alive_timer.start()
        self.ready.emit(self._port)

    def _on_failed(self, message):
        if self._process is not None:
            self._process.stop()
            self._process = None
        self._alive_timer.stop()
        self._set_state(FAILED, message)

    def _check_alive(self):
        if self._state != RUNNING or self._process is None:
            return
        if self._process.is_running():
            return
        code = self._process.exit_code()
        self._process = None
        self._alive_timer.stop()
        log.error("Ultimate RVC завершился после запуска, код выхода: %s", code)
        self._set_state(
            FAILED,
            self._tr(
                "Ultimate RVC неожиданно завершился (код выхода: {}). "
                "Вывод процесса — в файле: {}"
            ).format(code, self._log_path),
        )
