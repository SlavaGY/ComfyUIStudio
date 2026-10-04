"""
Контроллер запуска ComfyUI -> Imagine (этап R5).

Вынесено из MainWindow (launcher_window.py): создание и остановка
процессов ComfyUI/Imagine, два watcher'а готовности, откат при сбоях,
отмена запуска. Поведение сохранено один в один (контракт зафиксирован в
docs/baseline_R0.md §3 и перенесённых тестах tests/launcher/
test_launch_controller.py), включая прежние «странности»:

  * ошибка ComfyUI (on_server_failed) гасит только ComfyUI и не обнуляет
    ни один объект процесса;
  * ошибка Imagine (on_imagine_failed) гасит оба процесса и тоже ничего
    не обнуляет;
  * отмена и stop() обнуляют imagine_process, но объект comfy_process
    остаётся (его is_running() уже False).

За окном остаётся то, что относится к виду: переключение страниц,
встроенный браузер, синхронизация темы, трей. Контроллер сообщает окну
сигналами: comfy_ready (открыть браузер на ComfyUI) и imagine_ready(port)
(открыть браузер на Imagine). С видом настроек он общается через
переданный `view` (SettingsPage): set_status, set_server_running,
show_launch_progress, hide_launch_progress, update_launch_progress, _tr.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ..core.comfy_process import ComfyProcess
from ..core.config import build_extra_launch_args, prepare_launch_script
from ..core.imagine_process import ImagineProcess
from ..core.logging_setup import log
from ..integration.comfy_theme import sync_comfyui_color_palette
from .widgets.launch_watcher import ImagineLaunchWatcher, LaunchWatcher


class LaunchController(QObject):
    comfy_ready = Signal()
    imagine_ready = Signal(int)

    def __init__(
        self,
        view,
        log_bridge,
        current_theme,
        loc=None,
        parent=None,
        launch_watcher=None,
        imagine_launch_watcher=None,
    ):
        """current_theme — callable без аргументов, возвращает имя
        текущей темы приложения (нужно для синхронизации палитры ComfyUI
        до старта). launch_watcher/imagine_launch_watcher — для подмены
        в тестах."""
        super().__init__(parent)
        self._view = view
        self._log_bridge = log_bridge
        self._current_theme = current_theme
        self.cfg = {}
        self.comfy_process = None
        # Процесс Imagine существует только когда cfg["interface"] ==
        # "imagine"; None во всех остальных случаях, в т.ч. пока ComfyUI
        # ещё запускается.
        self.imagine_process = None

        self.launch_watcher = launch_watcher or LaunchWatcher(loc=loc, parent=self)
        self.launch_watcher.ready.connect(self.on_server_ready)
        self.launch_watcher.failed.connect(self.on_server_failed)
        self.launch_watcher.progress.connect(view.update_launch_progress)

        # Второй watcher включается в цепочку ТОЛЬКО для interface ==
        # "imagine": on_server_ready() либо сразу сообщает окну открыть
        # браузер на ComfyUI, либо сначала поднимает Imagine и ждёт его.
        self.imagine_launch_watcher = imagine_launch_watcher or ImagineLaunchWatcher(
            loc=loc, parent=self
        )
        self.imagine_launch_watcher.ready.connect(self.on_imagine_ready)
        self.imagine_launch_watcher.failed.connect(self.on_imagine_failed)
        self.imagine_launch_watcher.progress.connect(view.update_launch_progress)

    # -- состояние -------------------------------------------------------

    def is_comfy_running(self):
        return self.comfy_process is not None and self.comfy_process.is_running()

    def running_port(self):
        """Порт ComfyUI, пока его процесс жив (для ResourceMonitor)."""
        if self.is_comfy_running():
            return self.cfg.get("port")
        return None

    # -- запуск ------------------------------------------------------------

    def launch(self, cfg):
        self.cfg = cfg
        try:
            launch_script = prepare_launch_script(
                cfg["root_path"], cfg["script"], build_extra_launch_args(cfg)
            )
        except OSError as e:
            log.exception("Не удалось подготовить скрипт запуска")
            self._view.set_status(
                self._view._tr("Не удалось подготовить скрипт запуска: {}").format(e)
            )
            return

        if cfg.get("sync_comfy_theme"):
            sync_comfyui_color_palette(cfg["root_path"], self._current_theme())

        self.comfy_process = ComfyProcess(
            cfg["root_path"], launch_script, self._log_bridge,
            env_overrides=cfg.get("env_vars"),
        )
        self.comfy_process.start()

        # Остаёмся на экране настроек — виден живой лог запуска, только
        # снизу появляется индикатор прогресса вместо отдельной страницы.
        self._view.show_launch_progress(
            self._view._tr("Запуск ComfyUI, ожидание сервера...")
        )
        self.launch_watcher.start(cfg["port"], self.comfy_process)

    def on_server_ready(self):
        log.info("Сервер ComfyUI поднялся")

        if self.cfg.get("interface") == "imagine":
            self._start_imagine()
            return

        self.comfy_ready.emit()

    def _start_imagine(self):
        """Поднимает Imagine, направленный на уже запущенный этим же
        лаунчером ComfyUI (cfg["port"]) — см. "Интерфейс" в ui/settings/
        comfyui_page.py и core/imagine_process.py. Imagine не поднимает
        ComfyUI сам, в отличие от самостоятельного режима (см.
        imagine/__init__.py)."""
        imagine_cfg = self.cfg.get("imagine", {})
        imagine_port = imagine_cfg.get("port", 7860)
        self._view.show_launch_progress(
            self._view._tr("Запуск Imagine, ожидание сервера...")
        )
        self.imagine_process = ImagineProcess(
            host="127.0.0.1",
            port=imagine_port,
            comfy_host="127.0.0.1",
            comfy_port=self.cfg["port"],
            dev_mode=bool(imagine_cfg.get("dev_mode", False)),
            # Настроенный порт Remote передаётся вне зависимости от того,
            # включён ли Remote (Remote, этап 3 дорожной карты): если не
            # запущен, progress_forwarder.py внутри Imagine просто не
            # достучится (best-effort) — Imagine от Remote независим
            # (см. §0.1 дорожной карты Remote).
            remote_port=self.cfg.get("remote", {}).get("port"),
        )
        try:
            self.imagine_process.start()
        except RuntimeError as e:
            self.on_imagine_failed(
                self._view._tr("Не удалось запустить Imagine: {}").format(e)
            )
            return
        self.imagine_launch_watcher.start(imagine_port, self.imagine_process)

    def on_imagine_ready(self):
        log.info("Сервер Imagine поднялся, открываю встроенный браузер")
        self._view.hide_launch_progress()
        self.imagine_ready.emit(self.cfg.get("imagine", {}).get("port", 7860))

    # -- сбои --------------------------------------------------------------

    def on_server_failed(self, message):
        if self.comfy_process:
            self.comfy_process.stop()
        self._view.hide_launch_progress()
        self._view.set_status(message)
        self._view.set_server_running(False)

    def on_imagine_failed(self, message):
        # Imagine не поднялся — откатываемся полностью (останавливаем и
        # ComfyUI тоже), а не остаёмся в подвешенном состоянии «ComfyUI
        # работает, но показать нечего»: пользователь выбрал интерфейс
        # Imagine и не ждёт, что ComfyUI продолжит крутиться в фоне без
        # видимого интерфейса.
        if self.imagine_process:
            self.imagine_process.stop()
        if self.comfy_process:
            self.comfy_process.stop()
        self._view.hide_launch_progress()
        self._view.set_status(message)
        self._view.set_server_running(False)

    # -- отмена / остановка --------------------------------------------------

    def cancel(self):
        self.launch_watcher.stop()
        self.imagine_launch_watcher.stop()
        self.stop_processes()
        self._view.hide_launch_progress()
        self._view.set_status(self._view._tr("Запуск отменён."))
        self._view.set_server_running(False)

    def stop(self):
        """Остановка по просьбе пользователя (кнопка/трей): процессы +
        сброс статуса на странице настроек. Переключение страницы и
        выгрузку браузера делает окно."""
        self.stop_processes()
        self._view.set_status("")
        self._view.set_server_running(False)

    def stop_processes(self):
        """Только процессы, без обращений к виду (используется и при
        выходе из приложения)."""
        if self.imagine_process:
            self.imagine_process.stop()
            self.imagine_process = None
        if self.comfy_process:
            self.comfy_process.stop()
