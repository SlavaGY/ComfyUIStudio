"""
Мониторинг ресурсов (CPU/RAM/GPU) и оценка ETA очереди генераций.

Вынесено из comfyui_launcher.py (этап 1 дорожной карты): ResourceMonitor
опрашивает psutil/pynvml по таймеру и параллельно разбирает строки
tqdm-прогресса из stdout ComfyUI (см. core.comfy_process.ProcessLogBridge)
для оценки ETA текущего сэмплинга; format_eta_seconds/format_stats_tooltip/
level_color -- вспомогательные форматтеры, используемые как здесь, так и
в ui.widgets.resource_bar.
"""

import re

from PySide6.QtCore import QTimer, Signal, QObject

from .comfy_api import fetch_history_ids, fetch_queue_status
from .constants import APP_NAME
from .logging_setup import log

try:
    import psutil
except ImportError:
    psutil = None

try:
    import pynvml
except ImportError:
    pynvml = None

RESOURCE_POLL_INTERVAL_MS = 2000

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


class ResourceMonitor(QObject):
    stats_updated = Signal(dict)

    def __init__(self, get_running_port_fn, parent=None):
        super().__init__(parent)
        self._get_running_port = get_running_port_fn
        self._nvml_ok = False
        self._gpu_handle = None
        self._warned_psutil = False
        self._warned_nvml = False

        # Счётчик "готово за сессию" (см. fetch_history_ids) — по set()
        # прочитанных id, а не по разнице длин: так надёжнее в двух
        # смыслах сразу -- (1) не зависит от того, в каком порядке
        # опрашиваются /queue и /history (это два отдельных, не атомарных
        # HTTP-запроса — см. _poll), (2) естественно защищает от
        # повторного счёта одного и того же id.
        # None -- сессия ещё не началась (порт ни разу не был обнаружен
        # запущенным); при первом успешном опросе туда попадёт то, что
        # уже было в /history ДО старта сессии, чтобы не засчитать это
        # как "сделано сейчас".
        self._session_seen_history_ids = None
        self._session_done_ids = set()

        # -- ETA всей очереди --
        # Вместо /ws (не удалось надёжно поймать формат сообщений --
        # см. историю правок) используем то, что и так уже печатает сам
        # ComfyUI в свой stdout при сэмплинге -- строку прогресс-бара
        # tqdm вида "74%|███████▍ | 26/35 [00:24<00:07, 1.21it/s]".
        # ResourceMonitor.feed_log_line читает её из ProcessLogBridge
        # (подключается в MainWindow.__init__ к
        # log_bridge.progress_chunk_received) -- это та же труба, из
        # которой лаунчер и так читает вывод процесса ComfyUI для лога,
        # отдельное сетевое соединение не нужно.
        # Даёт актуальный прогресс только для ОДНОГО, сейчас считающего
        # шаги сэмплера (это же ограничение и у самого tqdm в консоли) --
        # для остальных заданий в очереди (pending) объём по-прежнему
        # берётся из графовой эвристики count_steps_in_prompt().
        self._current_progress = None  # {"done": int, "total": int}
        # prompt_id, к которому ОТНОСИТСЯ self._current_progress (лучшее
        # предположение -- см. _poll: единственный running_id на момент
        # последнего обновления). Строки tqdm не содержат prompt_id, так
        # что это единственный способ понять, что задание сменилось и
        # старые "done"/"total" от предыдущего задания больше не
        # актуальны -- без этого, пока новое задание грузит модель и
        # ещё не напечатало ни одной своей строки, использовались бы
        # цифры от УЖЕ ЗАКОНЧИВШЕГОСЯ предыдущего задания (например
        # done=39 при total=39 -- "уже готово"), что и давало ложные
        # "< 1 с" на самом деле только начавшихся заданиях.
        self._progress_for_id = None
        self._avg_sec_per_step = None
        # Диагностика по КАЖДОМУ заданию отдельно (не только по первому
        # за сессию) -- id заданий, для которых уже залогировали и смену,
        # и первую пойманную строку прогресса. Нужно, чтобы видеть в
        # логе, что происходит именно со 2-м/3-м заданием в очереди, а
        # не только с самым первым.
        self._logged_switch_for_ids = set()
        self._logged_progress_for_ids = set()
        # Диагностика: если очередь активна (что-то running), а от
        # feed_log_line за это время не пришло ни одной строки прогресса
        # -- один раз предупреждаем в лог (не на каждый опрос, чтобы не
        # спамить). Помогает отличить "строки вообще не доходят из
        # _LogReaderThread" от "просто ещё не было ни одного тика".
        self._stall_polls = 0
        self._logged_stall_warning_for_ids = set()

        if pynvml is not None:
            try:
                pynvml.nvmlInit()
                self._gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                self._nvml_ok = True
                log.info("NVML инициализирован, GPU-метрики доступны")
            except Exception as e:
                log.warning("NVML недоступен (нет NVIDIA GPU или драйвера?): %s", e)
        else:
            log.warning("Модуль pynvml не установлен — метрики GPU будут недоступны")

        if psutil is not None:
            psutil.cpu_percent(interval=None)  # первый вызов всегда возвращает 0.0
        else:
            log.warning("Модуль psutil не установлен — метрики CPU/RAM будут недоступны")

        self._timer = QTimer(self)
        self._timer.setInterval(RESOURCE_POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)

    def start(self):
        self._timer.start()
        self._poll()

    def stop(self):
        self._timer.stop()

    # -- прогресс сэмплера из stdout ComfyUI (см. комментарий в __init__) --

    def feed_log_line(self, line: str):
        """Разбирает строку прогресс-бара tqdm из вывода ComfyUI (см.
        _TQDM_PROGRESS_RE) -- обновляет self._current_progress (для
        оставшихся шагов ТЕКУЩЕГО задания) и self._avg_sec_per_step (по
        rate, который tqdm и так сам считает и сглаживает -- поэтому
        здесь без дополнительного EMA, значение просто заменяется на
        последнее известное)."""
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

        # Раньше ХОТЯ БЫ ОДНО задание с неизвестным объёмом обнуляло
        # оценку ЦЕЛИКОМ -- даже если у остальных (например у ТЕКУЩЕГО,
        # уже идущего) объём отлично известен из tqdm. На практике это
        # почти всегда било по ожидающим заданиям (эвристика по графу не
        # находит "steps" для части воркфлоу), из-за чего ETA
        # переставал считаться, как только в очереди появлялось хоть
        # одно ожидающее задание -- независимо от того, насколько точно
        # известен прогресс уже выполняющегося. Вместо этого: заданиям с
        # неизвестным объёмом подставляем СРЕДНИЙ объём остальных
        # заданий этой же очереди, у которых объём известен (в рамках
        # одной "серии" генераций объёмы обычно похожи) -- это оценка, а
        # не точное число, но оно куда полезнее, чем внезапное "не
        # знаю" из-за одного-единственного неопределённого задания.
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

    def _poll(self):
        stats = {}

        if psutil is not None:
            try:
                stats["cpu_percent"] = psutil.cpu_percent(interval=None)
                vm = psutil.virtual_memory()
                stats["ram_percent"] = vm.percent
                stats["ram_used_gb"] = vm.used / (1024 ** 3)
                stats["ram_total_gb"] = vm.total / (1024 ** 3)
            except Exception:
                if not self._warned_psutil:
                    log.exception("Ошибка чтения метрик CPU/RAM")
                    self._warned_psutil = True

        if self._nvml_ok:
            try:
                util = pynvml.nvmlDeviceGetUtilizationRates(self._gpu_handle)
                mem = pynvml.nvmlDeviceGetMemoryInfo(self._gpu_handle)
                temp = pynvml.nvmlDeviceGetTemperature(
                    self._gpu_handle, pynvml.NVML_TEMPERATURE_GPU
                )
                name = pynvml.nvmlDeviceGetName(self._gpu_handle)
                if isinstance(name, bytes):
                    name = name.decode("utf-8", "ignore")
                stats["gpu_available"] = True
                stats["gpu_name"] = name
                stats["gpu_util"] = util.gpu
                stats["gpu_mem_used_gb"] = mem.used / (1024 ** 3)
                stats["gpu_mem_total_gb"] = mem.total / (1024 ** 3)
                stats["gpu_temp"] = temp
            except Exception:
                if not self._warned_nvml:
                    log.exception("Ошибка чтения метрик GPU")
                    self._warned_nvml = True
                stats["gpu_available"] = False
        else:
            stats["gpu_available"] = False

        port = self._get_running_port()
        if port:
            queue_info = fetch_queue_status(port)
            if queue_info is not None:
                running = queue_info["running"]
                pending = queue_info["pending"]
                running_ids = queue_info["running_ids"]
                step_totals = queue_info["step_totals"]
                stats["queue_running"] = running
                stats["queue_pending"] = pending

                history_ids = fetch_history_ids(port)
                if history_ids is not None:
                    if self._session_seen_history_ids is None:
                        # Первый успешный опрос за это включение ComfyUI --
                        # запоминаем то, что уже есть в /history, как "не
                        # наше", иначе в счётчик попало бы то, что было
                        # сделано ДО запуска лаунчера/в прошлые сессии.
                        self._session_seen_history_ids = set(history_ids)
                    # "Готово за сессию" = то, чего не было в /history на
                    # старте сессии, И чего СЕЙЧАС нет в running_ids по
                    # данным /queue из ЭТОГО ЖЕ опроса. Второе условие --
                    # защита от гонки: /queue и /history это два отдельных
                    # HTTP-запроса, не единый снимок состояния, и
                    # генерация может успеть попасть в /history в
                    # промежутке между ними, оставаясь в это же мгновение
                    # ещё "running" по /queue -- без этой проверки она на
                    # секунду засчитывалась готовой, хотя прогресс-бар
                    # всё ещё показывал её выполняющейся. Просто
                    # отложится до следующего опроса (2 сек), когда она
                    # уже точно пропадёт из running_ids.
                    newly_done = (
                        history_ids - self._session_seen_history_ids
                    ) - running_ids
                    self._session_done_ids |= newly_done
                stats["queue_completed_session"] = len(self._session_done_ids)

                # Сменилось ли ЗАДАНИЕ, которое сейчас единственное
                # выполняется? Если да -- self._current_progress (если
                # там что-то есть) относится к УЖЕ не тому заданию,
                # обнуляем -- иначе первые секунды нового задания (пока
                # оно ещё грузит модель и не напечатало ни одной своей
                # строки прогресса) считались бы по остаточным цифрам от
                # предыдущего, уже готового задания (см. комментарий у
                # self._progress_for_id в __init__).
                current_single_id = (
                    next(iter(running_ids)) if len(running_ids) == 1 else None
                )
                if current_single_id != self._progress_for_id:
                    if current_single_id not in self._logged_switch_for_ids:
                        log.info(
                            "ETA очереди: активное задание сменилось %s -> %s "
                            "(running_ids=%s), сбрасываю накопленный прогресс",
                            self._progress_for_id, current_single_id, running_ids,
                        )
                        if current_single_id is not None:
                            self._logged_switch_for_ids.add(current_single_id)
                    self._current_progress = None
                    self._progress_for_id = current_single_id

                # ETA -- см. _compute_eta_seconds/feed_log_line.
                stats["queue_eta_seconds"] = self._compute_eta_seconds(
                    step_totals, running_ids
                )

                if running_ids and self._current_progress is None:
                    # ВАЖНО: проверяем именно self._current_progress (для
                    # ТЕКУЩЕГО задания), а не self._avg_sec_per_step --
                    # последнее, однажды установившись на первом задании,
                    # больше не сбрасывается между заданиями, и проверка
                    # по нему замаскировала бы точно такое же зависание
                    # на 2-м/3-м задании.
                    self._stall_polls += 1
                    if (
                        self._stall_polls >= 5
                        and current_single_id is not None
                        and current_single_id not in self._logged_stall_warning_for_ids
                    ):
                        # ~10 секунд (5 опросов по 2 сек) активной очереди,
                        # а от feed_log_line не пришло ни одной строки
                        # прогресса ДЛЯ ЭТОГО задания -- значит строки из
                        # _LogReaderThread либо не доходят до
                        # progress_chunk_received, либо не совпадают с
                        # _TQDM_PROGRESS_RE. Дальше искать нужно уже по
                        # этому логу, а не гадать.
                        log.warning(
                            "ETA очереди: задание %s идёт уже %d опросов "
                            "подряд, но feed_log_line ни разу не распознал "
                            "для него строку прогресса -- ETA останется "
                            "'оценка...' (см. COMFY_LOG_PATH -- доходят ли "
                            "туда вообще строки вида 'N/M [...it/s]')",
                            current_single_id, self._stall_polls,
                        )
                        self._logged_stall_warning_for_ids.add(current_single_id)
                else:
                    self._stall_polls = 0
        else:
            # ComfyUI не запущен -- сбрасываем сессию и ETA-состояние,
            # чтобы при следующем запуске счёт начался заново с нуля, а не
            # продолжал считать от старого /history (это уже мог быть
            # другой процесс ComfyUI с чистым журналом) и от скорости шага,
            # замеренной в прошлый раз (могла быть другая модель/разрешение).
            self._session_seen_history_ids = None
            self._session_done_ids = set()
            self._current_progress = None
            self._progress_for_id = None
            self._avg_sec_per_step = None
            self._logged_switch_for_ids = set()
            self._logged_progress_for_ids = set()
            self._stall_polls = 0
            self._logged_stall_warning_for_ids = set()

        self.stats_updated.emit(stats)



def format_eta_seconds(seconds, tr=None) -> str:
    """"~2 мин 30 с" / "~45 с" / "оценка..." (скорость шага ещё не
    замерена) -- используется и в чипе очереди, и в подсказке трея.

    tr -- необязательная функция перевода (обычно self._tr / loc.tr);
    если не передана, строки остаются на русском (поведение по
    умолчанию, как раньше)."""
    if tr is None:
        tr = lambda text: text
    if seconds is None:
        return tr("оценка...")
    if seconds < 1:
        return tr("< 1 с")
    m, s = divmod(int(round(seconds)), 60)
    return f"~{m} {tr('мин')} {s} {tr('с')}" if m > 0 else f"~{s} {tr('с')}"



def format_stats_tooltip(stats: dict, tr=None) -> str:
    """Компактная подсказка для трея.

    Важно: Windows обрезает текст всплывающей подсказки трея примерно
    на 128 символах без предупреждения (именно так "срезалась" строка
    с очередью). Поэтому здесь без заголовка, без лишних слов и с
    жёсткой подстраховкой по длине.

    tr -- необязательная функция перевода, см. format_eta_seconds().
    """
    if tr is None:
        tr = lambda text: text
    lines = []
    if "cpu_percent" in stats:
        lines.append(f"CPU {stats['cpu_percent']:.0f}%")
    if "ram_percent" in stats:
        gb = tr("ГБ")
        lines.append(
            f"RAM {stats['ram_percent']:.0f}% "
            f"({stats['ram_used_gb']:.1f}/{stats['ram_total_gb']:.1f} {gb})"
        )
    if stats.get("gpu_available"):
        lines.append(f"GPU {stats['gpu_util']}% {stats['gpu_temp']}°C")
        gb = tr("ГБ")
        lines.append(
            f"VRAM {stats['gpu_mem_used_gb']:.1f}/{stats['gpu_mem_total_gb']:.1f} {gb}"
        )
    if "queue_pending" in stats:
        running, pending = stats["queue_running"], stats["queue_pending"]
        line = f"{tr('Очередь')} {running}/{pending}"
        if "queue_completed_session" in stats:
            line += f" · {tr('Готово')} {stats['queue_completed_session']}"
        if (running + pending) > 0 and "queue_eta_seconds" in stats:
            line += f" · ETA {format_eta_seconds(stats['queue_eta_seconds'], tr=tr)}"
        lines.append(line)
    else:
        lines.append(tr("ComfyUI не запущен"))

    text = "\n".join(lines) if lines else APP_NAME
    if len(text) > 120:
        text = text[:117] + "..."
    return text



def level_color(value, warn=60, crit=85):
    """Цвет по уровню нагрузки/температуры: зелёный -> жёлтый -> красный."""
    if value is None:
        return "#5b6472"
    if value >= crit:
        return "#d9534f"
    if value >= warn:
        return "#d98c2b"
    return "#3fae4f"


NEUTRAL_CHIP_COLOR = "#5b6472"
QUEUE_ACTIVE_COLOR = "#3a7ecf"
QUEUE_IDLE_COLOR = "#3fae4f"



