"""
Клиентская сторона IPC с подпроцессом-воркером эмбеддингов — см.
embedding_worker.py для полного объяснения, зачем вообще отдельный
процесс (коротко: единственный способ гарантированно вернуть ОС всю
память torch — реально завершить процесс, а не dereference+gc.collect()
внутри одного общего с лаунчером процесса, которые не могут тронуть
внутреннее состояние уже импортированного рантайма — см. запись в
дорожной карте от 2026-08-20 с реальными цифрами).

WorkerHandle инкапсулирует subprocess.Popen + построчный JSON-протокол.
Один экземпляр на процесс ComfyUIStudio (см. _worker в embedding.py) —
тот же паттерн единственного module-level состояния, что раньше был у
_model в embedding.py, просто теперь это "хендл на подпроцесс", а не
"сама модель в этом же процессе".
"""

from __future__ import annotations

import base64
import json
import logging
import subprocess
import sys
import threading
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Скрытый CLI-флаг, которым main.py распознаёт "это не обычный запуск
# GUI, а запрос на воркер-режим" — см. _worker_command() ниже и
# диспетчеризацию в самом начале main() в корневом main.py (ДО создания
# QApplication и любых тяжёлых импортов).
WORKER_CLI_FLAG = "--promptvault-embedding-worker"


class WorkerError(RuntimeError):
    """Что угодно пошло не так с подпроцессом воркера — не удалось
    запустить, не удалось загрузить модель, подпроцесс неожиданно
    завершился и т.п. Вызывающий код (embedding.py) ловит её так же,
    как раньше ловил обычный RuntimeError от прямой загрузки модели —
    внешнее поведение (семантический поиск молча деградирует, а не
    роняет приложение) не меняется."""


class WorkerHandle:
    """Владеет subprocess.Popen воркера, пока он нужен. Все публичные
    методы синхронные и блокирующие — тот же паттерн, что и раньше был
    у model.encode() в этом же процессе: вызывающий код в repository.py
    и так уже вызывает их с главного потока Qt небольшими батчами,
    чтобы не подвесить UI надолго (см. docstring
    backfill_missing_embeddings) — синхронный IPC-запрос к уже
    прогретому подпроцессу не медленнее, чем был синхронный вызов
    encode() в этом же процессе, разве что на первый запрос уходит
    чуть больше времени на сериализацию через пайп при больших батчах."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._loaded_model_name: str | None = None
        self._loaded_device_preference: str | None = None
        self._loaded_device: str | None = None

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _spawn(self) -> None:
        """Поднимает подпроцесс воркера, если он ещё не запущен.
        Вызывать только под self._lock."""

        if self.is_running():
            return

        cmd = _worker_command()

        logger.info("Запуск подпроцесса эмбеддингов: %s", cmd)

        # startupinfo/creationflags прячут консольное окно на Windows в
        # windowed-сборке (--console=False в ComfyUIStudio-*.spec) —
        # без этого спавн ЕЩЁ ОДНОГО процесса того же exe в
        # воркер-режиме мог бы мигнуть отдельным окном консоли.
        startupinfo = None
        creationflags = 0
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = subprocess.CREATE_NO_WINDOW

        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,  # line-buffered
            startupinfo=startupinfo,
            creationflags=creationflags,
        )

        self._loaded_model_name = None
        self._loaded_device_preference = None
        self._loaded_device = None

        # stderr воркера (питоновские трейсбеки, предупреждения
        # библиотек типа huggingface_hub) пробрасываем в лог основного
        # процесса отдельным потоком — иначе он просто копился бы в
        # пайпе и никогда бы не был виден: subprocess.PIPE без чтения
        # рано или поздно заполняется и подвешивает дочерний процесс на
        # первой же попытке туда что-то написать.
        threading.Thread(
            target=_pump_stderr, args=(self._process,), daemon=True,
        ).start()

    def _request(self, payload: dict) -> dict:
        """Отправляет одну JSON-команду и ждёт один ответ. Вызывать
        только под self._lock — протокол строго request/response,
        параллельные запросы перепутали бы ответы между собой."""

        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise WorkerError("подпроцесс эмбеддингов не запущен")

        line = json.dumps(payload, ensure_ascii=False)
        try:
            process.stdin.write(line + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            self._process = None
            raise WorkerError(f"подпроцесс эмбеддингов недоступен: {e}") from e

        response_line = process.stdout.readline()
        if not response_line:
            # подпроцесс закрыл stdout — скорее всего, упал; заберём код
            # возврата в само исключение, чтобы не приходилось лезть в
            # отдельный лог только ради этого (полный трейсбек всё
            # равно попадёт в основной лог через _pump_stderr)
            code = process.poll()
            self._process = None
            raise WorkerError(f"подпроцесс эмбеддингов завершился неожиданно (код {code})")

        try:
            return json.loads(response_line)
        except Exception as e:
            raise WorkerError(f"подпроцесс эмбеддингов вернул не-JSON: {response_line!r}") from e

    def gpu_available(self) -> bool:
        """Чисто информационная проверка для UI (см. embedding.gpu_available)
        — спавнит подпроцесс, если он ещё не запущен, специально ради
        этой проверки (та же лёгкая цена, что и любое другое обращение
        к семантическому поиску в этой сессии — подпроцесс так или
        иначе будет освобождён при закрытии PromptVault, см. terminate())."""

        with self._lock:
            self._spawn()
            try:
                response = self._request({"cmd": "gpu_available"})
            except WorkerError:
                return False
            return bool(response.get("ok") and response.get("available"))

    def ensure_loaded(
        self,
        model_name: str,
        device_preference: str,
        query_prefix: str,
        max_seq_length: int,
    ) -> str:
        """Гарантирует, что в подпроцессе загружена модель model_name с
        нужным предпочтением устройства — переиспользует уже запущенный
        подпроцесс и уже загруженную модель, если параметры совпадают с
        прошлым разом (самый частый случай — повторные вызовы
        compute_embedding в рамках одной сессии). Возвращает РЕАЛЬНОЕ
        устройство, на котором в итоге оказалась модель (подпроцесс
        может откатиться на CPU при "cuda" без CUDA-сборки torch — та
        же логика, что раньше была в _pick_device, теперь просто внутри
        воркера, см. embedding_worker._pick_device).

        Поднимает WorkerError, если подпроцесс не запустился или модель
        не загрузилась (несовместимый torch, нет сети при первом
        запуске и нет кэша и т.п. — та же природа ошибок, что раньше
        ловил _load_and_verify_model в embedding.py)."""

        with self._lock:
            self._spawn()

            if (
                self._loaded_model_name == model_name
                and self._loaded_device_preference == device_preference
            ):
                return self._loaded_device

            response = self._request({
                "cmd": "load",
                "model_name": model_name,
                "device_preference": device_preference,
                "query_prefix": query_prefix,
                "max_seq_length": max_seq_length,
            })

            if not response.get("ok"):
                raise WorkerError(response.get("error") or "не удалось загрузить модель эмбеддингов")

            actual_device = response.get("device", "cpu")
            self._loaded_model_name = model_name
            self._loaded_device_preference = device_preference
            self._loaded_device = actual_device
            return actual_device

    def encode(self, texts: list[str], *, batch_size: int = 32) -> np.ndarray:
        """Возвращает np.ndarray формы (len(texts), EMBEDDING_DIM) —
        нужен предшествующий успешный ensure_loaded(), иначе WorkerError."""

        with self._lock:
            response = self._request({
                "cmd": "encode", "texts": texts, "batch_size": batch_size,
            })

        if not response.get("ok"):
            raise WorkerError(response.get("error") or "не удалось вычислить эмбеддинги")

        raw = base64.b64decode(response["data_b64"])
        arr = np.frombuffer(raw, dtype=np.float32)
        return arr.reshape(response["shape"])

    def terminate(self, *, timeout: float = 5.0) -> None:
        """Останавливает подпроцесс воркера — ГЛАВНЫЙ смысл всей этой
        переделки: в отличие от dereference+gc.collect() внутри одного
        общего процесса (которые не могут тронуть внутреннее состояние
        уже импортированного рантайма torch — см. запись в дорожной
        карте от 2026-08-20), завершение ОТДЕЛЬНОГО процесса гарантированно
        возвращает ОС 100% его памяти, что бы там ни было живо внутри."""

        with self._lock:
            process = self._process
            if process is None:
                return

            if process.poll() is None:
                try:
                    # сначала вежливо — дадим воркеру шанс закрыться
                    # штатно (сейчас ему закрываться особо нечего, но
                    # на случай, если сюда добавится что-то вроде
                    # временных файлов — дешёвая подстраховка на будущее)
                    if process.stdin is not None:
                        try:
                            process.stdin.write(json.dumps({"cmd": "quit"}) + "\n")
                            process.stdin.flush()
                        except (BrokenPipeError, OSError):
                            pass
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    logger.warning(
                        "Подпроцесс эмбеддингов не завершился за %.1fс — "
                        "принудительно останавливаю (kill)", timeout,
                    )
                    process.kill()
                    try:
                        process.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        logger.error(
                            "Подпроцесс эмбеддингов не удалось остановить "
                            "даже через kill() — может остаться зомби-процессом"
                        )

            logger.info("Подпроцесс эмбеддингов остановлен, вся его память возвращена ОС")

            self._process = None
            self._loaded_model_name = None
            self._loaded_device_preference = None
            self._loaded_device = None


def _pump_stderr(process: subprocess.Popen) -> None:
    if process.stderr is None:
        return
    try:
        for line in process.stderr:
            line = line.rstrip()
            if line:
                logger.debug("[embedding_worker] %s", line)
    except (BrokenPipeError, OSError, ValueError):
        # процесс уже завершается/поток уже закрыт — не критично, это
        # фоновый daemon-поток чисто для логирования
        pass


def _worker_command() -> list[str]:
    """Команда для спавна подпроцесса воркера — по-разному собирается
    для запуска из исходников и из собранного PyInstaller-exe.

    Из исходников: sys.executable — это системный python, у него есть
    флаг -m для запуска модуля по имени пакета, самый чистый способ.

    Из PyInstaller-сборки: sys.executable — это САМ frozen-exe (нет
    отдельного python.exe рядом), поэтому вместо -m передаём ему же
    скрытый WORKER_CLI_FLAG — main.py проверяет его в самом начале (до
    создания QApplication, до любых тяжёлых импортов) и, если он есть,
    вызывает embedding_worker.run_worker() вместо обычного запуска GUI.
    Один и тот же exe в итоге умеет запускаться и как GUI, и как
    воркер-подпроцесс самого себя — обычный паттерн для frozen
    multiprocessing-подобных сценариев (тот же принцип, что решает
    multiprocessing.freeze_support() для стандартного multiprocessing)."""

    if getattr(sys, "frozen", False):
        return [sys.executable, WORKER_CLI_FLAG]

    return [sys.executable, "-m", "comfyui_studio.promptvault.core.embedding_worker"]
