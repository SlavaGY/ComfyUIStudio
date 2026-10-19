"""Desktop LM Studio lifecycle and asynchronous native-window readiness."""

import time

import psutil
from PySide6.QtCore import QObject, QTimer, Signal

from ...errors import ProcessStartError
from ..core.lm_studio import LmStudioProcess, lm_studio_executable
from ..core.logging_setup import log


class LmStudioController(QObject):
    state_changed = Signal(str, str)
    ready = Signal(object)  # HWND is pointer-sized; Signal(int) truncates it on 64-bit Windows.
    releasing = Signal()
    TIMEOUT_SECONDS = 60

    def __init__(self, config, loc=None, parent=None, process_factory=LmStudioProcess):
        super().__init__(parent)
        self.config = config
        self.loc = loc
        self._factory = process_factory
        self._process = None
        self.state = "stopped"
        self._deadline = 0
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._check)

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def _set_state(self, state, message=""):
        self.state = state
        self.state_changed.emit(state, message)

    def start(self):
        if self._process is not None:
            if self.state == "running":
                self.ready.emit(self._process.hwnd)
            return
        process = self._factory(lm_studio_executable(self.config.cfg))
        self._process = process
        try:
            process.start()
        except (ProcessStartError, OSError, psutil.Error) as error:
            self.fail(self._tr("Не удалось запустить LM Studio: {}").format(self._tr(str(error))))
            return
        self._deadline = time.monotonic() + self.TIMEOUT_SECONDS
        self._set_state("starting", self._tr("Запуск LM Studio, ожидание окна..."))
        self._timer.start()
        self._check()

    def _check(self):
        if self._process is None:
            return
        try:
            hwnd = self._process.poll_window()
        except (OSError, psutil.Error) as error:
            self.fail(self._tr("Не удалось открыть окно LM Studio: {}").format(error))
            return
        if self.state == "running":
            if hwnd is None:
                self.fail(self._tr("Окно LM Studio закрыто."))
        elif hwnd is not None:
            self._set_state("running", self._tr("LM Studio запущен"))
            self.ready.emit(hwnd)
        elif time.monotonic() >= self._deadline:
            self.fail(self._tr("Не удалось дождаться окна LM Studio."))

    def stop(self):
        self._timer.stop()
        self.releasing.emit()  # Release the foreign QWindow before its native HWND dies.
        if self._process is not None:
            try:
                self._process.stop()
            except (ProcessStartError, OSError, psutil.Error) as error:
                log.error("Failed to stop LM Studio: %s", error)
                self._set_state("failed_active", self._tr("Не удалось остановить LM Studio."))
                return False
        self._process = None
        self._set_state("stopped")
        return True

    def fail(self, message):
        if self.stop():
            self._set_state("failed", message)
