"""WebSocket-клиент ComfyUI с переподключением и Qt-сигналами событий.

Модуль отделён от Qt-независимого comfy_api и импортируется лениво.
JSON-сообщения и бинарные превью имеют разные обработчики; бинарный фрейм
содержит четырёхбайтный big-endian тип события и исходные байты.

Подключение повторяется с ограниченной экспоненциальной задержкой: сервер
может ещё запускаться при первой попытке. clientId задаёт адресата событий
задания. Если /prompt отправляет браузер с другим clientId, монитор может
получать status без progress; поэтому ETA также читает tqdm из stdout.

История диагностики и ограничений: docs/history/stage6_comment_history.md."""

import json
import uuid

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtWebSockets import QWebSocket

from .logging_setup import log

RECONNECT_INITIAL_MS = 2000
RECONNECT_MAX_MS = 15000


class ComfyWebSocketClient(QObject):
    """Клиент ws://127.0.0.1:{port}/ws с переподключением до вызова stop().

    HTTP-опрос остаётся независимым источником состояния. Подключение WS
    не гарантирует получение прогресса заданий другого clientId.

    Сигналы connected/disconnected отражают состояние соединения.
    status_received, progress_received, executing_received, executed_received
    и execution_error_received передают словари соответствующих событий.
    Для progress используются value/max/prompt_id; node=None в executing
    обозначает завершение задания.

    event_received(str, dict) передаёт все распознанные JSON-события, включая
    события без отдельного сигнала. preview_frame_received(int, bytes)
    передаёт тип события и исходный бинарный фрейм без декодирования изображения."""

    connected = Signal()
    disconnected = Signal()
    status_received = Signal(dict)
    progress_received = Signal(dict)
    executing_received = Signal(dict)
    executed_received = Signal(dict)
    execution_error_received = Signal(dict)
    event_received = Signal(str, dict)
    preview_frame_received = Signal(int, bytes)

    def __init__(self, port, parent=None):
        super().__init__(parent)
        self.port = port
        # Один clientId сохраняется на всех переподключениях экземпляра.
        self.client_id = str(uuid.uuid4())
        self._socket = QWebSocket()
        self._socket.connected.connect(self._on_connected)
        self._socket.disconnected.connect(self._on_disconnected)
        self._socket.errorOccurred.connect(self._on_error)
        self._socket.textMessageReceived.connect(self._on_text_message)
        self._socket.binaryMessageReceived.connect(self._on_binary_message)

        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self._attempt_connect)
        self._reconnect_delay_ms = RECONNECT_INITIAL_MS

        self._is_connected = False
        self._stopped = True  # start() ещё не вызывался
        # Диагностика (см. докстринг модуля): каждый РАЗЛИЧНЫЙ "type" из
        # входящих текстовых сообщений логируется один раз при первом
        # обнаружении -- если "progress" среди них ни разу не появится,
        # в логе будет видно, что ComfyUI реально шлёт вместо него,
        # вместо того чтобы гадать по недоступной здесь живой машине.
        self._seen_msg_types = set()

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    def start(self):
        """Начать (пере)подключение и держать соединение, пока не
        вызван stop(). Безопасно вызывать повторно -- уже
        запущенный/подключённый клиент просто продолжит работать."""
        if not self._stopped:
            return
        self._stopped = False
        self._reconnect_delay_ms = RECONNECT_INITIAL_MS
        self._attempt_connect()

    def stop(self):
        """Остановить клиент: закрыть сокет, отменить запланированное
        переподключение. После stop() клиент не будет сам
        переподключаться -- нужен новый start()."""
        self._stopped = True
        self._reconnect_timer.stop()
        self._socket.abort()
        if self._is_connected:
            self._is_connected = False
            self.disconnected.emit()

    def _attempt_connect(self):
        if self._stopped:
            return
        url = QUrl(f"ws://127.0.0.1:{self.port}/ws?clientId={self.client_id}")
        self._socket.open(url)

    def _schedule_reconnect(self):
        if self._stopped:
            return
        self._reconnect_timer.start(self._reconnect_delay_ms)
        self._reconnect_delay_ms = min(self._reconnect_delay_ms * 2, RECONNECT_MAX_MS)

    def _on_connected(self):
        log.info("WebSocket ComfyUI подключён (порт %s)", self.port)
        self._is_connected = True
        self._reconnect_delay_ms = RECONNECT_INITIAL_MS
        self.connected.emit()

    def _on_disconnected(self):
        was_connected = self._is_connected
        self._is_connected = False
        if was_connected:
            log.info("WebSocket ComfyUI отключился (порт %s)", self.port)
            self.disconnected.emit()
        self._schedule_reconnect()

    def _on_error(self, error):
        # errorOccurred приходит и на обычные "не удалось подключиться"
        # (ComfyUI ещё не поднялся/не запущен) -- это ожидаемая, частая
        # ситуация (переподключение уже входит в нормальный жизненный
        # цикл, не только сбой), поэтому DEBUG, а не WARNING/ERROR;
        # реальный сигнал беды -- если WS так и не подключается ни разу
        # долгое время, но это уже дело вызывающей стороны (graceful
        # fallback на HTTP-опрос, см. ResourceMonitor) отслеживать, не
        # этого класса.
        log.debug(
            "WebSocket ComfyUI: ошибка соединения (порт %s): %s",
            self.port, self._socket.errorString(),
        )
        # disconnected() Qt эмитит сам по себе следом за ошибкой в
        # большинстве случаев, но не гарантированно для "не удалось
        # установить соединение вообще" (сокет мог не дойти до
        # состояния Connected) -- планируем переподключение и здесь на
        # случай, если _on_disconnected не будет вызван.
        if self._is_connected:
            self._is_connected = False
            self.disconnected.emit()
        self._schedule_reconnect()

    def _on_text_message(self, message: str):
        try:
            data = json.loads(message)
        except (TypeError, ValueError):
            log.debug("WebSocket ComfyUI: не-JSON текстовое сообщение, пропущено")
            return
        if not isinstance(data, dict):
            return
        msg_type = data.get("type")
        payload = data.get("data")
        payload = payload if isinstance(payload, dict) else {}
        if not msg_type:
            return

        if msg_type not in self._seen_msg_types:
            # Диагностика, см. докстринг модуля -- логируем КАЖДЫЙ
            # различный тип сообщения один раз при первом обнаружении
            # (не на каждое сообщение, чтобы не спамить -- "progress" и
            # "status" могут приходить по многу раз в секунду). Ключи
            # payload (без значений) -- этого обычно достаточно, чтобы
            # свериться с ожидаемой схемой (например, есть ли
            # "prompt_id" в "progress"), не печатая потенциально большие
            # данные (outputs с путями файлов и т.п.) в лог целиком.
            self._seen_msg_types.add(msg_type)
            log.info(
                "WebSocket ComfyUI: новый тип события '%s' (ключи data: %s)",
                msg_type, sorted(payload.keys()),
            )

        self.event_received.emit(msg_type, payload)

        if msg_type == "status":
            self.status_received.emit(payload)
        elif msg_type == "progress":
            self.progress_received.emit(payload)
        elif msg_type == "executing":
            self.executing_received.emit(payload)
        elif msg_type == "executed":
            self.executed_received.emit(payload)
        elif msg_type == "execution_error":
            self.execution_error_received.emit(payload)
        # Остальные типы доступны через общий сигнал event_received.

    def _on_binary_message(self, message):
        # ВАЖНО: никогда не json.loads() здесь -- см. докстринг модуля.
        # message -- QByteArray в PySide6, приводим к bytes явно (в
        # некоторых версиях PySide6 QByteArray уже ведёт себя как
        # bytes-подобный объект в Python-коде, но .data() гарантирует
        # bytes независимо от версии биндинга).
        try:
            raw = bytes(message.data()) if hasattr(message, "data") else bytes(message)
        except Exception:
            log.debug("WebSocket ComfyUI: бинарный фрейм, не удалось прочитать как bytes")
            return
        event_type = 0
        if len(raw) >= 4:
            event_type = int.from_bytes(raw[:4], byteorder="big", signed=False)
        self.preview_frame_received.emit(event_type, raw)
