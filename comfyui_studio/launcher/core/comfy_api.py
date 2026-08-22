"""
HTTP-клиент к API ComfyUI (этап 6 дорожной карты — HTTP API abstraction).

До этого этапа здесь был "зачаток" слоя — набор свободных функций
(fetch_queue_status/fetch_history_ids/count_steps_in_prompt/is_port_open),
вызываемых напрямую из UI-кода (LaunchWatcher, ResourceMonitor). Этап 6
собирает их в класс ComfyAPIClient и расширяет чисто по HTTP, БЕЗ
изменения транспорта (никакого /ws здесь нет — см. этап 7): та же
логика опроса, что и раньше, просто за общим фасадом с явными
методами и типизированными результатами вместо словарей.

Свободные функции (is_port_open, fetch_queue_status, fetch_history_ids,
count_steps_in_prompt) сохранены как есть — это чистая логика без
побочных эффектов вне сети, ComfyAPIClient вызывает их же внутри, а не
дублирует. Стадия 0 дорожной карты отдельно называла именно эти
функции первыми кандидатами на unit-тесты (чистая логика, без
Qt-зависимостей) — см. tests/launcher/test_comfy_api.py.

ComfyAPIClient статeless по HTTP-соединению (обычный urllib на каждый
вызов, без держащегося сокета/сессии), но не по порту: порт можно
задать один раз в конструкторе (случай LaunchWatcher — порт фиксирован
на всю сессию запуска, известен из cfg) или передавать в каждый вызов
отдельно (случай ResourceMonitor — текущий порт решает внешний
callback в MainWindow._get_running_port и может быть None, если
ComfyUI сейчас не запущен). Явный port= в вызове метода всегда
перекрывает self.port.
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field

DEFAULT_TIMEOUT = 1.5


def is_port_open(port, timeout=1.0):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=timeout):
            return True
    except urllib.error.URLError:
        return False
    except Exception:
        return False


STEP_INPUT_KEYS = ("steps", "sampling_steps", "num_steps")  # имена входов, которые ищем в узлах графа

def count_steps_in_prompt(prompt_dict):
    """Сумма числового поля "steps" по всем узлам графа задания (формат
    API-графа: {node_id: {class_type, inputs}}) — грубая, но рабочая
    эвристика объёма работы для KSampler/KSamplerAdvanced и большинства
    кастомных сэмплеров с тем же именем входа. Если steps приходит не
    числом (ссылка на другой узел), просто пропускаем этот узел -- тянуть
    оттуда рекурсивно не будем, оценка и так приблизительная."""
    total = 0
    if not prompt_dict:
        return total
    for node in prompt_dict.values():
        inputs = node.get("inputs") if isinstance(node, dict) else None
        if not inputs:
            continue
        for key in STEP_INPUT_KEYS:
            v = inputs.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                total += v
    return total


def _fetch_json(url, timeout):
    """Общий каркас GET-запроса + разбор JSON. Возвращает None на любой
    сетевой/парсинг сбой (недоступный порт, не-JSON ответ, таймаут) —
    вызывающий код везде трактует None как "сейчас узнать не удалось",
    не как ошибку, которую нужно поднимать выше (тот же принцип, что и
    в исходных fetch_queue_status/fetch_history_ids до этапа 6)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def fetch_queue_status(port, timeout=1.5):
    """Возвращает {"running", "pending", "running_ids", "step_totals"} из
    /queue ComfyUI, или None, если недоступно.

    running_ids -- set() prompt_id заданий, которые ПРЯМО СЕЙЧАС
    выполняются (не просто числятся первыми в очереди) — нужно, чтобы
    ResourceMonitor мог отличить их от только что закончившихся в
    /history (см. fetch_history_ids ниже и комментарий в _poll).

    step_totals -- {prompt_id: total_steps} для ВСЕХ заданий в очереди
    (бегущих и ожидающих), см. count_steps_in_prompt() -- нужно для
    оценки оставшегося времени всей очереди."""
    data = _fetch_json(f"http://127.0.0.1:{port}/queue", timeout)
    if data is None:
        return None
    running_items = data.get("queue_running", [])
    pending_items = data.get("queue_pending", [])
    running_ids = {item[1] for item in running_items if len(item) > 1}
    step_totals = {}
    for item in running_items + pending_items:
        if len(item) > 2:
            step_totals[item[1]] = count_steps_in_prompt(item[2])
    return {
        "running": len(running_items),
        "pending": len(pending_items),
        "running_ids": running_ids,
        "step_totals": step_totals,
    }


def _fetch_raw_queue(port, timeout):
    """Как fetch_queue_status, но без агрегации -- сырой ответ /queue,
    нужен там, где важен сам граф задания (item[2]), а не только его
    посчитанный объём в шагах. Сейчас единственный потребитель --
    ComfyAPIClient.get_current_workflow()."""
    return _fetch_json(f"http://127.0.0.1:{port}/queue", timeout)


def fetch_history_ids(port, timeout=1.5):
    """Возвращает set() prompt_id всех записей /history ComfyUI, или None,
    если недоступно. /history — собственный журнал ComfyUI обо всех
    запросах, которые ДОЕХАЛИ до конца (успешно или с ошибкой; висящие в
    очереди туда не попадают, добавляются туда только после завершения
    исполнения — см. execution.py/PromptQueue.task_done в самом
    ComfyUI)."""
    data = _fetch_json(f"http://127.0.0.1:{port}/history", timeout)
    if data is None:
        return None
    return set(data.keys())


# --------------------------------------------------------------------------
# Типизированные результаты (этап 6)
# --------------------------------------------------------------------------

@dataclass
class QueueState:
    """Состояние очереди ComfyUI -- то же, что раньше возвращалось словарём
    из fetch_queue_status(), но именованными полями."""
    running: int
    pending: int
    running_ids: set
    step_totals: dict


@dataclass
class SystemStats:
    """Ответ /system_stats ComfyUI (новый эндпоинт для этапа 6 -- не
    опрашивался нигде до этого этапа). Смоделированы только поля,
    которые реально нужны сейчас (см. этап 8 -- "какие модели
    используются" по VRAM per-device); остальное остаётся доступным
    через raw, а не раздувается в дополнительные именованные поля
    заранее. ВАЖНО: точный набор ключей ComfyUI не зафиксирован здесь
    контрактом (в песочнице разработки нет запущенного ComfyUI, чтобы
    сверить реальный ответ живьём) -- from_response() поэтому
    защищается через .get() везде и не падает, если какого-то поля нет
    или структура чуть отличается между версиями ComfyUI; при первом
    реальном использовании (этап 8) стоит свериться с фактическим
    ответом на машине пользователя."""
    os: str = None
    python_version: str = None
    comfyui_version: str = None
    embedded_python: bool = None
    devices: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_response(cls, data):
        system = data.get("system") if isinstance(data, dict) else None
        system = system if isinstance(system, dict) else {}
        devices = data.get("devices") if isinstance(data, dict) else None
        return cls(
            os=system.get("os"),
            python_version=system.get("python_version"),
            comfyui_version=system.get("comfyui_version"),
            embedded_python=system.get("embedded_python"),
            devices=devices if isinstance(devices, list) else [],
            raw=data if isinstance(data, dict) else {},
        )


class ComfyAPIClient:
    """Фасад HTTP-опроса ComfyUI (этап 6 дорожной карты).

    Точка роста, описанная в плане этапа 6:
        get_queue()            -- расширяет fetch_queue_status
        get_history()          -- расширяет fetch_history_ids
        get_system_stats()     -- новое: /system_stats
        get_current_workflow() -- новое: граф сейчас выполняемого задания
        get_object_info()      -- новое: /object_info

    Критерий готовности этапа: LaunchWatcher и ResourceMonitor (и через
    него -- ResourceBar/LogPanel/индикатор очереди в TrayIcon/
    BrowserPage, получающие данные из stats_updated) переведены на эти
    методы и НЕ используют fetch_queue_status/fetch_history_ids/
    is_port_open напрямую. Ни одной строчки WebSocket-кода здесь нет --
    это намеренно, см. этап 7.
    """

    def __init__(self, port=None, timeout=DEFAULT_TIMEOUT):
        self.port = port
        self.timeout = timeout

    def _resolve_port(self, port):
        return port if port is not None else self.port

    def is_available(self, port=None, timeout=None) -> bool:
        """True, если ComfyUI отвечает на своём порту. Порт неизвестен
        (ни в вызове, ни в конструкторе) -- трактуем как "недоступен",
        не как ошибку."""
        p = self._resolve_port(port)
        if p is None:
            return False
        return is_port_open(p, timeout=timeout or self.timeout)

    def get_queue(self, port=None, timeout=None):
        """QueueState или None (порт неизвестен, либо опрос не удался)."""
        p = self._resolve_port(port)
        if p is None:
            return None
        raw = fetch_queue_status(p, timeout=timeout or self.timeout)
        if raw is None:
            return None
        return QueueState(**raw)

    def get_history_ids(self, port=None, timeout=None):
        """set() id всех завершённых заданий, или None. Отдельно от
        get_history() -- нужен только набор id (см. ResourceMonitor,
        подсчёт "готово за сессию" по разнице множеств), и незачем
        каждый раз перекачивать по HTTP полные записи истории (граф,
        outputs) только чтобы посчитать len()/сравнить set()."""
        p = self._resolve_port(port)
        if p is None:
            return None
        return fetch_history_ids(p, timeout=timeout or self.timeout)

    def get_history(self, limit=None, port=None, timeout=None):
        """Список записей /history (каждая -- {"id": prompt_id, **запись
        ComfyUI: prompt/outputs/status}), или None. limit -- вернуть
        только последние N записей (по порядку самого /history, обычно
        хронологический порядок вставки, но ComfyUI это явно не
        гарантирует -- при необходимости точной сортировки по времени
        сортировать по status/status_str или собственным меткам
        вызывающей стороне)."""
        p = self._resolve_port(port)
        if p is None:
            return None
        data = _fetch_json(f"http://127.0.0.1:{p}/history", timeout or self.timeout)
        if data is None:
            return None
        entries = [{"id": prompt_id, **payload} for prompt_id, payload in data.items()]
        if limit is not None:
            entries = entries[-limit:]
        return entries

    def get_system_stats(self, port=None, timeout=None):
        """SystemStats или None. Новый эндпоинт для этапа 6 -- см.
        предупреждение в докстринге SystemStats про непроверенную живьём
        схему ответа."""
        p = self._resolve_port(port)
        if p is None:
            return None
        data = _fetch_json(f"http://127.0.0.1:{p}/system_stats", timeout or self.timeout)
        if data is None:
            return None
        return SystemStats.from_response(data)

    def get_current_workflow(self, port=None, timeout=None):
        """Граф (формат API-графа: {node_id: {class_type, inputs}})
        задания, которое прямо сейчас выполняется, или None -- если
        ComfyUI недоступен, либо сейчас ничего не выполняется, либо
        выполняется больше одного задания одновременно (мульти-GPU) --
        в последнем случае неоднозначно, чей граф возвращать, так что
        осознанно возвращаем None вместо угадывания (тот же принцип
        осторожности, что и у ResourceMonitor._compute_eta_seconds с
        self._current_progress при len(running_ids) != 1)."""
        p = self._resolve_port(port)
        if p is None:
            return None
        data = _fetch_raw_queue(p, timeout or self.timeout)
        if data is None:
            return None
        running_items = data.get("queue_running", [])
        if len(running_items) != 1:
            return None
        item = running_items[0]
        if len(item) <= 2:
            return None
        graph = item[2]
        return graph if isinstance(graph, dict) else None

    def get_object_info(self, node_class=None, port=None, timeout=None):
        """Словарь зарегистрированных нод из /object_info (все ноды, или
        одна -- если передан node_class -- через /object_info/{class}),
        либо None. Тяжёлый эндпоинт (полный /object_info у типичной
        установки ComfyUI с кастомными нодами -- это сотни/тысячи
        записей) -- вызывающей стороне (этап 8 -- "состояние custom
        nodes") стоит кэшировать результат самой, а не опрашивать на
        каждый тик таймера, как get_queue()/get_history_ids()."""
        p = self._resolve_port(port)
        if p is None:
            return None
        path = "/object_info"
        if node_class:
            path += f"/{node_class}"
        return _fetch_json(f"http://127.0.0.1:{p}{path}", timeout or self.timeout)

    def subscribe_websocket(self, callback=None, port=None):
        """Возвращает ComfyWebSocketClient (этап 7 дорожной карты) для
        текущего порта, или None, если порт неизвестен. callback, если
        передан, подключается к событию ComfyWebSocketClient.event_received
        (msg_type: str, payload: dict) -- под конкретные типы событий
        (progress/executing/executed/...) у клиента есть отдельные
        именованные сигналы, см. comfy_ws.py.

        Импорт comfy_ws -- НАМЕРЕННО внутри метода, а не на уровне
        модуля: comfy_ws.py тянет PySide6.QtWebSockets, а сам
        comfy_api.py остаётся Qt-независимым (см. докстринг модуля и
        tests/launcher/test_comfy_api.py, которые не требуют PySide6
        вообще) -- ленивый импорт даёт это только тем вызывающим,
        которым WebSocket-канал реально нужен, не всем, кто просто
        импортирует ComfyAPIClient ради HTTP-методов.

        Возвращённый клиент создаётся в состоянии "не запущен" --
        вызывающая сторона должна вызвать у него .start() сама (см.
        ComfyWebSocketClient.start/stop), чтобы явно управлять его
        жизненным циклом (например, привязать к тому же условию
        "ComfyUI сейчас запущен", что и HTTP-опрос -- см.
        ResourceMonitor)."""
        p = self._resolve_port(port)
        if p is None:
            return None
        from .comfy_ws import ComfyWebSocketClient

        client = ComfyWebSocketClient(p)
        if callback is not None:
            client.event_received.connect(callback)
        return client
