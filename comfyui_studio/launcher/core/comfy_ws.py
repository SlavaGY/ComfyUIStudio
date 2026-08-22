"""
WebSocket-слой поверх ComfyAPIClient (этап 7 дорожной карты —
WebSocket realtime layer).

Отдельный модуль, а не часть comfy_api.py -- comfy_api.py намеренно
остался Qt-независимым на этапе 6 (чистый urllib/json, покрыт
unit-тестами без Qt, см. tests/launcher/test_comfy_api.py и правку
launcher/__init__.py про ленивый импорт), и WebSocket-клиент на базе
QWebSocket эту независимость сломал бы, если бы жил в том же файле.
ComfyAPIClient.subscribe_websocket() импортирует этот модуль ЛЕНИВО,
внутри метода -- см. comfy_api.py.

Защитная мера, ради которой стоит отдельно проговорить дизайн: ComfyUI
на одном и том же сокете /ws шлёт вперемешку ТЕКСТОВЫЕ (JSON:
status/progress/executing/executed/execution_error/...) и БИНАРНЫЕ
(превью-изображения при генерации, если включены preview-ноды: 4-байтный
big-endian тип события, дальше сырые байты) фреймы. Текстовые и
бинарные сообщения разведены на РАЗНЫЕ обработчики Qt-сигналов
(textMessageReceived / binaryMessageReceived) с самого начала, и
binaryMessageReceived никогда не пытается json.loads() свои данные --
см. _on_binary_message.

ИСПРАВЛЕНО после первого прогона этапа 7 на реальной машине: более
ранняя попытка использовать /ws в этом проекте (до этапа 0, в истории
чата, не в git) на самом деле проваливалась на этапе САМОГО
ПОДКЛЮЧЕНИЯ -- "Connection refused" один раз при старте (WS пытался
подключиться раньше, чем поднимался HTTP-сервер ComfyUI), без единой
попытки переподключиться после этого -- та реализация после первой
неудачи просто логировала предупреждение и на этом навсегда
останавливалась. Комментарий "не удалось надёжно поймать формат
сообщений", который отсюда пошёл дальше по коду и по этой дорожной
карте -- ошибочная догадка задним числом (WS ни разу не подключился
настолько, чтобы вообще получить хоть одно сообщение и столкнуться с
форматом). RECONNECT_INITIAL_MS/RECONNECT_MAX_MS/_schedule_reconnect
ниже устраняют именно эту, реальную причину.

Старая реализация также подключалась с query-параметром
`?clientId=<uuid>` (см. self.client_id ниже) -- этот клиент того не
делал изначально. Добавлено сюда же с диагностическим логом типов
входящих сообщений (см. _on_text_message) -- полезно и то, и другое,
но ни то ни другое НЕ решает проблему по факту (см. ниже).

ОКОНЧАТЕЛЬНЫЙ ОТВЕТ (после второго реального прогона, сверено с
исходником ComfyUI -- server.py/execution.py, ветка master,
comfyanonymous/ComfyUI и Comfy-Org/ComfyUI): "progress"/"executing"/
"executed"/"execution_error" структурно НЕДОСТИЖИМЫ для этого
клиента, и дело не в конфигурации. `PromptServer.send_json(event,
data, sid=None)` рассылает всем сокетам только когда `sid is None`
(так уходит "status" -- единственное, что реально доходит до этого
клиента). А вот при старте задания `execution.py:
PromptExecutor.execute_async` делает `self.server.client_id =
extra_data["client_id"]` -- ЗАПОМИНАЕТ clientId ИЗ ЗАПРОСА `POST
/prompt`, которым ЭТО задание было поставлено в очередь -- и каждое
следующее `send_sync("progress"/"executing"/"executed", data,
self.server.client_id)` для него уходит ТОЛЬКО сокету с этим
конкретным clientId (подтверждено собственным тестом ComfyUI --
`tests/execution/test_progress_isolation.py`, буквально "progress
updates are properly isolated between WebSocket clients"). Задания в
типичной установке лаунчера ставит в очередь встроенный браузер
(QWebEngineView с родным фронтендом ComfyUI) под СВОИМ clientId
(хранит сам, обычно в localStorage страницы) -- наш сгенерированный
здесь uuid4 никогда с ним не совпадёт, какой бы valid UUID мы ни
подставили. Это не чинится настройкой клиента -- см. подраздел
"Окончательный ответ" в тексте этапа 7 дорожной карты для полного
разбора и того, что теоретически могло бы это решить (чтение
реального clientId браузера через QWebEngineView JS -- отдельная,
не сделанная здесь задача).
"""

import json
import uuid

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtWebSockets import QWebSocket

from .logging_setup import log

RECONNECT_INITIAL_MS = 2000
RECONNECT_MAX_MS = 15000


class ComfyWebSocketClient(QObject):
    """Подключается к ws://127.0.0.1:{port}/ws и разбирает события
    ComfyUI в реальном времени.

    Это ДОПОЛНИТЕЛЬНЫЙ канал поверх HTTP-опроса из этапа 6, не замена
    ему -- см. критерий готовности этапа 7 в дорожной карте:
    ResourceMonitor переключается на данные отсюда, когда соединение
    есть (см. connected/disconnected ниже), и продолжает работать на
    HTTP-опросе, если WS недоступен (внешний/удалённый инстанс ComfyUI,
    временный сбой соединения, версия ComfyUI без этого эндпоинта).
    Экземпляр сам переподключается с нарастающей паузой, пока не
    вызван stop() -- вызывающей стороне не нужно следить за этим
    вручную.

    Сигналы:
        connected()                          -- рукопожатие успешно
        disconnected()                       -- соединение потеряно/
                                                 закрыто (в т.ч. между
                                                 попытками переподключения)
        status_received(dict)                -- "status" -- обычно
                                                 {"exec_info": {"queue_remaining": N}}
        progress_received(dict)              -- "progress" -- содержит
                                                 value/max и, что важно,
                                                 prompt_id -- в отличие
                                                 от tqdm-строк в stdout
                                                 (см. system_monitor.py),
                                                 не нужно гадать, к
                                                 какому заданию относится
        executing_received(dict)             -- "executing" -- текущая
                                                 нода/prompt_id (node=None
                                                 означает, что задание
                                                 завершилось)
        executed_received(dict)              -- "executed" -- нода
                                                 отработала, есть outputs
        execution_error_received(dict)       -- "execution_error" --
                                                 канал предоставлен уже
                                                 сейчас, но НЕ разбирается
                                                 по workflow здесь -- это
                                                 задача этапа 8 ("ошибки
                                                 конкретного workflow"),
                                                 не этапа 7
        event_received(str, dict)            -- общий сигнал для ЛЮБОГО
                                                 распознанного текстового
                                                 события (включая все
                                                 перечисленные выше) --
                                                 под него же подключается
                                                 callback из
                                                 ComfyAPIClient.subscribe_websocket(callback)
        preview_frame_received(int, bytes)   -- бинарный фрейм (обычно
                                                 превью-изображение);
                                                 event-type + сырые байты
                                                 БЕЗ попытки декодировать
                                                 как JSON или как
                                                 изображение -- см.
                                                 докстринг модуля.
                                                 Декодирование самого
                                                 превью в этом этапе не
                                                 реализовано (не входит
                                                 в критерий готовности
                                                 этапа 7 -- см. дорожную
                                                 карту), сигнал даёт
                                                 будущему коду точку
                                                 подключения.
    """

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
        # client_id -- см. докстринг модуля: старая (дочатовая)
        # реализация подключалась с этим query-параметром, эта
        # изначально нет. Один uuid4 на весь жизненный цикл клиента
        # (переживает переподключения) -- если окажется, что ComfyUI
        # действительно требует стабильный client_id, чтобы считать
        # соединение "тем же" между реконнектами (а не только чтобы
        # вообще принять его), так безопаснее, чем генерировать новый
        # на каждую попытку.
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
        # Остальные типы (execution_start/execution_success/
        # execution_interrupted/execution_cached/...) пока не разбираются
        # по отдельности -- у них есть общий event_received выше, этого
        # достаточно для критерия готовности этапа 7; специфичные
        # сигналы добавлять по мере появления реальных потребителей
        # (см. этап 8).

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
