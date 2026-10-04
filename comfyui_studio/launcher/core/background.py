"""
Фоновые вызовы для опросов, которые не должны блокировать GUI (этап R6).

BackgroundTask выполняет один блокирующий вызов (HTTP-проверка порта,
запрос очереди ComfyUI) в рабочем потоке, а результат отдаёт в поток
владельца QObject (обычно GUI) через сигнал Qt. Правила:

  * одновременно выполняется не более одного вызова: пока предыдущий не
    завершился, run() возвращает False и ничего не запускает (так
    опрос раз в секунду не плодит потоки, когда сервер отвечает дольше
    интервала);
  * cancel() делает результат уже запущенного вызова недействительным —
    on_done для него не будет вызван (остановка/отмена/смена порта; без
    этого запоздавший «готово» мог бы открыть браузер после отмены
    запуска);
  * исключение из fn не пропадает: оно приходит в on_done вторым
    аргументом;
  * потоки daemon: закрытие приложения не ждёт зависший сокет (у всех
    вызовов есть собственный таймаут).

Для тестов `spawn` подменяется на синхронный запуск (spawn(fn) просто
вызывает fn): тогда сигнал доставляется прямым вызовом в том же потоке и
весь опрос становится детерминированным.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal


def spawn_thread(fn):
    threading.Thread(target=fn, daemon=True).start()


class BackgroundTask(QObject):
    # (поколение, результат, ошибка, on_done) -> поток владельца
    _delivered = Signal(object)

    def __init__(self, parent=None, spawn=None):
        super().__init__(parent)
        self._spawn = spawn or spawn_thread
        self._generation = 0
        self._busy = False
        self._delivered.connect(self._on_delivered)

    def is_busy(self):
        return self._busy

    def run(self, fn, on_done):
        """Запускает fn() в рабочем потоке; on_done(result, error) будет
        вызван в потоке владельца. Возвращает False (и ничего не
        запускает), если предыдущий вызов ещё выполняется."""
        if self._busy:
            return False
        self._busy = True
        self._generation += 1
        generation = self._generation

        def runner():
            try:
                result, error = fn(), None
            except Exception as e:  # noqa: BLE001 — ошибка уходит получателю
                result, error = None, e
            try:
                self._delivered.emit((generation, result, error, on_done))
            except RuntimeError:
                pass  # владелец уже удалён (окно закрыто) — получателя нет

        self._spawn(runner)
        return True

    def cancel(self):
        """Результат уже запущенного вызова (если он есть) будет
        отброшен; новый run() разрешён сразу."""
        self._generation += 1
        self._busy = False

    def _on_delivered(self, payload):
        generation, result, error, on_done = payload
        if generation != self._generation:
            return
        self._busy = False
        on_done(result, error)
