"""
Опрос ComfyUI по HTTP: доступность порта, состояние очереди, история.

Вынесено из comfyui_launcher.py (этап 1 дорожной карты). Эти функции —
зачаток HTTP-слоя, который на этапе 6 дорожной карты (HTTP API
abstraction) станет полноценным классом ComfyAPIClient; здесь пока
сохраняется исходное поведение "функции модуля", без изменений логики.
"""

import json
import urllib.request
import urllib.error


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
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/queue", timeout=timeout
        ) as resp:
            data = json.loads(resp.read().decode("utf-8"))
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
    except Exception:
        return None


def fetch_history_ids(port, timeout=1.5):
    """Возвращает set() prompt_id всех записей /history ComfyUI, или None,
    если недоступно. /history — собственный журнал ComfyUI обо всех
    запросах, которые ДОЕХАЛИ до конца (успешно или с ошибкой; висящие в
    очереди туда не попадают, добавляются туда только после завершения
    исполнения — см. execution.py/PromptQueue.task_done в самом
    ComfyUI)."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/history", timeout=timeout
        ) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return set(data.keys())
    except Exception:
        return None

