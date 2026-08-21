"""
Опрос готовности сервера ComfyUI после запуска процесса (таймаут,
статус для UI).

Вынесено из comfyui_launcher.py (этап 1 дорожной карты).
"""

from PySide6.QtCore import QObject, QTimer, Signal

from ...core.comfy_api import ComfyAPIClient
from ...core.comfy_process import ComfyProcess
from ...core.logging_setup import log


class LaunchWatcher(QObject):
    ready = Signal()
    failed = Signal(str)
    progress = Signal(str)

    TIMEOUT_SECONDS = 180

    def __init__(self, loc=None, parent=None):
        super().__init__(parent)
        self.loc = loc
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._check)
        self._port = None
        self._elapsed = 0
        self._process = None
        # Этап 6 дорожной карты: готовность сервера проверяется через
        # ComfyAPIClient.is_available(), а не напрямую через is_port_open.
        self._api = ComfyAPIClient()

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def start(self, port, process: ComfyProcess):
        self._port = port
        self._process = process
        self._elapsed = 0
        self.progress.emit(self._tr("Запуск ComfyUI, ожидание сервера..."))
        self._timer.start()

    def stop(self):
        self._timer.stop()

    def _check(self):
        if self._process is not None and not self._process.is_running():
            self.stop()
            code = self._process.exit_code()
            log.error("Процесс ComfyUI завершился раньше времени, код выхода: %s", code)
            self.failed.emit(
                self._tr(
                    "Процесс ComfyUI неожиданно завершился (код выхода: {}). "
                    "Подробности — в логе ниже."
                ).format(code)
            )
            return

        if self._api.is_available(port=self._port):
            self.stop()
            self.ready.emit()
            return

        self._elapsed += 1
        if self._elapsed >= self.TIMEOUT_SECONDS:
            self.stop()
            log.error("Таймаут ожидания сервера ComfyUI (%s сек)", self.TIMEOUT_SECONDS)
            self.failed.emit(
                self._tr("ComfyUI не поднялся за {} секунд.").format(self.TIMEOUT_SECONDS)
            )
            return

        self.progress.emit(
            self._tr("Запуск ComfyUI, ожидание сервера... ({}с)").format(self._elapsed)
        )


# --------------------------------------------------------------------------
# Страница со встроенным браузером
# --------------------------------------------------------------------------


