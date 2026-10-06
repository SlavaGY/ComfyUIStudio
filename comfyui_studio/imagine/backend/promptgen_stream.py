"""
promptgen_stream.py
Обмен с запущенным llama-server по HTTP: POST /v1/chat/completions со
стримингом SSE и разбор обрыва соединения.

Вынесено из PromptGenerator на этапе R10 плана рефакторинга: методы
_stream_completion и _lost_connection_message стали функциями без self
(состояния генератора они не использовали). Поведение отмены (_Cancelled)
не менялось.
"""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Optional

from comfyui_studio.imagine.backend.promptgen_base import (
    MAX_TOKENS, STREAM_TIMEOUT_S, PromptGenError, _Cancelled, _error_text,
)
from comfyui_studio.imagine.backend.promptgen_process import _exit_message

if TYPE_CHECKING:
    from comfyui_studio.imagine.backend.promptgen_job import Job

# Запросы к llama-server идут строго на localhost -- в обход системного
# прокси. urllib по умолчанию берёт прокси из переменных окружения и (на
# Windows) из реестра, а правило «<local>» в реестре НЕ считает локальным
# адрес вида 127.0.0.1 -- с включённым системным прокси (Clash, v2ray и
# т. п.) health-check и запрос ушли бы в прокси и не дошли бы до сервера.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def stream_completion(
    job: "Job", port: int, prompt: str, image: Optional[str], proc, log_path: str
) -> None:
    if image:
        content = [
            {"type": "image_url", "image_url": {"url": image}},
            {"type": "text", "text": prompt},
        ]
    else:
        content = prompt
    body = {
        "model": "local",
        "messages": [{"role": "user", "content": content}],
        "max_tokens": MAX_TOKENS,
        "stream": True,
        # финальный чанк с точным числом токенов (usage) -- для истории
        "stream_options": {"include_usage": True},
        # Qwen3 и похожие: не «думать» перед ответом. Модели без
        # такого параметра шаблона его игнорируют.
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    complete = False  # получен ли штатный конец потока ([DONE] / finish_reason)
    try:
        with _opener.open(req, timeout=STREAM_TIMEOUT_S) as resp:
            for raw_line in resp:
                if job.cancel_event.is_set():
                    raise _Cancelled()
                line = raw_line.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    complete = True
                    break
                try:
                    obj = json.loads(payload)
                except ValueError:
                    continue
                if isinstance(obj, dict) and obj.get("error"):
                    raise PromptGenError(f"llama-server: {_error_text(obj['error'])}", 502)
                if isinstance(obj.get("timings"), dict):
                    job.timings = obj["timings"]
                if isinstance(obj.get("usage"), dict):
                    job.usage = obj["usage"]
                choices = obj.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    job.mark("first_token")
                    job.add_content(delta["content"])
                elif delta.get("reasoning_content"):
                    job.mark("first_token")
                    job.note_reasoning()
                if choice.get("finish_reason"):
                    complete = True
                    if choice["finish_reason"] == "length":
                        job.truncated = True
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = _error_text(json.loads(exc.read().decode("utf-8", "replace")).get("error"))
        except (OSError, ValueError, AttributeError, http.client.HTTPException):
            # тело не прочиталось / не JSON / JSON не объект -- деталей не будет
            pass
        raise PromptGenError(
            f"llama-server вернул ошибку {exc.code}" + (f": {detail}" if detail else "") + ".",
            502,
        ) from exc
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as exc:
        if job.cancel_event.is_set():
            raise _Cancelled() from exc
        raise PromptGenError(
            lost_connection_message(proc, log_path, f"{exc}"), 502
        ) from exc

    # Поток мог закончиться "тихо": отмена (cancel/shutdown убили
    # процесс) либо llama-server упал на середине (например, нехватка
    # VRAM) -- в обоих случаях обрубок текста НЕ должен выдаваться за
    # готовый результат.
    if job.cancel_event.is_set():
        raise _Cancelled()
    if not complete:
        raise PromptGenError(lost_connection_message(proc, log_path, "поток ответа оборвался"), 502)


def lost_connection_message(proc, log_path: str, reason: str) -> str:
    # даём процессу мгновение завершиться, чтобы увидеть код выхода и
    # последние строки его вывода (там обычно и написана причина)
    for _ in range(10):
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    if proc.poll() is not None:
        time.sleep(0.2)  # дочитать вывод
        return _exit_message(proc, log_path, "во время генерации")
    return f"Соединение с llama-server потеряно: {reason}"
