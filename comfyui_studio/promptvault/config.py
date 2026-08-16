"""Константы приложения, вынесенные из отдельных модулей в одно место.

Если нужно подстроить производительность или поведение приложения —
начинать стоит отсюда, а не искать магические числа по всему коду.
"""

import sys
from pathlib import Path

APP_VERSION = "0.11.1"

# базовая директория пакета app/ — и в обычном запуске (python -m
# app.main), и внутри сборки PyInstaller (build.bat, задача: батник
# для сборки).
#
# ВАЖНО: Path(__file__).resolve().parent сам по себе НЕ надёжен внутри
# PyInstaller-сборки. Чистые .py-модули PyInstaller паковает в архив
# (PYZ) и подсовывает им синтетический __file__, который в некоторых
# версиях бутлоадера не совпадает с реальным расположением файлов на
# диске — из-за этого ICON_PATH.exists() в собранном .exe возвращал
# False, MainWindow._init_ (см. app/ui/main_window.py) тихо
# пропускал self.setWindowIcon(...), и приложение оставалось без
# иконки — в том числе в панели задач Windows. Сам PyInstaller как раз
# для этого документирует sys._MEIPASS (см. Run-time Information в
# его документации) — путь, куда бутлоадер реально распаковал (one-file)
# или где реально лежит (one-folder, _internal\...) всё, что попало
# туда через --add-data, и это ЕДИНСТВЕННЫЙ надёжный способ найти такие
# файлы в собранном виде. Вне сборки sys._MEIPASS не существует, и
# поведение остаётся прежним — Path(__file__).resolve().parent.
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    # После переноса пакета под общее пространство имён comfyui_studio
    # (этап 2 дорожной карты рефакторинга, ранее -- app/) данные лежат
    # в _MEIPASS/comfyui_studio/promptvault. Сам .spec ещё предстоит
    # обновить под новую раскладку -- см. этап 5 дорожной карты.
    _APP_DIR = Path(sys._MEIPASS) / "comfyui_studio" / "promptvault"
else:
    _APP_DIR = Path(__file__).resolve().parent

# app/resources/icon.png относительно _APP_DIR (см. её docstring выше)
ICON_PATH = _APP_DIR / "resources" / "icon.png"

# app/resources/translations/promptvault_{lang}.qm — скомпилированные
# переводы интерфейса (задача: полный аудит строк UI под self.tr(),
# см. app/i18n.py и tools/fill_translations.py); .ts лежит рядом как
# исходник для перевода, .qm — то, что реально грузит QTranslator
TRANSLATIONS_DIR = _APP_DIR / "resources" / "translations"


# ------------------------------------------------------------------
# Пути

APP_DATA_DIR = Path.home() / ".promptvault"
DB_PATH = APP_DATA_DIR / "promptvault.db"
LOG_DIR = APP_DATA_DIR / "logs"
THUMBNAIL_CACHE_DIR = APP_DATA_DIR / "thumbnails"


# ------------------------------------------------------------------
# GenerationList (app/ui/generation_list.py)

# примерная высота одной карточки генерации в списке; используется как
# sizeHint для ВСЕХ элементов списка ещё до создания реальных виджетов —
# это и позволяет не создавать виджеты заранее для невидимых строк
# (виртуализация)
CARD_HEIGHT = 130

# запас строк сверху/снизу видимой области, для которых виджеты
# создаются заранее — сглаживает скролл, не давая виджетам
# пересоздаваться на каждый мелкий скролл-тик
BUFFER_ROWS = 8


# ------------------------------------------------------------------
# GalleryManager (app/core/gallery_manager.py)

# сколько ждать после последнего клика по избранному/рейтингу перед
# пересборкой списка (пересортировкой) — без этого debounce каждый
# клик синхронно пересоздавал весь список и заметно лагал на больших
# библиотеках; клики сами по себе применяются мгновенно, откладывается
# только дорогостоящая пересортировка/перерисовка
REFRESH_DEBOUNCE_MS = 400

# размер одной "страницы" при ленивой (постраничной) загрузке генераций
# из БД — см. GenerationRepository.load_filtered_page и
# GalleryManager.load_more_filtered (задача: настоящая виртуальная
# пагинация). Первая страница загружается сразу при открытии папки
# (независимо от общего размера библиотеки), остальные — по требованию,
# когда пользователь долистывает список до уже показанного конца (см.
# GenerationList.moreNeeded) — а не заранее все разом в фоне.
GENERATIONS_PAGE_SIZE = 500


# ------------------------------------------------------------------
# Автоочистка (app/core/thumbnails.py, app/core/logger.py)

# миниатюры/логи старше этого возраста (в днях) удаляются при старте
# приложения (см. app/main.py)
THUMBNAIL_MAX_AGE_DAYS = 30
LOG_MAX_AGE_DAYS = 30

# суммарный размер кэша миниатюр/папки логов (в байтах), при
# превышении которого удаляются самые старые файлы (по mtime), пока
# размер не опустится до лимита — защищает от неограниченного роста
# на очень больших библиотеках даже в пределах MAX_AGE_DAYS
THUMBNAIL_CACHE_MAX_BYTES = 500 * 1024 * 1024  # 500 МБ
LOG_DIR_MAX_BYTES = 50 * 1024 * 1024  # 50 МБ


# ------------------------------------------------------------------
# StatisticsWindow (app/ui/statistics_window.py)

# количество столбцов в топ-N диаграммах моделей/сэмплеров/LoRA
STATISTICS_TOP_N = 10

# количество корзин (bins) в гистограммах CFG/Steps
STATISTICS_HISTOGRAM_BUCKETS = 10


# ------------------------------------------------------------------
# FolderSync (app/core/folder_sync.py)

# период резервной периодической синхронизации папки — гарантированно
# подхватывает изменения (в т.ч. новые подпапки), которые мог
# пропустить QFileSystemWatcher
POLL_INTERVAL_MS = 4000


# ------------------------------------------------------------------
# StarRatingWidget (app/ui/star_rating.py)

MIN_RATING = 0
MAX_RATING = 5


# ------------------------------------------------------------------
# Семантический поиск (app/core/embedding.py, app/core/generation_filter.py)

# минимальное косинусное сходство между эмбеддингом запроса и
# эмбеддингом промпта, при котором генерация считается семантическим
# совпадением. Эмбеддинги L2-нормализованы, так что сходство лежит в
# [-1, 1].
#
# История подбора (диапазон осмысленных значений у каждой модели свой
# — значения ниже не сравнимы между собой при смене MODEL_NAME):
#   0.35 — paraphrase-multilingual-MiniLM-L12-v2 (задача 3.1, слишком мягко)
#   0.48 — та же модель, после устранения источников шума (см. embedding.py)
#   0.6  — paraphrase-multilingual-mpnet-base-v2 / multilingual-e5-large
#   0.85 — intfloat/e5-large-v2 (см. embedding.py) — ТЕКУЩАЯ модель,
#          англоязычная (см. её docstring: сознательный отказ от
#          поддержки русского в СЕМАНТИЧЕСКОМ поиске ради качества на
#          английском — обычный текстовый поиск по подстроке
#          русский по-прежнему поддерживает)
SEMANTIC_SIMILARITY_THRESHOLD = 0.84


# ------------------------------------------------------------------
# Выбор модели эмбеддингов (app/core/embedding.py, окно настроек) —
# реестр поддерживаемых моделей: характеристики для UI настроек
# (ram_mb/quality/speed/recommendation) и технические параметры
# (размерность вектора, обязательные префиксы запрос/документ,
# порог семантического сходства — свой для каждой модели, диапазон
# осмысленных значений косинусного сходства у разных моделей не
# сравним, см. SEMANTIC_SIMILARITY_THRESHOLD выше и docstring
# embedding.py).
#
# quality/speed/recommendation — НЕ готовый текст для показа
# пользователю, а нейтральные ключи (задача: полный аудит строк UI
# под self.tr()) — сам текст на нужном языке собирает UI-слой (см.
# SettingsWindow._quality_label/_speed_label/_recommendation_label в
# app/ui/settings_window.py). Раньше здесь лежал готовый русский
# текст ("Отличное", "Средняя" и т.п.) — из-за этого при выбранном
# английском интерфейсе селектор модели эмбеддинга всё равно
# показывал русский: это лежало в core-слое (app/config.py), а не в
# UI, и self.tr() до него никак не добирался.
EMBEDDING_MODELS: dict[str, dict] = {
    "e5-large-v2": {
        "repo_id": "intfloat/e5-large-v2",
        "dim": 1024,
        "ram_mb": 1300,
        "quality": "excellent",
        "speed": "medium",
        "recommendation": "best_default",
        "query_prefix": "query: ",
        "passage_prefix": "passage: ",
        "similarity_threshold": 0.84,
    },
    "e5-base-v2": {
        "repo_id": "intfloat/e5-base-v2",
        "dim": 768,
        "ram_mb": 440,
        "quality": "very_good",
        "speed": "faster",
        "recommendation": "good_balance",
        "query_prefix": "query: ",
        "passage_prefix": "passage: ",
        "similarity_threshold": 0.84,
    },
    "bge-base-en-v1.5": {
        "repo_id": "BAAI/bge-base-en-v1.5",
        "dim": 768,
        "ram_mb": 420,
        "quality": "excellent",
        "speed": "faster",
        "recommendation": "great_alternative",
        # bge использует другую схему префиксов, чем E5: только запрос
        # получает инструктирующий префикс, документ — без префикса
        # (см. официальную карточку модели на HuggingFace)
        "query_prefix": "Represent this sentence for searching relevant passages: ",
        "passage_prefix": "",
        # диапазон косинусного сходства у bge заметно ниже, чем у
        # e5-large-v2 — эмпирически подобран менее тщательно, чем 0.84
        "similarity_threshold": 0.75,
    },
    "all-MiniLM-L6-v2": {
        "repo_id": "sentence-transformers/all-MiniLM-L6-v2",
        "dim": 384,
        "ram_mb": 90,
        "quality": "good",
        "speed": "very_fast",
        "recommendation": "for_weak_machines",
        # не асимметричная retrieval-модель — префиксов не требует
        "query_prefix": "",
        "passage_prefix": "",
        "similarity_threshold": 0.5,
    },
    "e5-small-v2": {
        "repo_id": "intfloat/e5-small-v2",
        "dim": 384,
        "ram_mb": 130,
        "quality": "good",
        "speed": "fast",
        "recommendation": "tradeoff",
        "query_prefix": "query: ",
        "passage_prefix": "passage: ",
        "similarity_threshold": 0.84,
    },
}

# ключ модели по умолчанию (см. EMBEDDING_MODELS) — та же модель, что
# использовалась до появления выбора модели в настройках
DEFAULT_EMBEDDING_MODEL = "e5-large-v2"
