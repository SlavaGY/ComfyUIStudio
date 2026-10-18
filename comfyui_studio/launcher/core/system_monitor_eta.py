"""Прогресс из stdout ComfyUI и оценка времени очереди.

stdout и WebSocket независимо обновляют общее состояние: подключённый
WebSocket может не получать прогресс заданий, отправленных другим clientId."""

import re

from .logging_setup import log
from .system_monitor_base import ResourceMonitorBase

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# Строка прогресс-бара сэмплера ComfyUI (tqdm), например:
# "74%|███████▍         | 26/35 [00:24<00:07, 1.21it/s]" -- см.
# ResourceMonitor.feed_log_line. Единица скорости бывает "it/s" (шагов в
# секунду) или, на медленных шагах, "s/it" (секунд на шаг) -- у tqdm это
# переключается автоматически в зависимости от того, что читабельнее.
_TQDM_PROGRESS_RE = re.compile(
    r"(?P<cur>\d+)/(?P<total>\d+)\s*"
    r"\[[^<\]]*<[^,\]]*,\s*(?P<rate>[\d.]+)\s*(?P<unit>it/s|s/it)\]"
)



class EtaMixin(ResourceMonitorBase):
    def feed_log_line(self, line: str):
        """Обновляет прогресс и скорость шага по строке tqdm из stdout.

        Скорость it/s переводится в секунды на шаг; s/it используется напрямую.
        Результат tqdm уже сглажен, дополнительное сглаживание не применяется.
        Парсинг работает и при подключённом WebSocket: события progress могут
        быть адресованы clientId браузера и не доходить до клиента монитора."""
        clean = _ANSI_ESCAPE_RE.sub("", line)
        m = _TQDM_PROGRESS_RE.search(clean)
        if not m:
            return
        try:
            cur = int(m.group("cur"))
            total = int(m.group("total"))
            rate = float(m.group("rate"))
        except (TypeError, ValueError):
            return
        if total <= 0 or rate <= 0:
            return

        self._current_progress = {"done": cur, "total": total}
        sec_per_step = rate if m.group("unit") == "s/it" else (1.0 / rate)
        self._avg_sec_per_step = sec_per_step

        job_key = self._progress_for_id or "?"
        if job_key not in self._logged_progress_for_ids:
            # Подтверждение в лог для КАЖДОГО задания отдельно -- чтобы
            # было видно, доходит ли реальный прогресс до каждого из них
            # по очереди, а не только до первого/последнего.
            log.info(
                "ETA очереди: поймана первая строка прогресса для %s (%s/%s, %.2f%s)",
                job_key, cur, total, rate, m.group("unit"),
            )
            self._logged_progress_for_ids.add(job_key)


    def _compute_eta_seconds(self, step_totals, running_ids):
        """0.0 -- в очереди реально ничего нет. None -- в очереди что-то
        есть, но посчитать нельзя: либо объём хотя бы одного задания
        неизвестен (эвристика по графу не нашла "steps", а строка
        прогресса ещё не пришла), либо скорость шага ещё не замерена
        (ни одной строки прогресса не было с момента запуска ComfyUI)."""
        if not step_totals:
            return 0.0

        # tqdm в консоли ComfyUI не сообщает prompt_id -- только текущий
        # прогресс ОДНОГО считающего сэмплера. Если сейчас выполняется
        # ровно одно задание, однозначно относим self._current_progress
        # к нему; если их несколько (мультиGPU) или ни одного -- не
        # рискуем угадать какое, используем только графовую эвристику.
        progress = self._current_progress if len(running_ids) == 1 else None
        progress_pid = next(iter(running_ids)) if progress else None

        # Объём (total) для каждого задания -- либо реальный (граф или
        # tqdm), либо, если оба не дали ничего, None ("не знаем").
        totals = {}
        for prompt_id, graph_total in step_totals.items():
            if prompt_id == progress_pid:
                total = max(graph_total, progress["total"])
            else:
                total = graph_total
            totals[prompt_id] = total if total > 0 else None

        # Неизвестный объём задания оцениваем средним известных объёмов
        # этой очереди: workflow не всегда содержит распознаваемые steps.
        # Если ориентиров нет вообще, ETA остаётся неизвестным.
        known = [t for t in totals.values() if t is not None]
        fallback_total = round(sum(known) / len(known)) if known else None

        remaining = 0
        for prompt_id, total in totals.items():
            if total is None:
                if fallback_total is None:
                    # Совсем без ориентиров (обычно только самое первое
                    # задание сессии, ещё до первой строки прогресса) --
                    # тут уже честно "не знаем" для всей очереди.
                    return None
                total = fallback_total
            done = (
                progress["done"] if prompt_id == progress_pid else 0
            )
            remaining += max(total - done, 0)

        if remaining <= 0:
            return 0.0
        if self._avg_sec_per_step is None:
            return None
        return remaining * self._avg_sec_per_step
