"""
Очередь заданий приложения RVC -- только stdlib.

Одно задание за раз (GPU один; два пайплайна RVC параллельно упёрлись бы в
VRAM), остальные ждут в очереди. Сами пайплайны ultimate_rvc монолитные --
их нельзя прервать «снаружи», поэтому отмена работает так:

  * задание в очереди -- снимается сразу;
  * задание выполняется -- ставится флаг, и ближайший вызов report()
    (пайплайн дёргает его на каждом этапе: «разделение», «конвертация» ...)
    бросает JobCancelled. Отмена, таким образом, срабатывает на границе
    этапов, а не мгновенно; внутри одного долгого этапа задание доработает
    до его конца.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections import deque

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
ERROR = "error"
CANCELLED = "cancelled"

FINISHED = (DONE, ERROR, CANCELLED)


class JobCancelled(Exception):
    """Бросается из report(), когда пользователь отменил выполняющееся задание."""


class Job:
    def __init__(self, job_id, kind, title, params):
        self.id = job_id
        self.kind = kind
        self.title = title
        self.params = params
        self.status = QUEUED
        self.stage = ""
        self.progress = 0.0
        self.files = []  # [{"label": str, "path": str}], первый -- основной результат
        self.error = ""
        self.created = time.time()
        self.started = None
        self.finished = None
        self.cancel_requested = False

    def snapshot(self):
        """Данные для JSON: пути на диске наружу не отдаются, только индекс
        файла (скачивание -- /api/jobs/<id>/files/<i>)."""
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "error": self.error,
            "files": [
                {"index": i, "label": f["label"], "name": f.get("name") or ""}
                for i, f in enumerate(self.files)
            ],
            "created": self.created,
            "started": self.started,
            "finished": self.finished,
            "cancel_requested": self.cancel_requested,
        }


class JobManager:
    def __init__(self, runner, max_history=50, autostart=True):
        """runner(job, report) -> список файлов результата [{"label","path"}].
        report(progress=None, stage=None, check_cancel=True)."""
        self._runner = runner
        self._max_history = max_history
        self._ids = itertools.count(1)
        self._jobs = {}
        self._queue = deque()
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._stopping = False
        self._thread = None
        if autostart:
            self.start()

    # -- жизненный цикл -----------------------------------------------------

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="rvc-jobs", daemon=True)
            self._thread.start()

    def shutdown(self):
        with self._wake:
            self._stopping = True
            self._wake.notify_all()

    # -- API ------------------------------------------------------------------

    def submit(self, kind, title, params):
        with self._wake:
            job = Job(next(self._ids), kind, title, params)
            self._jobs[job.id] = job
            self._queue.append(job.id)
            self._trim_history()
            self._wake.notify()
            return job

    def get(self, job_id):
        with self._lock:
            return self._jobs.get(job_id)

    def list(self):
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.id, reverse=True)

    def cancel(self, job_id):
        """True, если отмена принята (задание снято или помечено)."""
        with self._wake:
            job = self._jobs.get(job_id)
            if job is None or job.status in FINISHED:
                return False
            if job.status == QUEUED:
                self._queue.remove(job.id)
                job.status = CANCELLED
                job.finished = time.time()
                return True
            job.cancel_requested = True
            return True

    def busy_models(self):
        """Имена моделей, занятых активными заданиями (нельзя удалять)."""
        with self._lock:
            return {
                j.params.get("model_name")
                for j in self._jobs.values()
                if j.status in (QUEUED, RUNNING) and j.params.get("model_name")
            }

    # -- внутреннее ---------------------------------------------------------------

    def _trim_history(self):
        finished = [j for j in self._jobs.values() if j.status in FINISHED]
        finished.sort(key=lambda j: j.id)
        for job in finished[: max(0, len(finished) - self._max_history)]:
            del self._jobs[job.id]

    def _loop(self):
        while True:
            with self._wake:
                while not self._queue and not self._stopping:
                    self._wake.wait()
                if self._stopping:
                    return
                job = self._jobs[self._queue.popleft()]
                job.status = RUNNING
                job.started = time.time()
            self._run(job)

    def _run(self, job):
        def report(progress=None, stage=None, check_cancel=True):
            if progress is not None:
                job.progress = max(0.0, min(1.0, float(progress)))
            if stage:
                job.stage = str(stage)
            if check_cancel and job.cancel_requested:
                raise JobCancelled()

        try:
            # Разбор результата -- тоже внутри try: исключение вне него убило
            # бы рабочий поток, и вся очередь замерла бы без единой ошибки.
            files = list(self._runner(job, report) or [])
        except JobCancelled:
            self._finish(job, CANCELLED)
        except Exception as e:  # noqa: BLE001 -- граница потока: любая ошибка пайплайна
            # должна стать статусом задания, а не убить рабочий поток очереди.
            self._finish(job, ERROR, error=str(e) or type(e).__name__)
        else:
            job.files = files
            if job.cancel_requested:
                # Пайплайн успел закончиться раньше, чем увидел отмену.
                self._finish(job, CANCELLED)
            else:
                job.progress = 1.0
                self._finish(job, DONE)

    def _finish(self, job, status, error=""):
        with self._lock:
            job.status = status
            job.error = error
            job.finished = time.time()
