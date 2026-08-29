"""
Минимальный HTTP-клиент к ComfyUI.

Сознательно не переиспользует comfy_api.py из ComfyUIStudio напрямую
(этот инструмент собирается и работает отдельно от Studio, см. задачу
пользователя -- "не добавляя пока в основную студию"), но следует тем
же принципам: чистые функции, None/False при сбое сети вместо
исключений наружу, никакой Qt-зависимости. При интеграции в Studio этот
модуль -- первый кандидат на замену вызовами существующего
ComfyAPIClient.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
import uuid


class ComfyUIError(Exception):
    """Сеть/ComfyUI недоступны или ответили ошибкой -- отдаём наружу
    (в отличие от comfy_api.py в Studio, здесь это FastAPI-бэкенд, у
    которого есть кому вернуть внятный HTTP-статус вызвавшему фронтенду,
    поэтому исключение уместнее тихого None)."""


class ComfyClient:
    def __init__(self, host: str, port: int, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.client_id = str(uuid.uuid4())

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    # -- низкоуровневые хелперы -------------------------------------------------

    def _get(self, path: str, timeout: float = None):
        url = f"{self.base_url}{path}"
        try:
            with urllib.request.urlopen(url, timeout=timeout or self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ComfyUIError(f"GET {path} failed: {exc}") from exc

    def _post(self, path: str, payload: dict, timeout: float = None):
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST", headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                body = resp.read()
                return json.loads(body.decode("utf-8")) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise ComfyUIError(f"POST {path} -> HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ComfyUIError(f"POST {path} failed: {exc}") from exc

    # -- публичный API ------------------------------------------------------

    def is_alive(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.base_url}/", timeout=1.5):
                return True
        except Exception:
            return False

    def queue_prompt(self, graph: dict) -> str:
        """Отправляет граф на выполнение, возвращает prompt_id."""
        result = self._post("/prompt", {"prompt": graph, "client_id": self.client_id})
        prompt_id = result.get("prompt_id")
        if not prompt_id:
            raise ComfyUIError(f"ComfyUI не вернул prompt_id: {result}")
        return prompt_id

    def get_history(self, prompt_id: str):
        """Запись /history для конкретного задания, или None, если его
        там ещё нет (значит либо выполняется, либо ждёт в очереди)."""
        data = self._get(f"/history/{prompt_id}")
        return data.get(prompt_id)

    def get_queue_position(self, prompt_id: str):
        """(is_running, position_in_pending) -- грубая оценка того, где
        сейчас задание, если его ещё нет в /history."""
        data = self._get("/queue")
        running = data.get("queue_running", [])
        pending = data.get("queue_pending", [])
        for item in running:
            if len(item) > 1 and item[1] == prompt_id:
                return True, 0
        for idx, item in enumerate(pending):
            if len(item) > 1 and item[1] == prompt_id:
                return False, idx + 1
        return False, None

    def interrupt(self) -> None:
        self._post("/interrupt", {})

    def free_memory(self, unload_models: bool = True, free_memory: bool = True) -> None:
        """POST /free -- штатный эндпоинт ComfyUI для выгрузки моделей
        из VRAM/RAM без перезапуска сервера."""
        self._post("/free", {"unload_models": unload_models, "free_memory": free_memory})

    def view_image_url(self, filename: str, subfolder: str, image_type: str) -> str:
        """Прямая ссылка на ComfyUI /view -- используется бэкендом
        только для собственного проксирования (см. main.py:/api/image),
        фронтенд эту ссылку никогда не видит напрямую, чтобы не
        зависеть от того, что порт ComfyUI доступен из браузера
        пользователя (может быть скрыт за localhost-only)."""
        query = urllib.parse.urlencode(
            {"filename": filename, "subfolder": subfolder, "type": image_type}
        )
        return f"{self.base_url}/view?{query}"

    def fetch_image_bytes(self, filename: str, subfolder: str, image_type: str):
        url = self.view_image_url(filename, subfolder, image_type)
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                return resp.read(), resp.headers.get("Content-Type", "image/png")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ComfyUIError(f"Не удалось получить изображение: {exc}") from exc

    def get_object_info(self, node_class: str = None):
        path = "/object_info"
        if node_class:
            path += f"/{node_class}"
        return self._get(path)

    def get_combo_options(self, node_class: str, input_name: str) -> list[str]:
        """Список значений, которые ComfyUI реально примет для
        COMBO-входа конкретного узла -- источник истины для любого
        поля, где узел сам диктует допустимые строки (aspect_ratio,
        lora_1 и т.п.), вместо захардкоженного в нашем конфиге списка,
        который рассинхронизируется при любом обновлении узла (ровно
        так и произошло с aspect_ratio: набор строк в ResolutionSelector
        отличался от того, что было в config.json, и ComfyUI отклонял
        задание с value_not_in_list -- а со сканированием папки на диске
        для LoRA вышло наоборот: мы отдавали пути с '/', а ComfyUI на
        Windows ждал '\\', и тоже отклонял с value_not_in_list).

        ComfyUI отдаёт COMBO-вход в одном из ДВУХ форматов в
        зависимости от версии:
          - старый: [ [опция1, опция2, ...], {конфиг} ]
          - новый (типизированный): [ "COMBO", {"options": [опция1, ...], ...} ]
        Раньше код всегда брал combo_input[0] как список -- в новом
        формате это строка "COMBO", и при переборе строки в JS каждый
        символ становился отдельным пунктом выпадающего списка (видно
        было как "C O M B O" вместо реальных значений)."""
        node_info = self._node_info(node_class)
        if not isinstance(node_info, dict):
            return []
        try:
            combo_input = node_info["input"]["required"][input_name]
            first = combo_input[0]
            if isinstance(first, list):
                options = first
            elif isinstance(first, str) and isinstance(combo_input[1], dict):
                options = combo_input[1].get("options", [])
            else:
                options = []
            return [o for o in options if isinstance(o, str)]
        except (KeyError, IndexError, TypeError):
            return []

    def _node_info(self, node_class: str):
        try:
            info = self.get_object_info(node_class)
            node_info = info.get(node_class) if isinstance(info, dict) else None
        except ComfyUIError:
            node_info = None
        if isinstance(node_info, dict):
            return node_info
        # Некоторые кастомные узлы не отвечают на точечный
        # /object_info/<class> (404 или пустой/некорректный ответ), но
        # присутствуют в полном дампе /object_info -- пробуем его как
        # запасной путь, прежде чем сдаваться.
        full = self.get_object_info()
        return full.get(node_class) if isinstance(full, dict) else None
