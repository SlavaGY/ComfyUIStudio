"""
promptgen_job.py
Состояние одной задачи генератора промптов (Job), очистка ответа модели
от «рассуждений», подсчёт токенов и сборка записи для истории запросов.

Вынесено из promptgen.py на этапе R10 плана рефакторинга без изменения
кода. Здесь нет ни процессов, ни сети, ни Qt: только данные задачи и
чистые функции над ними.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
import uuid
from typing import Optional

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"<think>", re.IGNORECASE)


class Job:
    ACTIVE_STATES = ("loading", "generating")

    def __init__(self):
        self.id = uuid.uuid4().hex[:12]
        self.state = "loading"       # loading | generating | done | error | cancelled
        self.error: Optional[str] = None
        self.tokens = 0
        self.thinking = False
        self.truncated = False
        self.warnings: list[dict] = []
        self.timings: Optional[dict] = None   # блок timings из последнего SSE-чанка llama-server
        self.marks: dict[str, float] = {"created": time.monotonic()}
        self.created_wall = time.time()          # для истории: время начала в unix-секундах
        self.finished_wall: Optional[float] = None
        self.usage: Optional[dict] = None        # блок usage из последнего SSE-чанка (stream_options.include_usage)
        # что именно было запрошено (заполняется в _run, пишется в историю)
        self.prefix = ""
        self.user_text = ""
        self.full_prompt = ""
        self.has_image = False
        self.image_name = ""
        self.finished: Optional[float] = None
        self.cancel_event = threading.Event()
        self.proc: Optional[subprocess.Popen] = None
        self.thread: Optional[threading.Thread] = None
        self._raw = ""
        self._final: Optional[str] = None
        self._lock = threading.Lock()

    @property
    def created(self) -> float:
        return self.marks["created"]

    @property
    def active(self) -> bool:
        return self.state in self.ACTIVE_STATES

    # -- изменение состояния ------------------------------------------------

    def mark(self, name: str) -> None:
        self.marks.setdefault(name, time.monotonic())

    def set_state(self, state: str) -> None:
        with self._lock:
            if self.active:
                self.state = state

    def add_warning(self, warning: dict) -> None:
        with self._lock:
            self.warnings.append(warning)

    def add_content(self, piece: str) -> None:
        with self._lock:
            self._raw += piece
            self.tokens += 1
            # «думает», пока последний открывающий тег не закрыт
            low = self._raw.lower()
            self.thinking = low.rfind("<think>") > low.rfind("</think>")

    def note_reasoning(self) -> None:
        with self._lock:
            self.tokens += 1
            self.thinking = True

    def finish_ok(self, text: str) -> None:
        with self._lock:
            self._final = text
            self.state = "done"
            self.thinking = False
            self.finished = time.monotonic()
            self.finished_wall = time.time()

    def finish_error(self, message: str) -> None:
        with self._lock:
            self.error = message
            self.state = "error"
            self.finished = time.monotonic()
            self.finished_wall = time.time()

    def finish_cancelled(self) -> None:
        with self._lock:
            self.state = "cancelled"
            self.finished = time.monotonic()
            self.finished_wall = time.time()

    # -- чтение -------------------------------------------------------------

    def raw_text(self) -> str:
        with self._lock:
            return self._raw

    def snapshot(self) -> dict:
        with self._lock:
            text = self._final if self._final is not None else clean_output(self._raw)
            end = self.finished if self.finished is not None else time.monotonic()
            return {
                "job_id": self.id,
                "state": self.state,
                "text": text,
                "tokens": self.tokens,
                "thinking": self.thinking,
                "truncated": self.truncated,
                "warnings": list(self.warnings),
                "error": self.error,
                "elapsed": round(end - self.created, 1),
            }


def clean_output(raw: str) -> str:
    """Убирает «рассуждения» из ответа: закрытые <think>…</think> блоки, а
    также незакрытый <think>… (обрыв по лимиту токенов) -- всё после
    открывающего тега считается рассуждением, а не ответом."""
    text = _THINK_BLOCK_RE.sub("", raw)
    match = _THINK_OPEN_RE.search(text)
    if match:
        text = text[: match.start()]
    return text.strip()


def token_counts(job: "Job") -> tuple[Optional[int], Optional[int], Optional[int], str]:
    """(запрос, ответ, всего, источник). Приоритет: usage от llama-server
    -> блок timings (prompt_n + cache_n / predicted_n) -> число принятых
    SSE-чанков (≈ токенов ответа; запрос неизвестен)."""
    def num(value):
        return int(value) if isinstance(value, (int, float)) else None

    u = job.usage
    if u and num(u.get("completion_tokens")) is not None:
        prompt, completion = num(u.get("prompt_tokens")), num(u.get("completion_tokens"))
        total = num(u.get("total_tokens"))
        if total is None and prompt is not None:
            total = prompt + completion
        return prompt, completion, total, "usage"
    t = job.timings
    if t and num(t.get("predicted_n")) is not None:
        prompt_n = num(t.get("prompt_n"))
        prompt = None if prompt_n is None else prompt_n + (num(t.get("cache_n")) or 0)
        completion = num(t.get("predicted_n"))
        total = None if prompt is None else prompt + completion
        return prompt, completion, total, "timings"
    if job.tokens:
        return None, job.tokens, None, "chunks"
    return None, None, None, ""


def history_record(job: "Job", cfg: dict) -> dict:
    """Запись для базы истории (см. promptgen_history.py) по итогам задачи."""
    m = job.marks
    end = job.finished if job.finished is not None else time.monotonic()

    def span(a, b):
        return round(m[b] - m[a], 3) if a in m and b in m else None

    load_s = span("spawned", "ready")
    ttft_s = span("ready", "first_token")
    gen_s = span("first_token", "generated")
    prompt_tokens, completion_tokens, total_tokens, source = token_counts(job)
    rate = None
    t = job.timings
    if t and t.get("predicted_per_second"):
        rate = round(float(t["predicted_per_second"]), 2)  # скорость из самого llama-server
    elif gen_s and completion_tokens:
        rate = round(completion_tokens / gen_s, 2)

    with job._lock:
        final = job._final
        raw = job._raw
        state, error = job.state, job.error
    output = final if final is not None else clean_output(raw)
    return {
        "job_id": job.id,
        "started_at": job.created_wall,
        "finished_at": job.finished_wall or time.time(),
        "state": state,
        "error": error or "",
        "prefix": job.prefix,
        "user_text": job.user_text,
        "full_prompt": job.full_prompt,
        "has_image": job.has_image,
        "image_name": job.image_name,
        "output_text": output,
        "raw_output": raw if raw and raw.strip() != output else "",
        "truncated": job.truncated,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "tokens_source": source,
        "total_s": round(end - job.created, 3),
        "load_s": load_s,
        "ttft_s": ttft_s,
        "gen_s": gen_s,
        "tok_per_s": rate,
        "model_name": os.path.basename(cfg.get("model_path") or ""),
        "mmproj_name": os.path.basename(cfg.get("mmproj_path") or ""),
        "ctx_size": cfg.get("ctx_size"),
    }
