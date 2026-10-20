"""Local web app lifecycle. Probes run off the GUI thread."""

import time

from PySide6.QtCore import QObject, QTimer, Signal

from ...errors import ProcessStartError
from ..core.background import BackgroundTask
from ..core.lm_studio_app_process import LmStudioAppProcess, is_app_ready


class LmStudioAppController(QObject):
    state_changed = Signal(str, str)
    ready = Signal(int)
    releasing = Signal()

    def __init__(self, config, loc=None, parent=None, process_factory=LmStudioAppProcess):
        super().__init__(parent)
        self.config, self.loc = config, loc
        self._factory = process_factory
        self._process = None
        self.state = "stopped"
        self.port = None
        self._deadline = 0
        self._task = BackgroundTask(self)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._check)

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def _state(self, state, message=""):
        self.state = state
        self.state_changed.emit(state, message)

    def start(self):
        if self._process is not None and not self._process.is_running():
            self._timer.stop()
            self._task.cancel()
            self._process = None
        if self._process is not None:
            if self.state == "running":
                self.ready.emit(self.port)
            elif self.state == "failed_active":
                self._deadline = time.monotonic() + 60
                self._state("starting", self._tr("Запуск интерфейса LM Studio…"))
                self._timer.start()
                self._check()
            return
        try:
            process = self._factory(self.config.cfg)
            process.start()
        except (ProcessStartError, OSError) as error:
            self._state("failed", self._tr("Не удалось запустить LM Studio: {}").format(error))
            return
        self._process = process
        self.port = process.port
        self._deadline = time.monotonic() + 60
        self._state("starting", self._tr("Запуск интерфейса LM Studio…"))
        self._timer.start()
        self._check()

    def _check(self):
        if self._process is None:
            return
        if not self._process.is_running():
            if self.state == "failed_active":
                self._timer.stop()
                self._process = None
                self._state("stopped")
            else:
                self._fail(self._tr("Приложение LM Studio неожиданно завершилось. Подробности — в журнале Studio."))
        elif self.state == "starting":
            self._task.run(lambda: is_app_ready(self.port), self._probed)

    def _probed(self, ready, error):
        if self.state != "starting":
            return
        if ready and error is None:
            self._state("running", self._tr("LM Studio запущен"))
            self.ready.emit(self.port)
        elif time.monotonic() >= self._deadline:
            self._fail(self._tr("Не удалось дождаться интерфейса LM Studio."))

    def _fail(self, message):
        if self.stop():
            self._state("failed", message)

    def stop(self):
        self._timer.stop()
        self._task.cancel()
        self.releasing.emit()
        if self._process is not None:
            self._process.stop()
            if self._process.is_running():
                self._state("failed_active", self._tr("Не удалось остановить LM Studio."))
                self._timer.start()
                return False
        self._process = None
        self._state("stopped")
        return True
