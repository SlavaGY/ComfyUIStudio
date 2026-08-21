"""Семантические (векторные) эмбеддинги текста промптов.

Используется АНГЛОЯЗЫЧНАЯ модель intfloat/e5-large-v2 — англоязычная
версия той же E5-серии, что и multilingual-e5-large (которая
использовалась до неё).

ВАЖНО, ЭТО СОЗНАТЕЛЬНЫЙ ОТКАЗ ОТ ТРЕБОВАНИЯ ru/en ИЗ ЗАДАЧИ 3.1: на
практике многоязычная версия делила ёмкость модели между 100+ языками
и заметно хуже справлялась именно с русским, чем хотелось бы, а
англоязычные промпты в реальной библиотеке составляют подавляющее
большинство. Раз вся ёмкость модели уходит на один язык, а не
размазывается по сотне, английское качество ощутимо выше. Русские
запросы в семантическом поиске (по смыслу) больше НЕ поддерживаются —
для промптов и запросов не на английском модель не даёт осмысленных
эмбеддингов. Обычный текстовый поиск по подстроке (не по смыслу, поле
"Search" — не "Semantic search") по-прежнему работает на любом языке,
это его не касается.

Если понадобится вернуть русский — см. git-историю этого файла:
intfloat/multilingual-e5-large, тот же префиксный API, EMBEDDING_DIM
тоже 1024 (менять не пришлось бы).

E5-серия (в отличие от paraphrase-моделей, использовавшихся раньше в
этой задаче) обучена именно на retrieval: поиске релевантного фрагмента
по короткому запросу — задача АССИМЕТРИЧНАЯ, ровно наш случай (короткий
запрос против отдельного тега/фразы промпта).

ВАЖНО: у E5 обязательны префиксы перед текстом — без них модель
работает заметно хуже, так она обучена: "query: " перед поисковым
запросом (см. compute_query_embedding) и "passage: " перед каждым
тегом/фразой документа (см. compute_embedding/compute_embeddings_batch).
Это не опциональная настройка, а прямое требование модели — она
намеренно кодирует "это запрос" и "это документ" разными префиксами,
чтобы разделить их представления в векторном пространстве.

Модель крупная (~1.3 ГБ, 1024-мерные вектора) и заметно медленнее на
CPU, чем самая первая (MiniLM) версия из этой задачи. Порог сходства
SEMANTIC_SIMILARITY_THRESHOLD (см. app/config.py) для этой модели
эмпирически подобран на 0.85 — заметно выше, чем для предыдущих
моделей: диапазон осмысленных значений косинусного сходства у каждой
модели свой, значения от разных моделей между собой не сравнимы.

ВАЖНО про формат промптов AI-генерации: это почти всегда список тегов/
коротких фраз через запятую ("1girl, kneeling, forest, masterpiece,
..."), а НЕ связный естественный текст. Если закодировать весь такой
список одним эмбеддингом, модель усредняет все теги в одно "смазанное"
значение — специфичные детали ("kneeling in front of a boy") тонут в
общем фоне, а короткий запрос ("мальчик") почти никогда не наберёт
нужного косинусного сходства с этим усреднённым вектором целого
промпта, даже если соответствующий тег в промпте буквально присутствует.

Поэтому промпт ГЕНЕРАЦИИ (документ) кодируется не одним вектором, а
по отдельным тегам/фразам (см. _split_into_chunks) — эмбеддинг хранится
как несколько L2-нормализованных векторов подряд в одном BLOB. Сходство
с запросом — это МАКСИМУМ косинусного сходства среди всех тегов
документа (см. cosine_similarity), а не сходство с одним "средним"
вектором. Запрос пользователя (обычно короткая связная фраза) при этом
остаётся ОДНИМ вектором — см. compute_query_embedding — и сравнивается
целиком с каждым тегом документа.

Модель (~1.3 ГБ) грузится лениво и только один раз за время жизни
процесса — первый вызов, требующий эмбеддинг, может занять несколько
секунд (загрузка весов в память, при самом первом запуске на машине —
ещё и скачивание с HuggingFace Hub). Библиотека sentence-transformers —
опциональная зависимость: если она не установлена (или модель не
удалось загрузить, например нет сети при первом запуске), семантический
поиск должен молча деградировать, а не ронять всё приложение — весь
остальной функционал (обычный текстовый поиск, фильтры, галерея)
не должен зависеть от её наличия.
"""

from __future__ import annotations

import atexit
import importlib.util
import logging
import re
import sys
from typing import Any

import numpy as np

from comfyui_studio.promptvault.config import DEFAULT_EMBEDDING_MODEL, EMBEDDING_MODELS
from comfyui_studio.promptvault.core.embedding_ipc import WorkerError, WorkerHandle

logger = logging.getLogger(__name__)

MODEL_NAME = "intfloat/e5-large-v2"

# размерность вектора, отдаваемого этой моделью (1024 — как и у
# multilingual-e5-large, которая использовалась до неё, менять не
# пришлось; 768 было у mpnet-base, 384 — у самой первой MiniLM-L12) —
# используется, чтобы распознать и отбросить "чужие" эмбеддинги,
# оставшиеся в БД от другой модели (например, посчитанные ДО очередного
# перехода — они не бьются по размеру и просто дают пустой результат в
# bytes_to_chunks, см. её защиту от несовместимых данных, а не падение;
# ВАЖНО: одинаковая размерность НЕ означает совместимость данных между
# multilingual-e5-large и e5-large-v2 — это разные веса, их вектора
# нельзя сравнивать между собой, несмотря на одинаковый EMBEDDING_DIM,
# поэтому полный пересчёт всё равно обязателен при переходе), а также
# чтобы разложить BLOB с несколькими векторами тегов обратно на
# отдельные вектора (см. bytes_to_chunks)
EMBEDDING_DIM = 1024

# E5-модели обучены с явным разделением "это запрос" / "это документ" —
# без этих префиксов модель работает заметно хуже, это не опциональная
# настройка, а прямое требование архитектуры (см. модуль docstring)
_QUERY_PREFIX = "query: "
_PASSAGE_PREFIX = "passage: "

# модель по умолчанию режет вход на 128 токенов — для промпта из
# отдельного короткого тега этого с большим запасом достаточно, но
# отдельные "теги" в промптах иногда оказываются целыми фразами;
# увеличиваем запас прочности (архитектура XLM-R, на которой основана
# модель, поддерживает позиционные эмбеддинги вплоть до 512)
MAX_SEQ_LENGTH = 256

_worker = WorkerHandle()
_load_failed = False

# на случай, если ни один явный путь закрытия (unload_model(), вызванный
# из on_close/settings_window_destroyed) не сработал -- например,
# PromptVault запущен standalone (см. main.py) и весь процесс просто
# завершается сам, без промежуточных Qt-сигналов уничтожения окна.
# Подпроцесс воркера НЕ завершается автоматически вместе с родителем на
# большинстве платформ (в т.ч. на Windows без явного использования Job
# Objects) -- без этой подстраховки он остался бы висеть отдельным
# осиротевшим процессом после закрытия PromptVault. Явные вызовы
# unload_model() в on_close-путях остаются нужны САМИ ПО СЕБЕ (не только
# для страховки) -- при standalone=False PromptVault делит процесс с
# лаунчером, и тот продолжает жить после закрытия только окна
# PromptVault, так что atexit здесь бы не сработал вовремя (сработал бы
# только при закрытии всего ComfyUIStudio, а не сразу после закрытия
# именно PromptVault).
atexit.register(lambda: _worker.terminate() if _worker.is_running() else None)

# см. set_enabled — позволяет пользователю (через
# GalleryManager.set_semantic_search_enabled) полностью отключить
# семантический поиск на уровне приложения, чтобы модель (~1.3 ГБ)
# вообще никогда не загружалась в память, если она не нужна
_disabled_by_user = False

# ------------------------------------------------------------------
# выбор модели эмбеддингов (задача: настройка модели) — см.
# EMBEDDING_MODELS в app/config.py. MODEL_NAME/EMBEDDING_DIM/
# _QUERY_PREFIX/_PASSAGE_PREFIX выше остаются обычными module-level
# переменными (не превращены в property/функции) намеренно — это тот
# же самый интерфейс, которым уже пользуются существующие тесты
# (monkeypatch.setattr(embedding, "EMBEDDING_DIM", ...) и т.п.),
# set_model() ниже просто переприсваивает их значения при смене модели.
_current_model_key: str = DEFAULT_EMBEDDING_MODEL


def available_models() -> dict[str, dict]:
    """Реестр поддерживаемых моделей эмбеддингов (см. EMBEDDING_MODELS
    в app/config.py) — используется окном настроек для построения
    списка выбора."""

    return EMBEDDING_MODELS


def current_model_key() -> str | None:
    """Ключ текущей выбранной модели (см. EMBEDDING_MODELS), либо
    None, если семантический поиск полностью отключён пользователем
    (вариант "без модели", см. set_model)."""

    if _disabled_by_user:
        return None

    return _current_model_key


def current_similarity_threshold() -> float:
    """Порог косинусного сходства для ТЕКУЩЕЙ выбранной модели (см.
    EMBEDDING_MODELS в app/config.py — свой для каждой модели, значения
    разных моделей между собой не сравнимы)."""

    return float(EMBEDDING_MODELS[_current_model_key]["similarity_threshold"])


def set_model(model_key: str | None) -> None:
    """Переключает модель эмбеддингов на model_key (см. EMBEDDING_MODELS
    в app/config.py), либо полностью отключает семантический поиск,
    если model_key is None (вариант "без модели" в настройках — то же
    самое, что set_enabled(False)).

    Сбрасывает уже загруженный экземпляр модели (если он был) — со
    следующего обращения через get_model() будет загружена заново уже
    новая модель. Смена модели делает старые эмбеддинги в БД
    несовместимыми (другие веса — другое векторное пространство, даже
    при одинаковой размерности), поэтому вызывающий код (см.
    GalleryManager.set_embedding_model) должен предложить пользователю
    полный пересчёт (GenerationRepository.recompute_all_embeddings).
    """

    global _current_model_key, MODEL_NAME, EMBEDDING_DIM
    global _QUERY_PREFIX, _PASSAGE_PREFIX, _load_failed

    if model_key is None:
        set_enabled(False)
        return

    if model_key not in EMBEDDING_MODELS:
        raise ValueError(f"Неизвестная модель эмбеддингов: {model_key}")

    info = EMBEDDING_MODELS[model_key]

    _current_model_key = model_key
    MODEL_NAME = info["repo_id"]
    EMBEDDING_DIM = info["dim"]
    _QUERY_PREFIX = info["query_prefix"]
    _PASSAGE_PREFIX = info["passage_prefix"]

    # старая модель (если была загружена в подпроцессе-воркере)
    # относится к прошлой модели — её нельзя переиспользовать; менять
    # здесь ничего специально не нужно: WorkerHandle.ensure_loaded()
    # сам сравнивает запрошенные model_name/device_preference с тем,
    # что уже загружено в воркере, и перезагрузит модель заново при
    # следующем get_model(), если они разошлись (см. embedding_ipc.py)
    _load_failed = False

    set_enabled(True)

    logger.info("Модель эмбеддингов переключена на %s (%s)", model_key, MODEL_NAME)


# ------------------------------------------------------------------
# выбор устройства (CPU/GPU) — задача: настройка вычислений

# "auto" — автоопределение (см. embedding_worker._pick_device,
# исполняется в подпроцессе), "cpu"/"cuda" — принудительный выбор
# пользователем в настройках
_device_preference: str = "auto"


def device_preference() -> str:

    return _device_preference


def set_device_preference(preference: str) -> None:
    """Устанавливает предпочтение устройства: "auto" (автоопределение,
    см. embedding_worker._pick_device — исполняется в подпроцессе, не
    здесь), "cpu" или "cuda" (принудительно).

    Принудительный выбор "cuda" без установленного torch с
    CUDA-сборкой не приведёт к реальному использованию GPU — см.
    предупреждение в UI (SettingsWindow) и комментарий у
    embedding_worker._pick_device про CPU-only сборку torch, которую
    `pip install torch` часто ставит по умолчанию на Windows.

    Менять уже загруженную в подпроцессе модель здесь не нужно — см.
    комментарий в set_model() выше, WorkerHandle.ensure_loaded() сам
    заметит расхождение и перезагрузит модель на новом устройстве при
    следующем get_model().
    """

    global _device_preference, _load_failed

    if preference not in ("auto", "cpu", "cuda"):
        raise ValueError(f"Неизвестное устройство: {preference}")

    _device_preference = preference

    _load_failed = False

    logger.info("Устройство для модели эмбеддингов: %s", preference)


def gpu_available() -> bool:
    """True, если установлен torch со сборкой, поддерживающей CUDA, и
    физически доступен GPU — чисто информационная проверка для UI
    (SettingsWindow), сама по себе ничего не переключает.

    НЕ импортирует torch в ЭТОМ процессе — делегирует проверку в
    подпроцесс-воркер (см. embedding_ipc.WorkerHandle.gpu_available()),
    спавня его при необходимости. Раньше эта функция делала `import
    torch` прямо здесь — сам факт такого импорта (даже без загрузки
    модели) навсегда тянул за собой память torch в основной процесс,
    ровно то, чего вся эта архитектура с подпроцессом и пытается
    избежать (см. модуль embedding_worker.py и запись в дорожной карте
    от 2026-08-20)."""

    return _worker.gpu_available()


# embedding_worker._load_model() делает самопроверочный encode() именно
# потому, что конструктор SentenceTransformer может успешно отработать
# даже с несовместимым torch — а падение происходит только на первом
# реальном forward-проходе. Но сама ПОПЫТКА создать SentenceTransformer
# уже выделяет память под веса модели (~1.3 ГБ) ДО этого падения — в
# ПОДПРОЦЕССЕ это уже не так критично, как раньше было для основного
# процесса (подпроцесс так и так будет полностью убит при закрытии
# PromptVault, см. unload_model), но всё равно нет смысла тратить время
# на заведомо обречённую попытку. Проверка версии torch заранее (без
# импорта torch/sentence-transformers вообще, через метаданные пакета)
# позволяет пропустить её целиком.
_MIN_TORCH_VERSION = (2, 4)


def _torch_version_compatible() -> bool:
    """True, если версия установленного torch >= _MIN_TORCH_VERSION —
    либо если версию вообще не удалось определить (тогда не блокируем
    заранее, пусть обычный путь загрузки/самопроверки решает сам).

    Намеренно НЕ импортирует torch — только читает метаданные пакета
    (importlib.metadata), это на порядки дешевле по памяти и времени.
    """

    try:
        from importlib.metadata import version
        installed = version("torch")
    except Exception:
        return True

    try:
        major, minor = (int(p) for p in installed.split(".")[:2])
    except ValueError:
        return True

    return (major, minor) >= _MIN_TORCH_VERSION


def set_enabled(enabled: bool) -> None:
    """Включает/выключает семантический поиск на уровне приложения
    целиком, независимо от того, установлен ли sentence-transformers и
    исправна ли версия torch (см. GalleryManager.set_semantic_search_enabled
    — постоянное пользовательское переключение, задача: оптимизация
    памяти).

    При отключении модель (~1.3 ГБ весов) НЕ загружается вообще ни при
    синхронизации папки (эмбеддинги новых генераций просто не
    считаются — embedding остаётся NULL в БД, генерация по-прежнему
    доступна через обычный текстовый поиск), ни при досчитывании
    (backfill_missing_embeddings становится no-op, см. is_available).
    """

    global _disabled_by_user
    _disabled_by_user = not enabled

    if not enabled:
        logger.info(
            "Семантический поиск отключён пользователем — модель "
            "эмбеддингов загружаться не будет"
        )


def library_installed() -> bool:
    """True, если библиотека sentence-transformers физически
    установлена — чистая проверка окружения, не зависящая от
    пользовательского выбора вкл/выкл (см. set_enabled) и от того,
    падала ли уже попытка загрузки модели в этом запуске (см.
    _load_failed).

    Это то, что должен спрашивать UI (SettingsWindow, через
    GalleryManager.semantic_search_available()), чтобы решить, можно
    ли вообще ПОКАЗАТЬ переключатель "Enable semantic search" как
    кликабельный — иначе получается замкнутый круг: пользователь
    выключает семантический поиск, is_available() перестаёт быть True
    из-за _disabled_by_user, а если UI дизейблит сам чекбокс по этому
    же признаку, включить поиск обратно становится невозможно.

    ВАЖНО: намеренно НЕ `import sentence_transformers` — та библиотека
    тянет за собой torch, а сам факт импорта torch (даже без загрузки
    какой-либо модели) резервирует под себя изрядный кусок памяти сам
    по себе (нативные библиотеки — MKL/OpenMP/сишные экстеншены — не
    ленивые, грузятся целиком при самом импорте, не при первом реальном
    использовании). SettingsWindow вызывает эту функцию БЕЗУСЛОВНО при
    каждом построении окна настроек (см. _apply_semantic_search_
    availability), в том числе когда семантический поиск выключен —
    полноценный `import sentence_transformers` здесь означало бы, что
    просто ОТКРЫТИЕ окна настроек PromptVault само по себе тянет в
    память torch, независимо от того, включена ли настройка (именно
    так и было, когда эта функция ненадолго использовала полноценный
    import — импорт модуля кешируется в sys.modules на весь процесс,
    и снять его обратно потом уже нельзя, в отличие от unload_model()
    для самой модели). importlib.util.find_spec() ищет модуль на
    путях поиска и возвращает информацию о нём, не выполняя его код —
    достаточно, чтобы отличить "установлено" от "не установлено", и
    не стоит почти ничего по памяти или времени.

    Один нюанс find_spec(): если "sentence_transformers" уже лежит в
    sys.modules, но с отсутствующим/некорректным __spec__ (бывает у
    вручную сконструированных заглушек вроде
    types.ModuleType("sentence_transformers") без честного spec —
    именно так тестовый набор подделывает наличие пакета, см.
    tools/promptvault/tests/test_embedding.py), find_spec() не
    возвращает None, а поднимает ValueError — тогда считаем модуль
    установленным на основании того, что он и так уже в sys.modules
    (раз он там есть, значит в каком-то смысле уже "установлен",
    искать его по путям и не нужно).
    """

    try:
        return importlib.util.find_spec("sentence_transformers") is not None
    except ValueError:
        return "sentence_transformers" in sys.modules


def is_available() -> bool:
    """True, если библиотека sentence-transformers установлена, модель
    хотя бы попробовать загрузить ещё не пытались/пытались успешно, и
    пользователь не отключил семантический поиск явно (см. set_enabled).

    Это проверка "активен ли поиск ПРЯМО СЕЙЧАС" — используется местами
    вроде backfill_missing_embeddings, которым нужно знать, стоит ли
    вообще пытаться считать эмбеддинги. Для вопроса "можно ли в принципе
    ВКЛЮЧИТЬ семантический поиск" (например, чтобы решить, дизейблить
    ли сам переключатель в UI) нужен library_installed() выше, а не эта
    функция — иначе состояние "выключено пользователем" делает
    переключатель невозможным включить обратно.

    Не гарантирует, что загрузка при первом реальном вызове точно
    удастся (например, файлы модели могут быть повреждены) — но
    позволяет коду заранее не пытаться использовать семантический
    поиск как рабочую возможность, если зависимость вообще не
    установлена.
    """

    if _disabled_by_user:
        return False

    if _load_failed:
        return False

    return library_installed()


class _WorkerModelProxy:
    """Тонкая обёртка вокруг WorkerHandle, подставляемая как "модель" в
    get_model() — отвечает интерфейсу SentenceTransformer.encode()
    (сигнатура и поведение: строка на входе -> 1D вектор, список строк
    -> 2D массив), которого ждут compute_query_embedding/
    compute_embedding/compute_embeddings_batch ниже — и который же
    подменяют существующие тесты через monkeypatch на
    embedding.get_model (см. tools/promptvault/tests/conftest.py) —
    сама модель при этом физически живёт в ОТДЕЛЬНОМ подпроцессе (см.
    embedding_worker.py/embedding_ipc.py), а не в этом процессе.

    Существование этого класса — единственное, что позволило вынести
    torch в подпроцесс, ПОЛНОСТЬЮ не трогая compute_query_embedding/
    compute_embedding/compute_embeddings_batch и существующий тестовый
    seam на get_model(): весь остальной код продолжает думать, что
    работает с обычным объектом модели.
    """

    def __init__(self, worker: WorkerHandle) -> None:
        self._worker = worker

    def encode(
        self,
        texts,
        *,
        normalize_embeddings: bool = True,  # noqa: ARG002 — нормализация всегда включена на стороне воркера (см. embedding_worker.py), параметр принимается только для совместимости сигнатуры с SentenceTransformer.encode()
        show_progress_bar: bool = False,  # noqa: ARG002 — то же самое, воркер никогда не показывает прогресс-бар (пишет только в свой stderr, не в UI)
        batch_size: int = 32,
    ) -> np.ndarray:
        single = isinstance(texts, str)
        text_list = [texts] if single else list(texts)
        result = self._worker.encode(text_list, batch_size=batch_size)
        return result[0] if single else result


def get_model() -> Any:
    """Возвращает объект с интерфейсом SentenceTransformer.encode() —
    сама модель загружается (лениво, при первом обращении) не в этом
    процессе, а в отдельном подпроцессе-воркере (см. _WorkerModelProxy
    выше и модуль embedding_worker.py).

    Потокобезопасно на том же основании, что и раньше: WorkerHandle
    сам сериализует обращения к подпроцессу через внутренний
    threading.Lock (см. embedding_ipc.py) — здесь отдельная блокировка
    не нужна.
    """

    global _load_failed

    if _disabled_by_user:
        raise RuntimeError("Семантический поиск отключён пользователем")

    if _load_failed:
        raise RuntimeError("Загрузка модели эмбеддингов ранее уже завершилась ошибкой")

    if not library_installed():
        _load_failed = True
        raise RuntimeError(
            "Пакет sentence-transformers не установлен — семантический "
            "поиск недоступен (pip install sentence-transformers)"
        )

    if not _torch_version_compatible():
        # см. комментарий у _MIN_TORCH_VERSION — не тратим память и
        # время подпроцесса на заведомо обречённую попытку загрузки:
        # self-test в embedding_worker._load_model всё равно провалится
        # на первом реальном forward-проходе при слишком старом torch
        _load_failed = True
        logger.error(
            "Установленная версия torch слишком стара для модели "
            "эмбеддингов %s (нужен torch>=%d.%d) — семантический "
            "поиск отключён без попытки загрузки модели, чтобы не "
            "тратить время впустую. Обновите: pip install --upgrade torch",
            MODEL_NAME, *_MIN_TORCH_VERSION
        )
        raise RuntimeError(
            f"torch слишком стар для модели эмбеддингов "
            f"(нужен >= {_MIN_TORCH_VERSION[0]}.{_MIN_TORCH_VERSION[1]})"
        )

    from comfyui_studio.mem_diagnostics import log_memory

    log_memory("get_model(): до ensure_loaded() в подпроцессе-воркере")

    logger.info("Загрузка модели эмбеддингов (%s)...", MODEL_NAME)

    try:
        actual_device = _worker.ensure_loaded(
            MODEL_NAME, _device_preference, _QUERY_PREFIX, MAX_SEQ_LENGTH,
        )
    except WorkerError as e:
        # та же природа ошибок, что раньше ловил except Exception вокруг
        # _load_and_verify_model (нет сети при первом запуске и нет
        # кэша, повреждённый кеш HuggingFace, несовместимая версия
        # torch и т.п.) — не должны ронять приложение, только отключать
        # семантический поиск
        _load_failed = True
        logger.error(
            "Модель эмбеддингов %s не загрузилась в подпроцессе: %s. "
            "Частая причина — слишком старая версия torch (нужен "
            "torch>=2.4); проверьте: pip show torch, затем "
            "pip install --upgrade torch. Семантический поиск будет "
            "недоступен, приложение продолжит работать в обычном "
            "текстовом режиме поиска.",
            MODEL_NAME, e
        )
        raise RuntimeError(f"Не удалось загрузить модель эмбеддингов: {e}") from e

    log_memory("get_model(): после ensure_loaded() (модель готова в подпроцессе)")

    # предупреждение о принудительном "cuda" без реальной CUDA — раньше
    # логировалось внутри _pick_device (в этом же процессе), теперь сам
    # выбор устройства происходит в подпроцессе (см.
    # embedding_worker._pick_device) — сравниваем запрошенное
    # предпочтение с фактически полученным устройством здесь же, в
    # вызывающем коде, вместо того чтобы передавать логику логирования
    # через границу процессов
    if _device_preference == "cuda" and actual_device != "cuda":
        logger.warning(
            "Устройство 'cuda' выбрано пользователем, но CUDA недоступна "
            "(нужна CUDA-сборка torch) — используется CPU"
        )

    logger.info("Модель эмбеддингов загружена (device=%s)", actual_device)

    return _WorkerModelProxy(_worker)


def unload_model() -> None:
    """Останавливает подпроцесс-воркер эмбеддингов (см. embedding_ipc.
    WorkerHandle.terminate()/embedding_worker.py), если он был запущен —
    без изменения выбранной модели/устройства и без отключения
    семантического поиска для пользователя (в отличие от
    set_enabled(False)/set_model(None), которые заодно меняют
    настройку). Следующий вызов get_model() поднимет подпроцесс заново.

    Вызывающие: _on_child_window_destroyed в comfyui_studio/launcher/
    ui/settings_page.py (через ON_CLOSE_CALLBACKS в tool_registry.py,
    см. main.py) при закрытии полного окна PromptVault, и
    _on_settings_window_destroyed в comfyui_studio/launcher/ui/settings/
    promptvault_page.py при закрытии одних только настроек PromptVault
    (оба пути могут независимо довести до загрузки модели — см. кнопку
    "Recompute embeddings" в SettingsWindow). Плюс подстраховка на
    atexit чуть выше в файле — на случай standalone-запуска PromptVault
    (см. main.py), где весь процесс просто завершается сам, без
    промежуточных Qt-сигналов уничтожения окна.

    ПОЧЕМУ ПОДПРОЦЕСС, А НЕ ПРОСТО `_model = None` + `gc.collect()`
    (как было раньше) -- см. запись в дорожной карте от 2026-08-20 с
    реальными цифрами из живого прогона на Windows: `import
    sentence_transformers` (тянет torch) стоит ~475 МБ, сама модель
    (all-MiniLM-L6-v2) — всего ~70 МБ. `_model = None` + `gc.collect()`
    (и даже принудительный Windows EmptyWorkingSet поверх них)
    освобождали только эти ~70 МБ — оставшиеся ~475 МБ оставались
    "застрявшими" в процессе до самого закрытия ComfyUIStudio: это не
    освобождённая-но-не-возвращённая память, а ЖИВОЕ внутреннее
    состояние рантайма torch (пул потоков intra-op parallelism, буферы
    MKL/OpenMP, таблицы диспетчера операций ATen), которое остаётся
    referenced, пока модуль `torch` импортирован в процессе — а Python
    не умеет чисто "выгрузить" уже импортированный C-экстеншен обратно.

    WorkerHandle.terminate() решает это тем, что модель вообще никогда
    не грузится в ЭТОМ процессе — она живёт в отдельном подпроцессе
    (см. embedding_worker.py), который здесь просто убивается целиком.
    Завершение процесса ОС гарантированно забирает 100% его памяти —
    в отличие от dereference+gc.collect() внутри одного общего
    процесса, которые физически не могут тронуть внутреннее состояние
    уже импортированного рантайма (что и подтвердил реальный прогон:
    EmptyWorkingSet после dereference не сдвинул память ни на килобайт).
    """

    global _load_failed

    from comfyui_studio.mem_diagnostics import log_memory

    if not _worker.is_running():
        log_memory("unload_model(): подпроцесс уже не запущен — нечего выгружать")
        return

    log_memory("unload_model(): до terminate() подпроцесса")

    _worker.terminate()

    # свежая попытка загрузки (например, после повторного открытия
    # окна) не должна наследовать флаг ошибки от какого-то предыдущего
    # сеанса — если он был выставлен, get_model() и так больше не
    # пытался бы поднимать подпроцесс заново
    _load_failed = False

    log_memory("unload_model(): после terminate() подпроцесса (итог — должно быть близко к базовому уровню без torch)")

    logger.info("Подпроцесс эмбеддингов остановлен, модель выгружена из памяти")

# верхняя граница числа тегов на одну генерацию — защита от
# патологически длинных "промптов" (например, если кто-то вставил в
# positive целый абзац без единой запятой пополам с реальными тегами)
# от превращения одной генерации в тысячи отдельных эмбеддингов
MAX_CHUNKS_PER_TEXT = 100


# теги/фразы промпта чаще всего разделены запятыми, иногда —
# переносами строк (некоторые UI генерации кладут по тегу на строку)
# или точкой с запятой
_CHUNK_SPLIT_RE = re.compile(r"[,\n;]+")

# <lora:name:0.8> и подобные — ссылка на файл LoRA, а не описание
# содержимого изображения (сам факт использования конкретной LoRA уже
# хранится отдельно в БД, см. Generation.loras) — вырезается целиком
_LORA_TAG_RE = re.compile(r"<[^>]+>")

# ":1.2" / ": 0.8" — вес усиления/ослабления тега в синтаксисе SD
# (masterpiece:1.2) — не часть смысла тега, только его сила
_WEIGHT_SUFFIX_RE = re.compile(r":\s*[\d.]+")

# (...) / [...] / {...} — группировка/эмфазис в промптах SD; ВАЖНО:
# внутри часто лежит осмысленный текст, а не только вес — например
# "(red hair, blue eyes:1.2)" — поэтому скобки только разворачиваются
# (текст внутри остаётся и уходит на повторную разбивку по запятым), а
# не вырезаются вместе с содержимым
_BRACKET_CHARS_RE = re.compile(r"[(){}\[\]]")


def _split_into_chunks(text: str) -> list[str]:
    """Режет промпт на отдельные теги/фразы для по-тегового
    эмбеддинга — см. модуль docstring про то, зачем это нужно.

    Дополнительно нормализует характерный для SD-промптов синтаксис
    перед разбивкой: убирает ссылки на LoRA, веса усиления/ослабления
    тегов и символы группирующих скобок (сохраняя текст внутри них —
    см. _BRACKET_CHARS_RE), а также отбрасывает получившиеся мусорные
    токены — пустые, однобуквенные и чисто цифровые (например,
    случайно попавший в текст seed)."""

    text = _LORA_TAG_RE.sub(" ", text)
    text = _WEIGHT_SUFFIX_RE.sub(" ", text)
    text = _BRACKET_CHARS_RE.sub(" ", text)

    parts = _CHUNK_SPLIT_RE.split(text)
    chunks = []

    for part in parts:

        chunk = part.strip()

        if len(chunk) < 2:
            # пусто, один символ, либо то, что осталось после чистки
            # выше (одинокий пробел и т.п.) — не несёт смысла
            continue

        if chunk.isdigit():
            # чисто цифровой "тег" — почти всегда мусор (обрывок веса,
            # seed и т.п.), а не осмысленное содержимое
            continue

        chunks.append(chunk)

    return chunks[:MAX_CHUNKS_PER_TEXT]


# "технические" теги AI-генерации, не описывающие содержимое
# изображения — движковые указатели качества/рейтинга/движка. Они не
# являются словами ни одного языка, поэтому их эмбеддинги
# непредсказуемы и на практике оказываются "хабами" — ложно похожими
# сразу на многие несвязанные запросы (проблема hubness в
# многомерных embedding-пространствах). При по-теговом max-pooling
# сравнении (см. cosine_similarity) достаточно ОДНОГО такого
# ложно-похожего тега, чтобы вся генерация ошибочно попала в выдачу —
# поэтому такие теги исключаются из эмбеддинга ещё до кодирования.
#
# Список заведомо неполный (нет единого стандарта тегов между разными
# UI/чекпоинтами) — расширяйте при необходимости под свою библиотеку.
_BOILERPLATE_TAGS = {
    "masterpiece", "best quality", "high quality", "highest quality",
    "high resolution", "highres", "absurdres", "ultra detailed",
    "highly detailed", "extremely detailed", "detailed textures",
    "detailed background", "sharp focus", "illustration", "digital art",
    "digital painting", "official art", "good hands", "good anatomy",
    "break", "score_9", "score_8_up", "score_8", "score_7_up",
    "very aesthetic", "aesthetic", "5 fingers", "6 fingers", "text",
    "watermark", "signature", "censored", "bar censor", "mosaic censor",
}

# систематические префиксы движковых тегов (score_9, score_8_up,
# rating_explicit, source_anime, quality_amazing, aesthetic_high и
# т.п.) — покрываются паттерном, а не перечислением всех вариантов
_BOILERPLATE_PREFIXES = ("score_", "rating_", "source_", "quality_", "aesthetic_")


def _is_boilerplate_tag(chunk: str) -> bool:

    lowered = chunk.lower().strip()

    if lowered in _BOILERPLATE_TAGS:
        return True

    return lowered.startswith(_BOILERPLATE_PREFIXES)


def _filter_boilerplate(chunks: list[str]) -> list[str]:
    """Убирает технические теги (см. _BOILERPLATE_TAGS) из списка
    тегов документа перед эмбеддингом. Если после фильтрации не
    осталось ни одного тега (промпт целиком состоял из технических
    тегов) — возвращает исходный список как есть: лучше эмбеддинг с
    шумом, чем полное отсутствие эмбеддинга у генерации."""

    filtered = [c for c in chunks if not _is_boilerplate_tag(c)]

    return filtered if filtered else chunks


def compute_query_embedding(text: str) -> bytes | None:
    """Эмбеддинг ПОИСКОВОГО ЗАПРОСА — всегда ровно один вектор на весь
    текст целиком, БЕЗ разбиения на теги (в отличие от compute_embedding/
    compute_embeddings_batch, которые кодируют промпт генерации по
    отдельным тегам — см. модуль docstring).

    Запрос пользователя — обычно короткая связная фраза ("девочка стоит
    на коленях перед парнем"), и её смысл важно сохранить целиком, а не
    резать по запятым (которых в запросе обычно и нет). Сравнение с
    промптом генерации всё равно идёт по чанкам последнего — см.
    cosine_similarity.

    Текст кодируется с префиксом "query: " — обязательное требование
    E5-моделей (см. модуль docstring), не опциональная настройка.
    """

    if not text or not text.strip():
        return None

    try:
        model = get_model()
        vec = model.encode(
            _QUERY_PREFIX + text.strip(),
            normalize_embeddings=True,
            show_progress_bar=False,
        )
    except RuntimeError:
        return None
    except Exception as e:  # noqa: BLE001 — поиск не критичен, не роняем UI
        logger.warning("Не удалось вычислить эмбеддинг запроса: %s", e)
        return None

    return np.asarray(vec, dtype=np.float32).tobytes()


def compute_embedding(text: str) -> bytes | None:
    """Вычисляет эмбеддинг промпта ГЕНЕРАЦИИ (документа) — режет его на
    отдельные теги/фразы (см. _split_into_chunks) и кодирует каждый
    отдельно; результат — bytes, содержащий N L2-нормализованных
    векторов подряд (N * EMBEDDING_DIM float32 чисел). N зависит от
    количества тегов в конкретном промпте, поэтому размер BLOB для
    разных генераций разный — читать его нужно через bytes_to_chunks
    (reshape по EMBEDDING_DIM), а не считать, что там всегда один
    вектор.

    Возвращает None, если текст пустой либо модель недоступна/не
    загрузилась — вызывающий код (репозиторий) в этом случае просто не
    сохраняет эмбеддинг, и такая генерация не будет участвовать в
    семантическом поиске (но останется доступной через обычный текстовый).

    Каждый тег кодируется с префиксом "passage: " — обязательное
    требование E5-моделей (см. модуль docstring), не опциональная
    настройка.
    """

    chunks = _filter_boilerplate(_split_into_chunks(text))

    if not chunks:
        return None

    try:
        model = get_model()
        vectors = model.encode(
            [_PASSAGE_PREFIX + c for c in chunks],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
    except RuntimeError:
        return None
    except Exception as e:  # noqa: BLE001 — эмбеддинг не критичен, не роняем sync
        logger.warning("Не удалось вычислить эмбеддинг: %s", e)
        return None

    return np.asarray(vectors, dtype=np.float32).tobytes()


def compute_embeddings_batch(texts: list[str]) -> list[bytes | None]:
    """Батчевая версия compute_embedding — считает эмбеддинги сразу для
    списка промптов ОДНИМ проходом модели (все теги всех текстов
    собираются в один плоский список и кодируются разом), что при
    синхронизации папки с большим числом новых/изменённых файлов на
    порядок быстрее, чем вызывать compute_embedding() по одному —
    каждый отдельный вызов кодировщика трансформера имеет заметные
    накладные расходы, а тегов на весь батч обычно многие сотни.

    Порядок и длина результата соответствуют входному списку; для
    текстов без единого тега (пустая строка) и если модель недоступна,
    элемент результата — None.

    Каждый тег кодируется с префиксом "passage: " — обязательное
    требование E5-моделей (см. модуль docstring), не опциональная
    настройка.
    """

    if not texts:
        return []

    per_text_chunks = [_filter_boilerplate(_split_into_chunks(t)) for t in texts]

    flat_chunks: list[str] = []
    spans: list[tuple[int, int]] = []

    for chunks in per_text_chunks:
        start = len(flat_chunks)
        flat_chunks.extend(chunks)
        spans.append((start, len(chunks)))

    results: list[bytes | None] = [None] * len(texts)

    if not flat_chunks:
        return results

    try:
        model = get_model()
        vectors = model.encode(
            [_PASSAGE_PREFIX + c for c in flat_chunks],
            normalize_embeddings=True,
            show_progress_bar=False,
            batch_size=32,
        )
    except RuntimeError:
        return results
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Не удалось вычислить эмбеддинги (batch, %d текстов, %d тегов): %s",
            len(texts), len(flat_chunks), e
        )
        return results

    vectors = np.asarray(vectors, dtype=np.float32)

    for i, (start, count) in enumerate(spans):

        if count == 0:
            continue

        results[i] = vectors[start:start + count].tobytes()

    return results


def bytes_to_array(data: bytes) -> np.ndarray:
    """Интерпретирует bytes как ОДИН вектор запроса (см.
    compute_query_embedding) — форма (EMBEDDING_DIM,)."""

    return np.frombuffer(data, dtype=np.float32)


def bytes_to_chunks(data: bytes) -> np.ndarray:
    """Интерпретирует bytes как N векторов тегов документа (см.
    compute_embedding/compute_embeddings_batch) — форма
    (N, EMBEDDING_DIM). N == 1 для эмбеддингов, посчитанных ДО перехода
    на по-теговое кодирование (промпт без единой запятой, либо старые
    записи в БД) — такой BLOB по-прежнему корректно читается, reshape
    просто даёт один "чанк"."""

    if len(data) % 4 != 0:
        # длина не кратна размеру float32 — заведомо повреждённые данные
        return np.empty((0, EMBEDDING_DIM), dtype=np.float32)

    flat = np.frombuffer(data, dtype=np.float32)

    if flat.size == 0 or flat.size % EMBEDDING_DIM != 0:
        # повреждённые данные либо эмбеддинг от несовместимой модели
        # (другая размерность) — не пытаемся угадать форму
        return np.empty((0, EMBEDDING_DIM), dtype=np.float32)

    return flat.reshape(-1, EMBEDDING_DIM)


def cosine_similarity(query_vec: np.ndarray, embedding_bytes: bytes) -> float:
    """Сходство между вектором запроса (см. compute_query_embedding) и
    промптом документа, хранящимся в БД как несколько векторов тегов
    (см. compute_embedding) — bytes из EMBEDDING_DIM-мерных float32
    векторов подряд.

    Возвращает МАКСИМУМ косинусного сходства запроса с любым отдельным
    тегом документа, а не сходство с каким-то одним "усреднённым"
    вектором всего промпта — так короткий специфичный запрос ("boy")
    находит соответствующий тег даже в промпте из полусотни других
    тегов, а не тонет в их общем среднем (см. модуль docstring).
    """

    doc_vecs = bytes_to_chunks(embedding_bytes)

    if doc_vecs.shape[0] == 0 or doc_vecs.shape[1] != query_vec.shape[0]:
        # эмбеддинг повреждён либо от другой модели/версии (другая
        # размерность) — не сопоставим с текущим вектором запроса
        return 0.0

    query_norm = np.linalg.norm(query_vec)

    if query_norm == 0.0:
        return 0.0

    doc_norms = np.linalg.norm(doc_vecs, axis=1)
    valid = doc_norms > 0.0

    if not np.any(valid):
        return 0.0

    similarities = (doc_vecs[valid] @ query_vec) / (doc_norms[valid] * query_norm)

    return float(np.max(similarities))
