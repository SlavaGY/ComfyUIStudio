"""
Временный инструмент диагностики памяти -- НЕ постоянная часть
приложения.

Добавлен для поиска утечки, о которой сообщил пользователь: после
использования семантического поиска в PromptVault и последующего
закрытия его окна память (судя по Task Manager) не уменьшается, хотя
embedding.unload_model() уже делает dereference модели, gc.collect() и
(на Windows) EmptyWorkingSet (см. embedding._trim_working_set() и запись
в дорожной карте от 2026-08-20). Раз фикс не подействовал -- нужны не
предположения, а числа по контрольным точкам, чтобы увидеть, на каком
именно шаге память РЕАЛЬНО растёт и на каком (не) падает.

Использование -- одна строка в интересующей точке кода:

    from comfyui_studio.mem_diagnostics import log_memory
    log_memory("после закрытия окна PromptVault")

Пишет в отдельный файл mem_diagnostics.log (см. _log_path() ниже) —
НЕ переиспользует ни launcher.log, ни лог PromptVault: оба настраиваются
в разное время и в разном порядке в зависимости от того, что пользователь
открыл первым, а этому инструменту нужно ловить самую первую контрольную
точку сразу на старте процесса, до того, как что-либо ещё успело
настроить логирование. Дублирует в stdout, поэтому видно и в консоли
при запуске из исходников.

Когда утечка будет найдена и исправлена, вызовы log_memory() по всему
коду стоит убрать вместе с этим файлом — это диагностический инструмент
для конкретного расследования, а не часть постоянного логирования
приложения.
"""

import logging
import os
import threading
import time

try:
    import psutil
except ImportError:  # pragma: no cover -- см. log_memory() ниже, работает и без него
    psutil = None


_logger = logging.getLogger("mem_diagnostics")
_logger.setLevel(logging.DEBUG)
# НЕ пропускаем наверх в root/launcher/promptvault-логгеры -- у этого
# инструмента свой отдельный файл специально для того, чтобы все
# контрольные точки из разных модулей были на одной понятной ленте, не
# перемешанные с обычным логом приложения.
_logger.propagate = False

_lock = threading.Lock()
_configured = False
_process = None
_start_time = time.monotonic()


def _log_path() -> str:
    """Файл лога -- рядом с launcher.log (см. APP_DIR в
    launcher/core/constants.py), но с собственным именем. Если по
    какой-то причине константы лаунчера недоступны (например,
    диагностика запущена в изоляции от остального комплекта) —
    запасной путь рядом с текущей рабочей папкой, лишь бы не падать."""

    try:
        from comfyui_studio.launcher.core.constants import APP_DIR
        os.makedirs(APP_DIR, exist_ok=True)
        return os.path.join(APP_DIR, "mem_diagnostics.log")
    except Exception:
        return os.path.join(os.getcwd(), "mem_diagnostics.log")


def _ensure_configured() -> None:
    global _configured, _process

    if _configured:
        return

    with _lock:
        if _configured:
            return

        fmt = logging.Formatter(
            "%(asctime)s | %(message)s", "%H:%M:%S"
        )

        file_handler = logging.FileHandler(_log_path(), encoding="utf-8")
        file_handler.setFormatter(fmt)
        _logger.addHandler(file_handler)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(fmt)
        _logger.addHandler(console_handler)

        if psutil is None:
            _logger.warning(
                "psutil не установлен — контрольные точки будут "
                "писаться без чисел памяти. pip install psutil для "
                "полноценной диагностики."
            )
        else:
            try:
                _process = psutil.Process(os.getpid())
            except Exception:
                _logger.exception("Не удалось получить psutil.Process(os.getpid())")

        _configured = True
        _logger.info("=== диагностика памяти запущена (pid=%s) ===", os.getpid())


def log_memory(label: str) -> None:
    """Пишет одну строку в mem_diagnostics.log: время с первого вызова
    в этом процессе, текущий RSS (resident set size -- на Windows это
    ровно то число, что показывает Task Manager в столбце "Память"/
    рабочий набор процесса) и, где доступно, USS.

    RSS против USS: RSS считает все физические страницы, отображённые
    в адресное пространство процесса, включая общие с другими
    процессами (например, страницы загруженных системных .dll/.so,
    общих для многих процессов сразу) — то есть РОВНО то число, на
    которое смотрит пользователь в Task Manager, и именно оно
    интересует в этом расследовании. USS (unique set size) считает
    только страницы, приватные для ЭТОГО процесса — не всегда доступен
    (memory_full_info() требует дополнительных прав на некоторых
    платформах/конфигурациях), но там, где есть, помогает отличить
    "выросло из-за нашего torch" от "выросло из-за чего-то общего с
    системой" — на практике веса модели/буферы MKL почти всегда
    приватны для процесса, так что RSS и USS должны расти практически
    синхронно, если дело именно в модели.
    """

    _ensure_configured()

    if psutil is None or _process is None:
        _logger.info("%-70s  (psutil недоступен — нет чисел)", label)
        return

    try:
        mem = _process.memory_info()
        rss_mb = mem.rss / (1024 * 1024)

        uss_part = ""
        try:
            full = _process.memory_full_info()
            uss_part = f"  USS={full.uss / (1024 * 1024):8.1f} МБ"
        except Exception:
            pass

        elapsed = time.monotonic() - _start_time
        _logger.info(
            "[+%7.2fs]  RSS=%8.1f МБ%s  <- %s",
            elapsed, rss_mb, uss_part, label,
        )
    except Exception:
        _logger.exception("Не удалось снять метрику памяти для '%s'", label)
