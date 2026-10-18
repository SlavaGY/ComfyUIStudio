"""HTTP-клиент ComfyUI: готовность, очередь, история и системные метрики.

Модуль использует urllib/json и не зависит от Qt. WebSocket-клиент
импортируется лениво в subscribe_websocket.

Порт задаётся в конструкторе или явно в вызове; явное значение имеет
приоритет. Разрешение текущего порта остаётся у вызывающего кода,
чтобы отличать остановленный процесс от ошибки сетевого запроса."""

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
    """GET с разбором JSON; None означает сетевую ошибку или ошибку разбора."""
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
# Типизированные результаты
# --------------------------------------------------------------------------

@dataclass
class QueueState:
    """Число выполняемых и ожидающих заданий, их идентификаторы и объёмы шагов."""
    running: int
    pending: int
    running_ids: set
    step_totals: dict


@dataclass
class SystemStats:
    """Системные сведения и устройства из /system_stats ComfyUI.

    Именованные поля содержат используемые клиентом сведения; полный ответ
    доступен в raw. Отсутствующие и отличающиеся между версиями поля
    обрабатываются через .get() и проверку типов в from_response."""
    os: str | None = None
    python_version: str | None = None
    comfyui_version: str | None = None
    embedded_python: bool | None = None
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
    """HTTP-фасад для проверки готовности ComfyUI, очереди, истории,
    системных сведений, текущего workflow и зарегистрированных нод.

    WebSocket-клиент создаётся отдельно через ленивый subscribe_websocket;
    HTTP-методы остаются доступны без Qt."""

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
        """Возвращает SystemStats или None, если порт неизвестен или опрос не удался."""
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
        """Возвращает словарь зарегистрированных нод или None при недоступности.

        node_class ограничивает запрос одной нодой. Полный /object_info может быть
        большим; вызывающей стороне следует кэшировать результат и не запрашивать
        его на каждом тике таймера."""
        p = self._resolve_port(port)
        if p is None:
            return None
        path = "/object_info"
        if node_class:
            path += f"/{node_class}"
        return _fetch_json(f"http://127.0.0.1:{p}{path}", timeout or self.timeout)

    def subscribe_websocket(self, callback=None, port=None):
        """Создаёт незапущенный ComfyWebSocketClient для выбранного порта.

        Если порт неизвестен, возвращает None. callback подключается к общему
        сигналу event_received(str, dict); конкретные события также имеют отдельные
        сигналы. Вызывающая сторона управляет start()/stop() и временем жизни
        клиента. Ленивый импорт сохраняет независимость HTTP-клиента от Qt."""
        p = self._resolve_port(port)
        if p is None:
            return None
        from .comfy_ws import ComfyWebSocketClient

        client = ComfyWebSocketClient(p)
        if callback is not None:
            client.event_received.connect(callback)
        return client
