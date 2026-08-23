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


def _resolve_single_literal_input(prompt_dict, node_id):
    """Хелпер для count_steps_in_prompt ниже -- если узел node_id имеет
    РОВНО один числовой (не ссылку на другой узел) собственный вход,
    считаем его тем самым "константным" значением, которое эта нода
    отдаёт наружу (типичный случай -- ноды-примитивы вида "Value"/
    "PrimitiveInt"/пользовательские константы с одним полем). Если
    числовых входов 0 или больше 1 -- не гадаем, возвращаем None (не
    уверены, какой из них "тот самый")."""
    node = prompt_dict.get(str(node_id)) if prompt_dict else None
    if not isinstance(node, dict):
        return None
    inputs = node.get("inputs")
    if not isinstance(inputs, dict):
        return None
    numeric_values = [
        v for v in inputs.values() if isinstance(v, (int, float)) and not isinstance(v, bool)
    ]
    if len(numeric_values) == 1:
        return numeric_values[0]
    return None


def count_steps_in_prompt(prompt_dict):
    """Сумма числового поля "steps" по всем узлам графа задания (формат
    API-графа: {node_id: {class_type, inputs}}) — грубая, но рабочая
    эвристика объёма работы для KSampler/KSamplerAdvanced и большинства
    кастомных сэмплеров с тем же именем входа.

    Если steps приходит НЕ числом, а ссылкой на другой узел
    (["node_id", output_index] -- обычный формат связи в API-графе
    ComfyUI), пробуем РОВНО ОДИН шаг разрешения через
    _resolve_single_literal_input: помогает для узлов-примитивов
    ("Value"/"PrimitiveInt"/подобные константы), у которых итоговое
    число и так лежит прямо в их собственных inputs как обычное
    значение. Дальше вглубь графа не идём (оценка и так приблизительная,
    полноценный обход был бы избыточен).

    ВАЖНО, чего этот шаг разрешения принципиально НЕ может починить:
    ноду, которая вычисляет steps ВО ВРЕМЯ выполнения (например,
    "случайное число шагов") -- /history и /queue отдают ровно тот
    граф, который был ОТПРАВЛЕН на выполнение (структуру узлов и
    связей), а не то, что эти узлы посчитали в процессе работы; итоговое
    вычисленное значение нигде в ответах API не сохраняется и
    недоступно постфактум ни для истории, ни для очереди -- в этом
    случае total корректно остаётся заниженным/нулевым по этому узлу,
    это не баг разбора, а отсутствие самих данных на стороне ComfyUI."""
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
            elif (
                isinstance(v, (list, tuple))
                and len(v) == 2
                and isinstance(v[0], (str, int))
            ):
                resolved = _resolve_single_literal_input(prompt_dict, v[0])
                if resolved is not None:
                    total += resolved
    return total


def format_history_status(entry: dict) -> str:
    """Человекочитаемый статус записи /history (этап 8, диалог "Очередь
    и история" -- ui/widgets/queue_history_dialog.py). Формат поля
    "status" не зафиксирован жёстко ComfyUI между версиями (то же
    соображение, что и у SystemStats.from_response выше), поэтому
    берём через .get() с запасными значениями, а не полагаемся на
    конкретную структуру."""
    status = entry.get("status") if isinstance(entry, dict) else None
    if isinstance(status, dict):
        status_str = status.get("status_str")
        if status_str:
            return str(status_str)
        if status.get("completed") is True:
            return "success"
        if status.get("completed") is False:
            return "error"
    return "?"


def count_history_outputs(entry: dict) -> int:
    """Число изображений в outputs записи /history (этап 8)."""
    outputs = entry.get("outputs") if isinstance(entry, dict) else None
    if not isinstance(outputs, dict):
        return 0
    total = 0
    for node_outputs in outputs.values():
        if isinstance(node_outputs, dict):
            images = node_outputs.get("images")
            if isinstance(images, list):
                total += len(images)
    return total


def history_entry_graph(entry: dict):
    """Граф задания из записи /history, если он там есть (этап 8) --
    /history отдаёт его либо напрямую под ключом "prompt" словарём
    узлов, либо (в части версий ComfyUI) в формате
    [number, prompt_id, prompt_dict, ...], как и элементы /queue -- см.
    _fetch_raw_queue/get_current_workflow ниже. Оба случая встречаются
    в дикой природе -- не проверено вживую, см. общее ограничение
    песочницы разработки (нет запущенного ComfyUI под рукой), поэтому
    разбираем оба варианта защитно, а не полагаемся на один из них."""
    prompt = entry.get("prompt") if isinstance(entry, dict) else None
    if isinstance(prompt, dict):
        return prompt
    if isinstance(prompt, (list, tuple)) and len(prompt) > 2 and isinstance(prompt[2], dict):
        return prompt[2]
    return None


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


def _post_json(url, payload, timeout):
    """POST JSON-тело (или пустой POST, если payload is None -- нужно
    для /interrupt, у которого тела нет вообще, см. api.js/api.ts
    самого ComfyUI_frontend: #postItem(type, body) с body=None для
    interrupt). Возвращает True на HTTP 2xx, False на любой сбой
    (недоступный порт, таймаут, не-2xx ответ) -- тот же принцип "None/
    False значит просто не получилось сейчас", что и у _fetch_json,
    вызывающая сторона (этап 8 -- управление очередью/историей) не
    обязана различать разные причины отказа."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else b""
    req = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            return 200 <= status < 300
    except Exception:
        return False


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
    опрашивался нигде до этого этапа). Схема сверена с официальной
    документацией ComfyUI (docs.comfy.org, "Get system statistics") и с
    независимыми примерами использования: system.{os, python_version,
    embedded_python, comfyui_version, pytorch_version, ram_total,
    ram_free, ...}, devices: [{name, type, vram_total, vram_free,
    torch_vram_total?, torch_vram_free?}] (байты). Поля vram_total/
    vram_free задокументированы официально; torch_vram_* встречаются в
    сторонних примерах для локального сервера, но не в официальной
    cloud-документации -- используем их не как контракт, а как
    опциональный бонус (см. device_vram_usage ниже, который полагается
    только на vram_total/vram_free). raw хранит весь ответ целиком --
    остальные поля (argv, comfyui_frontend_version, index устройства и
    т.п.) доступны через него, а не раздуваются в дополнительные
    именованные поля заранее."""
    os: str = None
    python_version: str = None
    pytorch_version: str = None
    comfyui_version: str = None
    embedded_python: bool = None
    ram_total: float = None
    ram_free: float = None
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
            pytorch_version=system.get("pytorch_version"),
            comfyui_version=system.get("comfyui_version"),
            embedded_python=system.get("embedded_python"),
            ram_total=system.get("ram_total"),
            ram_free=system.get("ram_free"),
            devices=devices if isinstance(devices, list) else [],
            raw=data if isinstance(data, dict) else {},
        )


def device_vram_usage(device):
    """(used_bytes, total_bytes, percent_used) для одного элемента
    SystemStats.devices (этап 8, диалог "Модели и VRAM"), или None, если
    устройство не сообщает vram_total/vram_free в ожидаемом формате --
    опираемся только на эти два задокументированных поля (см. докстринг
    SystemStats выше), не на torch_vram_*, которые не гарантированы."""
    if not isinstance(device, dict):
        return None
    total = device.get("vram_total")
    free = device.get("vram_free")
    if not isinstance(total, (int, float)) or isinstance(total, bool):
        return None
    if not isinstance(free, (int, float)) or isinstance(free, bool):
        return None
    if total <= 0:
        return None
    used = max(total - free, 0)
    percent = (used / total) * 100
    return used, total, percent


def summarize_object_info(info):
    """Список [{"class_type", "display_name", "category"}] из ответа
    /object_info (этап 8, диалог "Custom nodes"), отсортированный по
    class_type. "category" -- стандартное, стабильное поле схемы
    ComfyUI (используется самим фронтендом для меню добавления нод),
    берём его как единственный надёжный группирующий признак:
    /object_info НЕ сообщает, из какого custom_nodes-пакета пришла
    конкретная нода (в отличие от того, что можно было бы предположить
    по логам ComfyUI-Manager при старте) -- такого поля в стандартной
    схеме ответа нет, поэтому мы её не показываем, а не потому что не
    распарсили."""
    if not isinstance(info, dict):
        return []
    result = []
    for class_type, meta in info.items():
        meta = meta if isinstance(meta, dict) else {}
        result.append(
            {
                "class_type": class_type,
                "display_name": meta.get("display_name") or class_type,
                "category": meta.get("category") or "",
            }
        )
    result.sort(key=lambda item: item["class_type"])
    return result


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

    def get_queue_items(self, port=None, timeout=None):
        """Структурированный СПИСОК заданий очереди (этап 8 дорожной
        карты -- "очередь задач" в UI), в отличие от QueueState выше,
        который отдаёт только агрегаты (нужен ResourceBar/ETA, не
        трогается этим методом). Каждый элемент:
        {"prompt_id", "status" ("running"/"pending"), "steps"} -- в
        порядке running (сначала) + pending, как их и отдаёт сам /queue
        ComfyUI. Или None, если порт неизвестен/опрос не удался.

        Переиспользует _fetch_raw_queue (тот же сырой /queue, что и
        get_current_workflow) и count_steps_in_prompt -- не дублирует
        разбор графа, который уже есть в fetch_queue_status."""
        p = self._resolve_port(port)
        if p is None:
            return None
        data = _fetch_raw_queue(p, timeout or self.timeout)
        if data is None:
            return None

        def _mk(item, status):
            prompt_id = item[1] if len(item) > 1 else None
            graph = item[2] if len(item) > 2 and isinstance(item[2], dict) else None
            return {
                "prompt_id": prompt_id,
                "status": status,
                "steps": count_steps_in_prompt(graph) if graph else 0,
            }

        running = [_mk(it, "running") for it in data.get("queue_running", [])]
        pending = [_mk(it, "pending") for it in data.get("queue_pending", [])]
        return running + pending

    def delete_queue_item(self, prompt_id, port=None, timeout=None) -> bool:
        """Снимает ОДНО ожидающее задание из очереди по prompt_id (POST
        /queue {"delete": [id]} -- та же форма запроса, что и у
        собственного фронтенда ComfyUI, см. api.ts:#postItem/deleteItem).
        Возвращает False, если порт неизвестен или запрос не удался --
        не различает "уже не в очереди" от "сеть недоступна", вызывающей
        стороне обычно достаточно просто обновить список после вызова
        и посмотреть, пропало ли задание."""
        p = self._resolve_port(port)
        if p is None:
            return False
        return _post_json(
            f"http://127.0.0.1:{p}/queue", {"delete": [prompt_id]}, timeout or self.timeout
        )

    def clear_queue(self, port=None, timeout=None) -> bool:
        """Полностью очищает ОЖИДАЮЩУЮ часть очереди (POST /queue
        {"clear": true}) -- задание, которое уже выполняется, этим не
        прерывается (для этого -- interrupt() ниже)."""
        p = self._resolve_port(port)
        if p is None:
            return False
        return _post_json(f"http://127.0.0.1:{p}/queue", {"clear": True}, timeout or self.timeout)

    def interrupt(self, port=None, timeout=None) -> bool:
        """Прерывает ТЕКУЩЕЕ выполняемое задание (POST /interrupt, без
        тела -- см. api.ts:#postItem('interrupt', null)). Не трогает
        остальную (ожидающую) очередь."""
        p = self._resolve_port(port)
        if p is None:
            return False
        return _post_json(f"http://127.0.0.1:{p}/interrupt", None, timeout or self.timeout)

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

    def delete_history_item(self, prompt_id, port=None, timeout=None) -> bool:
        """Удаляет ОДНУ запись из /history по prompt_id (POST /history
        {"delete": [id]})."""
        p = self._resolve_port(port)
        if p is None:
            return False
        return _post_json(
            f"http://127.0.0.1:{p}/history", {"delete": [prompt_id]}, timeout or self.timeout
        )

    def clear_history(self, port=None, timeout=None) -> bool:
        """Полностью очищает /history (POST /history {"clear": true})."""
        p = self._resolve_port(port)
        if p is None:
            return False
        return _post_json(f"http://127.0.0.1:{p}/history", {"clear": True}, timeout or self.timeout)

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

    def subscribe_websocket(self, callback=None, port=None, client_id=None):
        """Возвращает ComfyWebSocketClient (этап 7 дорожной карты) для
        текущего порта, или None, если порт неизвестен. callback, если
        передан, подключается к событию ComfyWebSocketClient.event_received
        (msg_type: str, payload: dict) -- под конкретные типы событий
        (progress/executing/executed/...) у клиента есть отдельные
        именованные сигналы, см. comfy_ws.py.

        client_id -- ОПАСНО передавать сюда реальный clientId уже
        живой страницы (встроенного браузера): на практике это
        вытесняет её собственное WS-соединение из реестра сокетов
        ComfyUI (сервер удаляет старую запись при подключении нового
        клиента с уже занятым id) и ломает саму страницу -- см.
        подробный разбор в ui/browser_page.py (у _EVENT_BRIDGE_JS) и в
        comfy_ws.py. Из-за этого этап 8 в итоге получает
        progress/executing/executed НЕ через этот параметр, а через
        отдельный JS-мост внутри уже открытой страницы (см.
        ResourceMonitor.feed_ws_event). Параметр оставлен как общая
        возможность API-слоя (например, для активной отправки СВОИХ
        заданий с заранее выбранным id), а не для пассивного
        подслушивания чужой сессии. Если не передан (None, по
        умолчанию) -- ComfyWebSocketClient сам сгенерирует
        свежий, ни с кем не пересекающийся uuid4 (безопасно).

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

        client = ComfyWebSocketClient(p, client_id=client_id)
        if callback is not None:
            client.event_received.connect(callback)
        return client
