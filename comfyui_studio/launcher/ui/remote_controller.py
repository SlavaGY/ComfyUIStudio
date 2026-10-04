"""
Контроллер Remote (этап R4): владеет процессом Remote, опросом его
готовности и вызовами его локального API из окна настроек.

Вынесено из MainWindow (launcher_window.py), где этот блок занимал ~180
строк и был единственным местом, делавшим HTTP-вызовы прямо в GUI-потоке.
Поведение сохранено (см. docs/baseline_R0.md §3), кроме одного
намеренного изменения: pairing, список устройств и отзыв
(call_local_api, таймаут 3 с на вызов) теперь выполняются в рабочем
потоке, а результат возвращается в GUI-поток сигналом. Раньше недоступный
или зависший Remote замораживал окно Studio на время таймаута (при отзыве
нескольких устройств — на время каждого вызова подряд).

Контроллер ничего не знает о виде страницы настроек: ему нужен только
объект `view` с методами set_remote_running_state / show_lan_url /
show_remote_pairing_code / show_remote_pairing_error /
set_remote_devices / show_remote_devices_error (это SettingsPage).

Устаревшие ответы отбрасываются: если за время запроса Remote был
остановлен или перезапущен, результат не показывается.
"""

from __future__ import annotations

import threading
import urllib.error

from PySide6.QtCore import QObject, QTimer, Signal

from ..core.background import BackgroundTask
from ..core.config import load_config
from ..core.logging_setup import log
from ..core.remote_net import (
    call_local_api,
    extract_error_detail,
    get_lan_ip,
    is_remote_available,
)
from ..core.remote_process import RemoteProcess

READY_POLL_INTERVAL_MS = 300
READY_MAX_ATTEMPTS = 20  # ~6 секунд при шаге 300 мс

NOT_RUNNING_MESSAGE = "Remote не запущен -- включите удалённый доступ."


def _spawn_thread(fn):
    threading.Thread(target=fn, daemon=True).start()


class RemoteController(QObject):
    # Выполнить callable в потоке получателя (GUI). Сигнал, а не QTimer
    # .singleShot из рабочего потока: у того нет цикла событий.
    _result_ready = Signal(object)

    def __init__(self, view, parent=None, spawn=None):
        super().__init__(parent)
        self._view = view
        # Подмена потока в тестах: spawn(fn) должен выполнить fn.
        self._spawn = spawn or _spawn_thread
        self.process = None
        self._ready_attempts = 0
        self._inflight = set()
        # R6: проверка «Remote поднялся» (HTTP, до 1 с) — в рабочем потоке.
        # spawn берётся лениво, чтобы подмена controller._spawn в тестах
        # действовала и здесь.
        self._ready_probe = BackgroundTask(self, spawn=lambda fn: self._spawn(fn))
        self._result_ready.connect(self._run_callback)

    # -- состояние -------------------------------------------------------

    def is_running(self):
        return self.process is not None and self.process.is_running()

    # -- запуск / остановка / готовность ----------------------------------

    def start(self):
        # НАЙДЕНО ПРИ ЖИВОМ ТЕСТИРОВАНИИ (2026-09-06): настройки Remote
        # автосохраняются прямо из AppSettingsDialog (_auto_save), а не
        # через конфиг окна, поэтому окно держит устаревший снимок.
        # Берём свежий конфиг С ДИСКА. (R7 заменит это общим ConfigStore.)
        fresh_cfg = load_config()
        remote_cfg = fresh_cfg.get("remote", {})
        port = remote_cfg.get("port", 7861)
        host = remote_cfg.get("host", "127.0.0.1")
        imagine_port = fresh_cfg.get("imagine", {}).get("port")
        try:
            self.process = RemoteProcess(
                host=host,
                port=port,
                comfy_host="127.0.0.1",
                comfy_port=fresh_cfg.get("port"),
                imagine_port=imagine_port,
            )
            self.process.start()
        except RuntimeError as e:
            log.error("Не удалось запустить Remote: %s", e)
            self.process = None
            self._view.set_remote_running_state(False, error=str(e))
            return
        self._ready_attempts = 0
        QTimer.singleShot(READY_POLL_INTERVAL_MS, self._poll_ready)

    def stop(self):
        if self.process:
            self.process.stop()
            self.process = None
        self._inflight.clear()  # ответы уже летящих запросов станут устаревшими
        self._ready_probe.cancel()
        self._view.set_remote_running_state(False)

    def on_enable_toggled(self, enabled: bool):
        if enabled:
            if self.process is None:
                self.start()
        else:
            self.stop()

    def _poll_ready(self):
        """Простой опрос готовности без отдельного класса-watcher —
        пользователь и так ждёт, глядя на статус в настройках. Проверка
        порта (HTTP, таймаут 1 с) идёт в рабочем потоке (R6); следующий
        опрос планируется после ответа, поэтому попытки не копятся, а
        счётчик считает завершённые неудачные проверки, как и раньше."""
        if self.process is None:
            return
        if not self.process.is_running():
            code = self.process.exit_code()
            log.error("Процесс Remote завершился раньше времени, код выхода: %s", code)
            self._view.set_remote_running_state(
                False,
                error=f"процесс неожиданно завершился (код выхода {code}), подробности в логе",
            )
            self.process = None
            return
        proc = self.process
        port = proc.port
        self._ready_probe.run(
            lambda: is_remote_available(port),
            lambda result, error: self._on_ready_probe(proc, result, error),
        )

    def _on_ready_probe(self, proc, available, error):
        if self.process is not proc:
            return  # Remote остановили или перезапустили за время проверки
        if error is not None:
            log.warning("Ошибка проверки готовности Remote: %s", error)
            available = False
        if available:
            self._view.set_remote_running_state(True)
            # Если Remote слушает не только localhost, подсказываем готовый
            # адрес для телефона вместо ручного поиска LAN-IP.
            if proc.host != "127.0.0.1":
                lan_ip = get_lan_ip()
                url = f"http://{lan_ip}:{proc.port}/" if lan_ip else None
                self._view.show_lan_url(url)
            else:
                self._view.show_lan_url(None)
            return
        self._ready_attempts += 1
        if self._ready_attempts >= READY_MAX_ATTEMPTS:
            self._view.set_remote_running_state(
                False, error="не поднялся вовремя, подробности в логе"
            )
            return
        QTimer.singleShot(READY_POLL_INTERVAL_MS, self._poll_ready)

    # -- локальный API: pairing / устройства -------------------------------

    def request_pairing(self):
        if not self.is_running():
            self._view.show_remote_pairing_error(NOT_RUNNING_MESSAGE)
            return
        port = self.process.port
        self._run_async(
            "pair",
            lambda: call_local_api(port, "POST", "/api/v1/remote/pair/start"),
            self._on_pairing_done,
        )

    def refresh_devices(self):
        if not self.is_running():
            self._view.show_remote_devices_error(NOT_RUNNING_MESSAGE)
            return
        port = self.process.port
        self._run_async(
            "devices",
            lambda: call_local_api(port, "GET", "/api/v1/remote/devices"),
            self._on_devices_done,
        )

    def revoke(self, device_ids: list):
        if not self.is_running():
            self._view.show_remote_devices_error(NOT_RUNNING_MESSAGE)
            return
        port = self.process.port
        ids = list(device_ids)

        def work():
            # Первая же ошибка прерывает отзыв (как и раньше).
            for device_id in ids:
                call_local_api(port, "POST", f"/api/v1/remote/devices/{device_id}/revoke")

        self._run_async("revoke", work, self._on_revoke_done)

    def _on_pairing_done(self, result, error):
        if error is None:
            try:
                code, expires_at, attempts_left = (
                    result["code"], result["expires_at"], result["attempts_left"],
                )
            except (KeyError, TypeError):
                error = ValueError(f"неожиданный ответ Remote: {result!r}")
        if error is not None:
            self._view.show_remote_pairing_error(self._describe_error(error))
            return
        self._view.show_remote_pairing_code(code, expires_at, attempts_left)

    def _on_devices_done(self, result, error):
        if error is not None:
            self._view.show_remote_devices_error(self._describe_error(error))
            return
        self._view.set_remote_devices(result or [])

    def _on_revoke_done(self, _result, error):
        if error is not None:
            self._view.show_remote_devices_error(self._describe_error(error))
            return
        self.refresh_devices()

    @staticmethod
    def _describe_error(error) -> str:
        if isinstance(error, urllib.error.HTTPError):  # подкласс URLError — первым
            return extract_error_detail(error)
        return str(error)

    # -- рабочий поток -----------------------------------------------------

    def _run_async(self, key, work, on_done):
        """Выполняет work() в рабочем потоке; on_done(result, error)
        вызывается в GUI-потоке. Повторный запрос того же вида, пока
        предыдущий не завершён, игнорируется (двойной клик)."""
        if key in self._inflight:
            return
        self._inflight.add(key)
        proc = self.process

        def runner():
            try:
                result, error = work(), None
            except Exception as e:  # noqa: BLE001 — любая ошибка уходит в UI текстом
                result, error = None, e
            try:
                self._result_ready.emit(
                    lambda: self._finish(key, proc, on_done, result, error)
                )
            except RuntimeError:
                pass  # контроллер уже удалён (окно закрыто) — показывать некому

        self._spawn(runner)

    def _finish(self, key, proc, on_done, result, error):
        if self.process is not proc or key not in self._inflight:
            return  # Remote остановили/перезапустили за время запроса
        self._inflight.discard(key)
        on_done(result, error)

    def _run_callback(self, callback):
        callback()
