"""
Опрос готовности сервера ComfyUI после запуска процесса (таймаут,
статус для UI).

Вынесено из comfyui_launcher.py (этап 1 дорожной карты).
"""

from PySide6.QtCore import QObject, QTimer, Signal

from ...core.comfy_api import ComfyAPIClient
from ...core.comfy_process import ComfyProcess
from ...core.constants import APP_LOG_PATH
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
# Опрос готовности Imagine (см. "Интерфейс: Imagine" в
# ui/settings/comfyui_page.py и core/imagine_process.py) — запускается
# ПОСЛЕ того, как ComfyUI уже готов (LaunchWatcher выше отработал) и
# ImagineProcess.start() был вызван; отдельный watcher, а не расширение
# LaunchWatcher, потому что процесс другой (ImagineProcess, не
# ComfyProcess) и проверка готовности другая (простой HTTP GET '/', а не
# ComfyAPIClient) -- пытаться обобщить оба под одним классом добавило бы
# больше условных ветвлений, чем сэкономило бы кода.
class ImagineLaunchWatcher(QObject):
    ready = Signal()
    failed = Signal(str)
    progress = Signal(str)

    TIMEOUT_SECONDS = 60

    def __init__(self, loc=None, parent=None):
        super().__init__(parent)
        self.loc = loc
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._check)
        self._port = None
        self._elapsed = 0
        self._process = None

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def start(self, port, process):
        self._port = port
        self._process = process
        self._elapsed = 0
        self.progress.emit(self._tr("Запуск Imagine, ожидание сервера..."))
        self._timer.start()

    def stop(self):
        self._timer.stop()

    def _check(self):
        from ...core.imagine_process import is_imagine_available

        if self._process is not None and not self._process.is_running():
            self.stop()
            code = self._process.exit_code()
            log.error("Процесс Imagine завершился раньше времени, код выхода: %s", code)
            # Полный вывод процесса (обычно traceback, если это
            # ImportError/ошибка конфигурации) уже ушёл в лог-файл из
            # ImagineProcess._log_exit() -- указываем на него, а не
            # дублируем текст здесь: он может быть длиной в экраны.
            self.failed.emit(
                self._tr(
                    "Процесс Imagine неожиданно завершился (код выхода: {}). "
                    "Подробности — в лог-файле: {}"
                ).format(code, APP_LOG_PATH)
            )
            return

        if is_imagine_available(self._port):
            self.stop()
            self.ready.emit()
            return

        self._elapsed += 1
        if self._elapsed >= self.TIMEOUT_SECONDS:
            self.stop()
            log.error("Таймаут ожидания сервера Imagine (%s сек)", self.TIMEOUT_SECONDS)
            self.failed.emit(
                self._tr("Imagine не поднялся за {} секунд.").format(self.TIMEOUT_SECONDS)
            )
            return

        self.progress.emit(
            self._tr("Запуск Imagine, ожидание сервера... ({}с)").format(self._elapsed)
        )


# --------------------------------------------------------------------------
# Страница со встроенным браузером
# --------------------------------------------------------------------------


