"""
Генератор промптов: по кнопке в Imagine запускает llama-server (llama.cpp)
с GGUF-моделью, отправляет ей запрос, забирает ответ и ВЫКЛЮЧАЕТ модель.

Настройки (путь к llama.cpp, модель, mmproj, префикс запроса) читаются из
общего файла %APPDATA%\\ComfyUIStudio\\prompt_generator.json при КАЖДОМ
запуске задачи (см. shared_promptgen.py) -- их пишет страница «Генератор
промптов» в настройках Studio, перезапускать Imagine после правок не нужно.

Как это работает
----------------
Задача (Job) -- один запуск «start → ответ → stop». Одновременно живёт
максимум одна: модель занимает VRAM, две параллельно нужны редко, а
конкурировать за видеокарту ещё и с ComfyUI -- тем более.

  0. Подготовка VRAM: при необходимости (см. free_comfy_mode) выгружаются
     модели ComfyUI, и ждём, пока драйвер реально освободит память.
  1. Поток задачи запускает `llama-server` на свободном локальном порту
     (cwd = папка llama.cpp, PATH дополнен папками с DLL, см.
     shared_promptgen.dll_dirs()); весь вывод процесса идёт в файл.
  2. Опрашивает GET /health, пока модель не загрузится (503 -- ещё
     грузится, 200 -- готова). Если процесс умер -- отдаёт хвост его
     вывода как текст ошибки.
  3. Шлёт POST /v1/chat/completions со stream=true (картинка, если есть,
     идёт как data:-URL в image_url) и копит ответ -- фронтенд опрашивает
     статус и показывает промежуточный текст. max_tokens = 8192.
  4. Останавливает llama-server (со всем деревом процессов) ПЕРЕД тем, как
     пометить задачу выполненной -- когда интерфейс видит «готово»,
     видеопамять уже свободна.

Почему опрос статуса, а не один долгий запрос: страница Imagine может быть
открыта через reverse-proxy Remote (с телефона), у которого таймаут 60 с
(см. remote/imagine_proxy.py) -- загрузка модели + генерация легко его
превышают.

Логи (%APPDATA%\\ComfyUIStudio\\logs\\)
---------------------------------------
  * promptgen.log -- ход каждой задачи с меткой [id]: настройки, снимки
    VRAM/RAM/процессов llama до и после, решение о выгрузке ComfyUI, pid и
    команда llama-server, тайминги (загрузка / до первого токена /
    генерация, ток/с из самого llama-server), предупреждения (модель
    загружена на GPU не полностью, mmproj на CPU), ошибки с трассировкой.
    Текст запроса и ответа НЕ пишется -- только длины.
  * llama-server/<время>-<id>.log -- полный вывод llama-server каждого
    запуска (последние LLAMA_LOGS_KEEP штук).

История запросов (%APPDATA%\\ComfyUIStudio\\promptgen_history.db, SQLite)
--------------------------------------------------------------------------
В отличие от promptgen.log, сюда пишется СОДЕРЖИМОЕ: по одной записи на
каждую задачу (в т. ч. ошибочную и отменённую) -- префикс, текст
пользователя, итоговый запрос, была ли картинка и её имя, ответ модели,
токены (запрос/ответ/всего), тайминги и время начала. Запись делается в
конце задачи (finally в _run), ошибки записи только логируются и на
генерацию не влияют. Отключается настройкой log_requests. Смотреть --
вкладка «История промптов» в настройках Studio; схема и запросы -- в
comfyui_studio/promptgen_history.py.

Защита от «осиротевшего» llama-server (он держит гигабайты VRAM)
-----------------------------------------------------------------
  * процесс всегда убивается вместе с деревом потомков (psutil; на
    случай отсутствия -- taskkill /T), а не только «головной» pid: обёртка
    llama-server из LM Studio могла бы оставить потомка жить;
  * pid запущенного процесса пишется в promptgen_running.json; при старте
    Imagine и перед каждой задачей уцелевшие с прошлого раза процессы
    (аварийное завершение Imagine) добиваются -- sweep_stale();
  * при остановке Imagine из лаунчера дочерние процессы гасит
    `taskkill /T` (см. ImagineProcess.stop()), плюс shutdown-хук FastAPI и
    atexit;
  * на Windows процесс дополнительно помещается в Job Object с флагом
    KILL_ON_JOB_CLOSE -- ОС убьёт llama-server, даже если Imagine упал
    аварийно (см. _attach_kill_on_close_job()).

Структура модулей (этап R10 плана рефакторинга)
-----------------------------------------------
Этот файл остаётся публичным входом: PromptGenerator (оркестрация одной
задачи), singleton `generator` и реэкспорт всего, что раньше жило здесь
(тесты и main.py обращаются к `promptgen.<имя>`). Остальное разнесено:

  * promptgen_base.py    -- константы, PromptGenError, _Cancelled, мелкие помощники;
  * promptgen_deps.py    -- необязательные shared_promptgen / promptgen_history;
  * promptgen_diag.py    -- логирование, VRAM/RAM, разбор вывода llama-server, _LoadProbe;
  * promptgen_job.py     -- Job, clean_output, подсчёт токенов, запись для истории;
  * promptgen_process.py -- запуск/остановка llama-server, Job Object, реестр процессов;
  * promptgen_stream.py  -- POST /v1/chat/completions со стримингом SSE.

Что нужно подменять в тестах через monkeypatch, живёт ЗДЕСЬ (gpu_memory
вызывается из PromptGenerator, _FIRST_REPORT_S/_REPORT_EVERY_S -- из
_wait_ready, generator -- из main.py): подмена `pg.<имя>` действует только на
код, который ищет имя в этом модуле.
"""

from __future__ import annotations

import atexit
import logging
import subprocess
import threading
import time
import urllib.error
from typing import Callable, Optional

from comfyui_studio.imagine.backend.promptgen_base import (
    MAX_TOKENS,
    LOAD_TIMEOUT_S,
    STREAM_TIMEOUT_S,
    MAX_IMAGE_DATA_URL_LEN,
    _MIB,
    PromptGenError,
    _Cancelled,
    _clean_image_name,
    _file_mb,
    _error_text,
)
from comfyui_studio.imagine.backend.promptgen_deps import (
    shared_promptgen,
    promptgen_history,
)
from comfyui_studio.imagine.backend.promptgen_diag import (
    log,
    _ensure_file_logging,
    _jlog,
    _with_log_hint,
    _psutil,
    gpu_memory,
    _max_free_mb,
    _fmt_gpus,
    _system_snapshot,
    estimate_vram_mb,
    _KEY_LINE_RE,
    _TS_RE,
    _OFFLOAD_RE,
    _CLIP_BACKEND_RE,
    _line_seconds,
    analyze_load_timeline,
    _compute_cache_dir,
    _dir_size_mb,
    _LoadProbe,
    _read_log_tail,
    _read_log_text,
    inspect_llama_log,
    log_job_stats,
)
from comfyui_studio.imagine.backend.promptgen_job import (
    _THINK_BLOCK_RE,
    _THINK_OPEN_RE,
    Job,
    clean_output,
    token_counts,
    history_record,
)
from comfyui_studio.imagine.backend.promptgen_process import (
    _attach_kill_on_close_job,
    _STATUS_DLL_NOT_FOUND,
    _free_port,
    _split_extra_args,
    build_command,
    build_env,
    _llama_log_path,
    _spawn,
    _kill_tree,
    _stop_process,
    _exit_message,
    _registry_path,
    _registry_read,
    _registry_write,
    _register_process,
    _unregister_process,
    sweep_stale,
)
from comfyui_studio.imagine.backend.promptgen_stream import (
    _opener,
    stream_completion,
    lost_connection_message,
)

# Реэкспорт: всё, что раньше определялось в этом файле, доступно как
# promptgen.<имя>. Состояние логирования (_file_handler*, _console_handler)
# и _job_handle живёт теперь в promptgen_diag / promptgen_process и сюда
# намеренно НЕ реэкспортируется: имя-копия расходилась бы с настоящим
# значением после первой же перепривязки.
__all__ = [
    "Job",
    "LOAD_TIMEOUT_S",
    "MAX_IMAGE_DATA_URL_LEN",
    "MAX_TOKENS",
    "PromptGenError",
    "PromptGenerator",
    "STREAM_TIMEOUT_S",
    "_CLIP_BACKEND_RE",
    "_Cancelled",
    "_FIRST_REPORT_S",
    "_KEY_LINE_RE",
    "_LoadProbe",
    "_MIB",
    "_OFFLOAD_RE",
    "_REPORT_EVERY_S",
    "_STATUS_DLL_NOT_FOUND",
    "_THINK_BLOCK_RE",
    "_THINK_OPEN_RE",
    "_TS_RE",
    "_attach_kill_on_close_job",
    "_clean_image_name",
    "_compute_cache_dir",
    "_dir_size_mb",
    "_ensure_file_logging",
    "_error_text",
    "_exit_message",
    "_file_mb",
    "_fmt_gpus",
    "_free_port",
    "_jlog",
    "_kill_tree",
    "_line_seconds",
    "_llama_log_path",
    "_max_free_mb",
    "_opener",
    "_psutil",
    "_read_log_tail",
    "_read_log_text",
    "_register_process",
    "_registry_path",
    "_registry_read",
    "_registry_write",
    "_spawn",
    "_split_extra_args",
    "_stop_process",
    "_system_snapshot",
    "_unregister_process",
    "_with_log_hint",
    "analyze_load_timeline",
    "build_command",
    "build_env",
    "clean_output",
    "estimate_vram_mb",
    "generator",
    "gpu_memory",
    "history_record",
    "inspect_llama_log",
    "log",
    "log_job_stats",
    "lost_connection_message",
    "promptgen_history",
    "shared_promptgen",
    "stream_completion",
    "sweep_stale",
    "token_counts",
]

# Как часто писать в лог ход загрузки модели (с замерами процесса). Лежит
# здесь, а не в promptgen_diag: тесты подменяют эти значения через pg.<имя>,
# а читает их _wait_ready этого модуля.
_FIRST_REPORT_S = 5.0
_REPORT_EVERY_S = 15.0


# ---------------------------------------------------------------------------
# Генератор
# ---------------------------------------------------------------------------

class PromptGenerator:
    def __init__(self):
        self._lock = threading.Lock()
        self._job: Optional[Job] = None
        self._last_stop: Optional[float] = None  # когда в последний раз гасили llama-server

    # -- настройки ------------------------------------------------------------

    @staticmethod
    def _settings() -> dict:
        if shared_promptgen is None:
            return {}
        return shared_promptgen.read_settings()

    # -- публичный интерфейс -------------------------------------------------

    def startup_sweep(self) -> None:
        """Вызывается при старте Imagine: добивает llama-server'ы от
        аварийно завершившегося прошлого запуска."""
        _ensure_file_logging()
        try:
            killed = sweep_stale()
            if killed:
                log.warning("При старте Imagine убито «осиротевших» llama-server: %d", killed)
        except Exception:
            log.exception("Ошибка уборки осиротевших процессов при старте")

    def status(self) -> dict:
        available = shared_promptgen is not None
        cfg = self._settings()
        configured = available and shared_promptgen.is_configured(cfg)
        problem = shared_promptgen.validate(cfg) if configured else None
        with self._lock:
            job = self._job
        return {
            "available": available,
            "configured": configured,
            "problem": problem,
            "vision": bool(cfg.get("mmproj_path", "").strip()),
            "image_max_side": cfg.get("image_max_side", 0) or 1024,
            "job": job.snapshot() if job else None,
        }

    def get_job(self, job_id: str) -> Optional[dict]:
        with self._lock:
            job = self._job
        if job is None or job.id != job_id:
            return None
        return job.snapshot()

    def start(
        self,
        text: str,
        image: Optional[str] = None,
        pre_start: Optional[Callable[[], None]] = None,
        image_name: Optional[str] = None,
    ) -> str:
        if shared_promptgen is None:
            raise PromptGenError("Генератор промптов доступен только внутри ComfyUI Studio.", 400)
        _ensure_file_logging()
        cfg = self._settings()
        problem = shared_promptgen.validate(cfg)
        if problem:
            log.warning("Запуск отклонён: %s", problem)
            raise PromptGenError(problem, 400)

        text = (text or "").strip()
        image = image or None
        if image:
            if not image.startswith("data:image/") or ";base64," not in image[:100]:
                raise PromptGenError("Изображение должно быть data:image/…;base64 URL.", 400)
            if len(image) > MAX_IMAGE_DATA_URL_LEN:
                raise PromptGenError("Изображение слишком большое.", 400)
            if not cfg.get("mmproj_path", "").strip():
                raise PromptGenError(
                    "Чтобы работать с изображениями, укажите файл mmproj в "
                    "настройках Studio (Генератор промптов).",
                    400,
                )
        if not text and not image:
            raise PromptGenError("Введите текст или прикрепите изображение.", 400)

        with self._lock:
            current = self._job
            if current is not None and current.active:
                thread = current.thread
                if thread is not None and not thread.is_alive():
                    # Страховка от «навсегда занято»: поток задачи умер, не
                    # успев выставить итоговое состояние (не должно
                    # случаться, но именно так выглядела бы зависшая
                    # генерация, лечившаяся только перезапуском Studio).
                    log.error("[%s] задача числилась активной, но её поток завершён — сбрасываю", current.id)
                    current.finish_error("Задача оборвалась неожиданно (см. лог).")
                else:
                    log.info("Запуск отклонён: уже работает задача %s (%s)", current.id, current.state)
                    raise PromptGenError("Генератор промптов уже работает.", 409)
            job = Job()
            self._job = job

        thread = threading.Thread(
            target=self._run,
            args=(job, cfg, text, image, pre_start, _clean_image_name(image_name)),
            daemon=True,
            name=f"promptgen-{job.id}",
        )
        thread.start()
        job.thread = thread
        return job.id

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._job
        if job is None or job.id != job_id or not job.active:
            return False
        _jlog(job, logging.INFO, "запрошена отмена")
        job.cancel_event.set()
        _kill_tree(job.proc)  # прерывает и ожидание загрузки, и стриминг
        return True

    def shutdown(self) -> None:
        with self._lock:
            job = self._job
        if job is not None and job.active:
            _jlog(job, logging.INFO, "остановка Imagine — прерываю задачу")
            job.cancel_event.set()
        if job is not None:
            _kill_tree(job.proc)

    # -- рабочий поток --------------------------------------------------------

    def _run(
        self, job: Job, cfg: dict, text: str, image: Optional[str], pre_start,
        image_name: str = "",
    ) -> None:
        proc = None
        log_fh = None
        llama_log = ""
        # что именно запрошено -- фиксируем до любых этапов, чтобы запись в
        # историю (в finally) была полной и при ошибке на раннем этапе
        job.prefix = cfg["prefix"]
        job.user_text = text
        job.has_image = bool(image)
        job.image_name = image_name if image else ""
        job.full_prompt = shared_promptgen.compose_prompt(cfg["prefix"], text, has_image=bool(image))
        try:
            _jlog(
                job, logging.INFO,
                "старт: текст %d симв., изображение: %s",
                len(text), f"{len(image) * 3 // 4 // 1024} КиБ" if image else "нет",
            )
            _jlog(
                job, logging.INFO,
                "настройки: llama_dir=%s model=%s (%d МиБ) mmproj=%s ctx=%s ngl=%r extra_args=%r "
                "free_comfy=%s image_max_side=%s",
                cfg["llama_dir"], cfg["model_path"], _file_mb(cfg["model_path"]),
                cfg["mmproj_path"] or "—", cfg["ctx_size"], cfg["gpu_layers"], cfg["extra_args"],
                cfg["free_comfy_mode"], cfg["image_max_side"],
            )
            swept = sweep_stale()
            if swept:
                _jlog(job, logging.WARNING, "перед запуском убито осиротевших llama-server: %d", swept)

            self._prepare_vram(job, cfg, pre_start)
            if job.cancel_event.is_set():
                raise _Cancelled()

            port = _free_port()
            cmd = build_command(cfg, port)
            llama_log = _llama_log_path(job.id)
            cache_dir = _compute_cache_dir()
            cache_before = _dir_size_mb(cache_dir)
            if cache_before is not None:
                _jlog(job, logging.INFO, "кэш CUDA-ядер драйвера NVIDIA (%s): %.0f МиБ", cache_dir, cache_before)
            job.mark("spawned")
            proc, log_fh = _spawn(cmd, cfg["llama_dir"].strip(), build_env(cfg), llama_log)
            job.proc = proc
            _register_process(proc, job.id)
            _jlog(
                job, logging.INFO, "llama-server запущен: pid=%s порт=%s\n  лог: %s\n  команда: %s",
                proc.pid, port, llama_log, cmd,
            )
            if job.cancel_event.is_set():  # cancel() мог прийти до присвоения job.proc
                raise _Cancelled()

            self._wait_ready(job, proc, llama_log, port)
            job.mark("ready")
            _jlog(job, logging.INFO, "модель загружена за %.1f с", job.marks["ready"] - job.marks["spawned"])
            cache_after = _dir_size_mb(cache_dir)
            if cache_before is not None and cache_after is not None:
                grown = cache_after - cache_before
                if grown >= 5:
                    _jlog(
                        job, logging.INFO,
                        "кэш CUDA-ядер вырос на %.0f МиБ за время загрузки — на этом запуске драйвер "
                        "компилировал CUDA-ядра (JIT из PTX); при повторных запусках это должно быть быстрее",
                        grown,
                    )
                else:
                    _jlog(job, logging.INFO, "кэш CUDA-ядер за время загрузки не изменился (%+.1f МиБ) — JIT-компиляции не было", grown)
            self._inspect_llama_log(job, cfg, llama_log)
            _jlog(job, logging.INFO, "после загрузки: %s", _system_snapshot())
            job.set_state("generating")

            prompt = job.full_prompt
            _jlog(job, logging.DEBUG, "запрос к модели: %d симв. (префикс + текст)", len(prompt))
            self._stream_completion(job, port, prompt, image, proc, llama_log)
            job.mark("generated")

            result = clean_output(job.raw_text())
            if not result:
                hint = (
                    " Модель, похоже, ушла в «рассуждения» и не выдала ответ — "
                    "отключите thinking (например, «Доп. аргументы»: --reasoning off)."
                    if job.tokens else ""
                )
                raise PromptGenError("Модель вернула пустой ответ." + hint)
            self._log_stats(job, len(result))

            # Сначала выключаем модель (освобождаем VRAM), потом сообщаем
            # «готово».
            report = _stop_process(proc, log_fh)
            proc = log_fh = None
            _jlog(job, logging.INFO, "llama-server остановлен: %s", report)
            self._last_stop = time.monotonic()
            _jlog(job, logging.INFO, "готово за %.1f с", time.monotonic() - job.created)
            job.finish_ok(result)
        except _Cancelled:
            _jlog(job, logging.INFO, "отменено")
            job.finish_cancelled()
        except PromptGenError as exc:
            if job.cancel_event.is_set():
                _jlog(job, logging.INFO, "отменено")
                job.finish_cancelled()
            else:
                _jlog(job, logging.WARNING, "ошибка: %s", exc)
                job.finish_error(_with_log_hint(str(exc)))
        except Exception as exc:
            if job.cancel_event.is_set():
                _jlog(job, logging.INFO, "отменено")
                job.finish_cancelled()
            else:
                _jlog(job, logging.ERROR, "внутренняя ошибка", exc_info=True)
                job.finish_error(_with_log_hint(f"Внутренняя ошибка: {exc}"))
        finally:
            if proc is not None:
                report = _stop_process(proc, log_fh)
                _jlog(job, logging.INFO, "llama-server остановлен (аварийно/по отмене): %s", report)
                self._last_stop = time.monotonic()
            elif log_fh is not None:
                log_fh.close()
            self._save_history(job, cfg)
            started_pid = job.proc.pid if job.proc is not None else None
            if started_pid is not None:
                _unregister_process(started_pid)
                try:
                    # драйвер освобождает память не мгновенно -- даём мгновение,
                    # чтобы в логе было видно реальное состояние VRAM
                    time.sleep(0.5)
                    _jlog(job, logging.INFO, "после остановки: %s", _system_snapshot())
                except Exception:  # граница: диагностика после остановки не должна влиять на итог задачи
                    log.debug("[%s] не удалось записать состояние после остановки", job.id, exc_info=True)

    # -- этапы ---------------------------------------------------------------

    def _wait_vram(self, job: Job, need_mb: Optional[int], max_s: float) -> Optional[list[dict]]:
        """Ждёт, пока освободится/стабилизируется видеопамять: выходит, когда
        свободно ≥ need_mb или два подряд замера почти не отличаются (память
        перестала освобождаться), либо по таймауту. None -- GPU не измеряется."""
        deadline = time.monotonic() + max_s
        previous: Optional[int] = None
        gpus = gpu_memory()
        while gpus is not None and time.monotonic() < deadline:
            free = _max_free_mb(gpus)
            if need_mb is not None and free is not None and free >= need_mb:
                return gpus
            if previous is not None and free is not None and abs(free - previous) < 32:
                return gpus
            previous = free
            if job.cancel_event.wait(0.5):
                raise _Cancelled()
            gpus = gpu_memory()
        return gpus

    def _prepare_vram(self, job: Job, cfg: dict, pre_start) -> None:
        """Готовит видеопамять к запуску: ждёт, пока драйвер освободит память
        после предыдущей остановки llama-server (если она была недавно), и
        по режиму free_comfy_mode решает, выгружать ли модели ComfyUI --
        видеопамять, занятая ComfyUI, заставляет llama-server (--fit)
        молча оставить часть слоёв на CPU, и генерация становится в разы
        медленнее."""
        mode = cfg.get("free_comfy_mode", "auto")
        need = estimate_vram_mb(cfg)
        gpus = gpu_memory()

        if gpus is not None and self._last_stop is not None and time.monotonic() - self._last_stop < 30:
            _jlog(
                job, logging.INFO,
                "предыдущая модель остановлена %.0f с назад — жду, пока освободится VRAM",
                time.monotonic() - self._last_stop,
            )
            gpus = self._wait_vram(job, None, 6.0) or gpus

        _jlog(job, logging.INFO, "перед запуском: %s; нужно ≈ %d МиБ", _system_snapshot(gpus), need)
        free = _max_free_mb(gpus)

        if mode == "never":
            _jlog(job, logging.INFO, "выгрузка моделей ComfyUI отключена настройкой")
            return
        if mode == "auto":
            if gpus is None:
                _jlog(job, logging.INFO, "auto: VRAM не измеряется — ComfyUI не трогаю")
                return
            if free is not None and free >= need:
                _jlog(job, logging.INFO, "auto: свободно %d МиБ ≥ нужно ≈%d — выгрузка ComfyUI не требуется", free, need)
                return
            _jlog(job, logging.INFO, "auto: свободно %s МиБ < нужно ≈%d — выгружаю модели ComfyUI", free, need)
        else:
            _jlog(job, logging.INFO, "выгружаю модели ComfyUI (режим «всегда»)")

        if pre_start is None:
            return
        try:
            pre_start()
        except Exception:
            _jlog(job, logging.WARNING, "не удалось выгрузить модели ComfyUI", exc_info=True)
            return
        if gpus is not None:
            if job.cancel_event.wait(1.0):  # ComfyUI обрабатывает /free асинхронно
                raise _Cancelled()
            after = self._wait_vram(job, need, 10.0)
            _jlog(job, logging.INFO, "после выгрузки ComfyUI: %s", _fmt_gpus(after))

    def _wait_ready(self, job: Job, proc: subprocess.Popen, log_path: str, port: int) -> None:
        url = f"http://127.0.0.1:{port}/health"
        started = time.monotonic()
        deadline = started + LOAD_TIMEOUT_S
        next_report = started + _FIRST_REPORT_S
        probe = _LoadProbe(proc.pid)
        while True:
            if job.cancel_event.is_set():
                raise _Cancelled()
            if proc.poll() is not None:
                # даём файловой системе секунду сбросить остаток вывода
                time.sleep(0.3)
                raise PromptGenError(_exit_message(proc, log_path))
            try:
                with _opener.open(url, timeout=2) as resp:
                    if resp.status == 200:
                        return
            except urllib.error.HTTPError:
                pass  # 503 -- модель ещё загружается
            except (urllib.error.URLError, OSError):
                pass  # порт ещё не слушается
            now = time.monotonic()
            if now > deadline:
                raise PromptGenError(
                    f"Модель не загрузилась за {LOAD_TIMEOUT_S // 60} мин — "
                    "проверьте размер модели и доступную память."
                )
            if now > next_report:
                _jlog(job, logging.INFO, "…модель загружается (%d с): %s", now - started, probe.describe())
                next_report = now + _REPORT_EVERY_S
            time.sleep(0.5)

    def _inspect_llama_log(self, job: Job, cfg: dict, log_path: str) -> None:
        """Достаёт из вывода llama-server то, что объясняет медленную работу
        (логика -- в promptgen_diag.inspect_llama_log)."""
        inspect_llama_log(job, cfg, log_path)

    @staticmethod
    def _token_counts(job: Job) -> tuple[Optional[int], Optional[int], Optional[int], str]:
        """(запрос, ответ, всего, источник); логика -- в promptgen_job.token_counts."""
        return token_counts(job)

    def _save_history(self, job: Job, cfg: dict) -> None:
        """Одна запись в базу истории (см. promptgen_history.py). Любая
        ошибка только логируется: журнал не должен ломать генерацию."""
        if promptgen_history is None or not cfg.get("log_requests", True):
            return
        try:
            promptgen_history.add_record(history_record(job, cfg))
        except Exception:
            _jlog(job, logging.WARNING, "не удалось записать запрос в историю", exc_info=True)

    def _log_stats(self, job: Job, result_len: int) -> None:
        log_job_stats(job, result_len)

    def _stream_completion(
        self, job: Job, port: int, prompt: str, image: Optional[str], proc, log_path: str
    ) -> None:
        stream_completion(job, port, prompt, image, proc, log_path)

    @staticmethod
    def _lost_connection_message(proc, log_path: str, reason: str) -> str:
        return lost_connection_message(proc, log_path, reason)


generator = PromptGenerator()
atexit.register(generator.shutdown)
