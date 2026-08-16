"""Presenter/Manager слой между UI (MainWindow) и данными (GenerationRepository).

MainWindow должен только создавать виджеты, подключать сигналы и
обрабатывать чисто UI-события (горячие клавиши и т.п.) — вся логика
загрузки, фильтрации, сортировки и мутации избранного/рейтинга живёт
здесь, в GalleryManager.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QSettings, QTimer, Signal

from app.config import (
    DEFAULT_EMBEDDING_MODEL,
    GENERATIONS_PAGE_SIZE,
    REFRESH_DEBOUNCE_MS,
)
from app.core import embedding
from app.core import generation_filter as _generation_filter_module
from app.core.generation import Generation
from app.core.generation_filter import FilterOptions, GenerationFilter
from app.core.repository import GenerationRepository
from app.core.sort_options import SortMode
from app.core.statistics import Statistics, compute_statistics

logger = logging.getLogger(__name__)


class GalleryManager(QObject):
    """Владеет состоянием галереи и всей логикой вокруг него.

    Сигналы:
        generations_changed: отфильтрованный/отсортированный список
            изменился — UI должен перечитать filtered_generations()
            и перестроить список карточек.
        selection_changed: изменилась текущая выбранная генерация
            (несёт саму Generation или None, если выбор снят).
        filter_changed: изменились параметры фильтрации или сортировки
            (полезно, например, для обновления счётчиков в UI).
        metadata_updated: метаданные генерации отредактированы и
            сохранены (несёт саму Generation с уже свежими данными).
        metadata_updated_hidden_by_filters: то же самое, что и
            metadata_updated (сохранение прошло успешно), но
            отредактированная генерация после этого перестала
            проходить текущие фильтры и пропала из видимого списка —
            UI может сообщить об этом пользователю, а не молча
            "потерять" карточку, которую он только что редактировал.
        bulk_metadata_updated: то же самое, что metadata_updated, но
            для массового редактирования (см. update_generations_metadata)
            — несёт список id генераций, для которых сохранение
            прошло успешно (уже актуальные Generation можно взять из
            self.generations/filtered_generations после этого сигнала,
            apply_filters() к этому моменту уже отработал).
        error_occurred: операция не удалась (несёт человекочитаемое
            сообщение) — UI может показать QMessageBox.
    """

    generations_changed = Signal()
    # см. load_more_filtered — несёт только НОВУЮ подгруженную страницу
    # (list[Generation]), а не весь filtered_generations целиком, чтобы
    # UI (GenerationList.append_generations) мог расширить уже
    # показанный список, не пересобирая его с нуля
    more_generations_loaded = Signal(list)
    selection_changed = Signal(object)
    filter_changed = Signal()
    metadata_updated = Signal(object)
    metadata_updated_hidden_by_filters = Signal(object)
    bulk_metadata_updated = Signal(list)
    error_occurred = Signal(str)

    def __init__(
        self,
        repository: GenerationRepository,
        parent: QObject | None = None,
    ):
        super().__init__(parent)

        self._repository = repository
        self._settings = QSettings("PromptVault", "PromptVault")

        # ВАЖНО (задача: настоящая виртуальная пагинация): filtered_generations
        # больше не Python-срез из отдельного self.generations (весь
        # список папки целиком в памяти) — сам SQL уже возвращает
        # отфильтрованный и отсортированный результат (см.
        # GenerationFilterSQL/GenerationSorterSQL/apply_filters), и в
        # памяти держится только то, что реально уже показано —
        # растёт постранично по запросу (см. load_more_filtered), а не
        # предзагружается целиком в фоне, как раньше.
        #
        # self.generations — тот же объект списка (см. _set_filtered),
        # оставлен как синоним ради обратной совместимости кода/тестов,
        # ожидающих старое имя — раздельными их держать незачем, теперь
        # это буквально одно и то же (SQL уже отфильтровал и
        # отсортировал за один проход, второго Python-прохода над
        # "полным списком" больше нет).
        self.filtered_generations: list[Generation] = []
        self.generations: list[Generation] = self.filtered_generations

        # см. filtered_total() — сколько генераций всего проходит
        # текущие фильтры (может быть больше len(filtered_generations),
        # пока не все страницы подгружены)
        self._filtered_total = 0
        self._loading_more = False

        # при активном семантическом поиске ранжировать по сходству
        # приходится сразу над ВСЕМ набором кандидатов (см.
        # GenerationRepository.load_filtered_for_semantic и
        # GenerationFilter.rank_by_semantic_query — векторное сходство
        # не выразить обычным SQL LIMIT/OFFSET) — здесь хранится этот
        # уже посчитанный полный ранжированный список, чтобы
        # load_more_filtered() просто "раздавал" из него следующий кусок,
        # не пересчитывая ранжирование заново на каждую подгружаемую
        # страницу
        self._semantic_ranked: list[Generation] | None = None

        self.current_generation: Generation | None = None
        self.current_folder: str | None = None
        self._closed = False

        # кэш доступных значений для попапа фильтров (задача 3.3) —
        # считается через дешёвые SQL DISTINCT-запросы (см.
        # GenerationRepository.available_models и т.п.), а не
        # построением set() над self.generations на каждый вызов;
        # инвалидируется при любом изменении содержимого папки
        self._available_models_cache: set[str] = set()
        self._available_samplers_cache: set[str] = set()
        self._available_loras_cache: set[str] = set()
        self._available_custom_tags_cache: set[str] = set()
        self._available_cache_dirty = True

        self._filter_options = self._load_filter_state()
        self._sort_mode = self._load_sort_state()

        # семантический поиск (задача: оптимизация памяти) — модель
        # эмбеддингов (~1.3 ГБ весов) грузится лениво при первом реальном
        # обращении (см. app/core/embedding.py), поэтому применяем
        # сохранённый пользовательский выбор ДО первого load_folder —
        # если пользователь ранее отключил семантический поиск, модель
        # не загрузится вообще ни разу за время жизни процесса
        #
        # порядок важен (задача: выбор модели эмбеддинга): сначала
        # применяем выбор МОДЕЛИ (определяет MODEL_NAME/EMBEDDING_DIM/
        # префиксы) — кроме случая "без модели", когда модель
        # полностью отключается и это состояние ничем ниже не
        # перезаписывается; иначе (модель выбрана) применяем отдельный
        # флаг "включён ли поиск вообще", который может временно
        # выключать поиск, не теряя при этом выбранную модель
        embedding_model_key = self._load_embedding_model_key()
        embedding.set_model(embedding_model_key)

        if embedding_model_key is not None:
            embedding.set_enabled(self._load_semantic_search_enabled_state())

        embedding.set_device_preference(self.device_preference())

        _generation_filter_module.SEMANTIC_SIMILARITY_THRESHOLD = (
            embedding.current_similarity_threshold()
        )

        # см. REFRESH_DEBOUNCE_MS — быстрые повторные клики по
        # избранному/рейтингу не должны пересобирать список на каждый
        # клик, поэтому пересортировка/перерисовка откладывается и
        # схлопывает все клики за интервал в один пересчёт
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(REFRESH_DEBOUNCE_MS)
        self._refresh_timer.timeout.connect(self.apply_filters)

    # ------------------------------------------------------------
    # загрузка папки

    def load_folder(self, folder: str) -> None:
        """Открывает папку: синхронизирует БД с диском и загружает
        первую отфильтрованную страницу (см. apply_filters).

        Первый вызов для новой папки может быть небыстрым (парсинг всех
        JSON-файлов), повторные — почти мгновенные, т.к. пересканируются
        только новые/изменившиеся файлы (см. GenerationRepository.sync_folder).

        Сам список из БД в память загружается лениво/постранично (см.
        apply_filters/load_more_filtered): сразу отображается только
        первая страница текущего фильтра/сортировки, остальные
        подгружаются по требованию (прокрутка списка), а не заранее
        целиком в фоне — вся папка разом в памяти не держится даже для
        очень больших библиотек (задача: настоящая виртуальная
        пагинация).
        """

        folder = str(folder)

        logger.info("Открытие папки: %s", folder)

        self.current_generation = None
        self.current_folder = folder

        # задача: сохранение пути к папке просмотра между сессиями —
        # запоминаем КАЖДУЮ успешно открытую папку (перезаписывая
        # предыдущую), не только выбранную вручную через диалог, так
        # что при следующем запуске MainWindow._restore_last_folder
        # открывает именно ту папку, что была открыта последней —
        # неважно, через диалог или уже через восстановление с
        # прошлого раза. Пишем ДО sync_folder/apply_filters ниже —
        # даже если сама загрузка упадёт на середине (см. except
        # OSError в MainWindow._open_folder_path), путь всё равно
        # успеет сохраниться, и следующий запуск попробует ту же
        # папку снова, а не откатится молча к пустому состоянию.
        self._settings.setValue("last_folder", folder)

        self._repository.sync_folder(folder)

        # догоняет эмбеддинги для записей, которые sync_folder не тронул,
        # потому что их JSON-файл не менялся — унаследованные от версии
        # приложения до задачи 3.1 записи и записи, для которых модель
        # эмбеддингов была недоступна при первой синхронизации (см.
        # docstring backfill_missing_embeddings). Ограничено батчем,
        # так что при очень большой библиотеке не блокирует открытие
        # папки надолго — библиотека дозаполняется постепенно, при
        # каждом следующем открытии/пересинхронизации.
        self._repository.backfill_missing_embeddings()

        self._invalidate_available_cache()
        self.apply_filters()

    def resync(self) -> None:
        """Перечитывает список из БД без повторного сканирования диска.

        Вызывается в ответ на FolderSync.changed — автосинхронизация уже
        обновила БД сама, здесь только подхватываем свежие данные в UI.
        """

        logger.debug("Перечитывание списка после автосинхронизации")

        self._invalidate_available_cache()
        self.apply_filters()

    # ------------------------------------------------------------
    # доступные значения для фильтров (модели/сэмплеры/lora), с кэшем

    def _invalidate_available_cache(self) -> None:

        self._available_cache_dirty = True

    def _ensure_available_cache(self) -> None:

        if not self._available_cache_dirty or self.current_folder is None:
            return

        self._available_models_cache = self._repository.available_models(self.current_folder)
        self._available_samplers_cache = self._repository.available_samplers(self.current_folder)
        self._available_loras_cache = self._repository.available_loras(self.current_folder)
        self._available_custom_tags_cache = self._repository.available_custom_tags(
            self.current_folder
        )

        self._available_cache_dirty = False

    def available_models(self) -> set[str]:

        self._ensure_available_cache()
        return self._available_models_cache

    def available_samplers(self) -> set[str]:

        self._ensure_available_cache()
        return self._available_samplers_cache

    def available_loras(self) -> set[str]:

        self._ensure_available_cache()
        return self._available_loras_cache

    def available_custom_tags(self) -> set[str]:

        self._ensure_available_cache()
        return self._available_custom_tags_cache

    # ------------------------------------------------------------
    # фильтры и сортировка

    def set_search(self, text: str) -> None:
        """Устанавливает текст поиска (несколько слов ищутся через И —
        см. GenerationFilter) и сразу применяет фильтры. Не сохраняется
        между запусками приложения (см. _load_filter_state)."""

        self._filter_options.search = text
        self.apply_filters()

    def set_semantic_search(self, text: str) -> None:
        """Устанавливает запрос семантического (векторного) поиска —
        см. GenerationFilter/embedding.py — и сразу применяет фильтры.

        Отдельно от set_search(): комбинируется с обычным текстовым
        поиском и остальными фильтрами через И, а не заменяет их. Не
        сохраняется между запусками приложения (см. _load_filter_state).
        """

        self._filter_options.semantic_query = text
        self.apply_filters()

    def set_filter_options(self, options: FilterOptions) -> None:
        """Заменяет все параметры фильтрации разом (используется
        попапом фильтров, где несколько полей меняются одновременно
        перед нажатием Apply) и сразу применяет. Не сохраняется между
        запусками приложения (см. _load_filter_state) — при следующем
        запуске фильтры и поиск снова пустые."""

        self._filter_options = options
        self.apply_filters()

    def filter_options(self) -> FilterOptions:

        return self._filter_options

    def set_sort_mode(self, mode: SortMode) -> None:
        """Устанавливает режим сортировки, сохраняет его в QSettings и
        сразу применяет."""

        self._sort_mode = mode
        self._settings.setValue("sort_mode", mode.name)
        self.apply_filters()

    def sort_mode(self) -> SortMode:

        return self._sort_mode

    # ------------------------------------------------------------
    # семантический поиск: вкл/выкл (задача: оптимизация памяти)

    def set_semantic_search_enabled(self, enabled: bool) -> None:
        """Включает/выключает семантический поиск целиком и запоминает
        выбор в QSettings (переживает перезапуск приложения).

        При отключении модель эмбеддингов (~1.3 ГБ весов) не будет
        загружена ни разу за время жизни процесса — ни при
        синхронизации папки, ни при досчитывании отсутствующих
        эмбеддингов (см. app.core.embedding.set_enabled). Уже
        загруженную модель это НЕ выгружает из памяти (простого способа
        освободить память CPU-модели PyTorch обратно ОС нет) — эффект
        полный только если применить настройку ДО первого использования
        семантического поиска в этом запуске приложения.
        """

        embedding.set_enabled(enabled)
        self._settings.setValue("semantic_search_enabled", enabled)

    def semantic_search_enabled(self) -> bool:

        return self._load_semantic_search_enabled_state()

    def _load_semantic_search_enabled_state(self) -> bool:

        value = self._settings.value("semantic_search_enabled", True)

        if isinstance(value, str):
            return value.lower() not in ("false", "0", "")

        return bool(value)

    # ------------------------------------------------------------
    # выбор модели эмбеддинга и устройства (CPU/GPU) — задача:
    # настройка модели эмбеддинга

    def available_embedding_models(self) -> dict[str, dict]:
        """Реестр поддерживаемых моделей (см. EMBEDDING_MODELS в
        app/config.py) — для построения списка выбора в SettingsWindow."""

        return embedding.available_models()

    def embedding_model_key(self) -> str | None:
        """Текущий сохранённый выбор модели эмбеддинга, либо None,
        если выбран вариант "без модели" (семантический поиск
        полностью отключён)."""

        return self._load_embedding_model_key()

    def set_embedding_model(self, model_key: str | None) -> None:
        """Переключает модель эмбеддинга (см. EMBEDDING_MODELS) и
        запоминает выбор в QSettings (переживает перезапуск).

        Старые эмбеддинги в БД остаются посчитанными ПРЕЖНЕЙ моделью —
        разные модели дают несовместимые векторы (разное пространство,
        даже при одинаковой размерности), поэтому после смены модели
        нужен полный пересчёт (см. recompute_all_embeddings) —
        предложить его пользователю должен вызывающий UI-код
        (SettingsWindow), само переключение модели пересчёт не
        запускает автоматически.
        """

        embedding.set_model(model_key)

        self._settings.setValue("embedding_model", model_key or "")

        # выбор конкретной модели явно подразумевает включённый поиск;
        # "без модели" — наоборот, полное отключение (см. порядок
        # применения в __init__)
        self._settings.setValue("semantic_search_enabled", model_key is not None)

        _generation_filter_module.SEMANTIC_SIMILARITY_THRESHOLD = (
            embedding.current_similarity_threshold()
        )

    def _load_embedding_model_key(self) -> str | None:

        value = self._settings.value("embedding_model", DEFAULT_EMBEDDING_MODEL)
        value = str(value)

        # пустая строка — сохранённый пользователем выбор "без модели"
        return value if value else None

    def device_preference(self) -> str:
        """Предпочтение устройства для модели эмбеддинга: "auto"
        (по умолчанию), "cpu" или "cuda"."""

        return str(self._settings.value("embedding_device", "auto"))

    def set_device_preference(self, preference: str) -> None:
        """См. embedding.set_device_preference — принудительный выбор
        "cuda" без установленного torch с CUDA-сборкой молча
        откатывается на CPU (см. gpu_available для предупреждения в UI)."""

        embedding.set_device_preference(preference)
        self._settings.setValue("embedding_device", preference)

    def gpu_available(self) -> bool:
        """Чисто информационная проверка для UI (SettingsWindow) —
        установлен ли torch с CUDA-сборкой и физически доступен ли GPU."""

        return embedding.gpu_available()

    def recompute_all_embeddings(self) -> int:
        """Полный пересчёт эмбеддингов ВСЕХ генераций в БД текущей
        моделью (например, после смены модели эмбеддинга) — см.
        GenerationRepository.recompute_all_embeddings.

        Может быть небыстрым для больших библиотек — вызывающий UI-код
        должен предупредить пользователя об этом до вызова. Возвращает
        количество пересчитанных генераций."""

        total = self._repository.recompute_all_embeddings()

        logger.info("Пересчитаны эмбеддинги для %d генераций", total)

        if self.current_folder is not None:
            self.apply_filters()

        return total

    # ------------------------------------------------------------
    # производительность: размер страницы ленивой загрузки (задача 3.3,
    # настраивается через SettingsWindow)

    def generations_page_size(self) -> int:

        return self._page_size()

    def set_generations_page_size(self, value: int) -> None:
        """Меняет размер страницы для СЛЕДУЮЩЕЙ загрузки папки —
        уже загруженную текущую папку не перестраивает задним числом."""

        self._settings.setValue("performance/page_size", int(value))

    def _page_size(self) -> int:

        value: Any = self._settings.value("performance/page_size", GENERATIONS_PAGE_SIZE)
        return int(value)

    # ------------------------------------------------------------
    # последняя открытая папка (задача: сохранение пути к папке
    # просмотра между сессиями) — сама запись происходит в
    # load_folder(), здесь только чтение для MainWindow, который
    # решает, восстанавливать ли её при старте (см.
    # MainWindow._restore_last_folder — папка могла с тех пор
    # исчезнуть/переименоваться, это забота UI-слоя, не этого метода)

    def last_folder(self) -> str | None:

        value = self._settings.value("last_folder", None)

        if not value:
            return None

        return str(value)

    # ------------------------------------------------------------
    # фильтры (не сохраняются между запусками) / сортировка (сохраняется,
    # QSettings)

    def _load_filter_state(self) -> FilterOptions:
        """Фильтры и поиск (в т.ч. семантический) больше НЕ сохраняются
        между запусками приложения — каждая новая сессия начинается с
        пустых FilterOptions по умолчанию, независимо от того, что
        было выставлено в прошлый раз (сортировка — set_sort_mode —
        и остальные настройки в QSettings это не затрагивает)."""

        return FilterOptions()

    def _load_sort_state(self) -> SortMode:

        name = str(self._settings.value("sort_mode", SortMode.NEWEST.name))

        try:
            return SortMode[name]
        except KeyError:
            return SortMode.NEWEST

    # ------------------------------------------------------------

    def apply_filters(self) -> None:
        """Загружает первую страницу текущей папки заново из БД с
        учётом фильтров (FilterOptions) и сортировки (SortMode) —
        целиком в SQL (см. GenerationFilterSQL/GenerationSorterSQL),
        вместо построчного разбора в Python над уже загруженным в
        память полным списком генераций папки (задача: перенос
        GenerationFilter/GenerationSorter на SQL).

        Остальные страницы отфильтрованного результата в память не
        грузятся заранее — только по требованию, см.
        load_more_filtered() (задача: настоящая виртуальная
        пагинация) — вызывается из UI при прокрутке списка к уже
        показанному концу (см. GenerationList.moreNeeded).

        Единственное исключение — активный семантический поиск:
        векторное сходство не выразить обычным SQL, так что кандидаты
        (уже суженные SQL по всем ОСТАЛЬНЫМ условиям) приходится
        получить и проранжировать в Python целиком за один раз (см.
        GenerationRepository.load_filtered_for_semantic и
        GenerationFilter.rank_by_semantic_query) — load_more_filtered()
        в этом случае просто раздаёт уже посчитанный результат по
        страницам, а не запрашивает БД заново на каждую.

        В конце пытается сохранить текущий выбор (по id, см.
        select_generation)."""

        # если пересчёт запущен не через сам _refresh_timer (например,
        # resync() после автосинхронизации или explicit set_filter_options
        # прямо посреди отложенного клика по рейтингу/избранному), то
        # ранее запланированный debounce больше не нужен — эта
        # пересборка его уже покрывает. Не остановить его — значит
        # рискнуть лишним, запоздалым apply_filters() чуть позже,
        # который может дёрнуть список в неожиданный момент.
        self._refresh_timer.stop()

        # запоминаем текущую выделенную генерацию по её id (а не по
        # identity объекта — после перезагрузки из БД это уже новые
        # объекты), чтобы клик по звезде/избранному или автосинхронизация
        # не "телепортировали" пользователя на другую карточку
        previous_id = (
            self.current_generation.id
            if self.current_generation is not None
            else None
        )

        self._semantic_ranked = None

        if self.current_folder is None:
            self._set_filtered([])
            self._filtered_total = 0
            self.select_generation(None)
            self.generations_changed.emit()
            self.filter_changed.emit()
            return

        page_size = self._page_size()
        semantic_query = self._filter_options.semantic_query.strip()

        if semantic_query:

            candidates = self._repository.load_filtered_for_semantic(
                self.current_folder, self._filter_options, self._sort_mode
            )

            # при активном семантическом поиске обычная сортировка
            # (по дате/модели/CFG и т.п.) намеренно пропускается —
            # ranked уже упорядочен по убыванию релевантности (или, в
            # деградированном режиме без модели эмбеддингов, сохраняет
            # порядок candidates, т.е. sort_mode из SQL-запроса выше —
            # см. GenerationFilter.rank_by_semantic_query)
            ranked = GenerationFilter.rank_by_semantic_query(candidates, semantic_query)

            self._semantic_ranked = ranked
            total = len(ranked)
            first_page = ranked[:page_size]

        else:
            total = self._repository.count_filtered(self.current_folder, self._filter_options)
            first_page = self._repository.load_filtered_page(
                self.current_folder, self._filter_options, self._sort_mode,
                offset=0, limit=page_size
            )

        self._filtered_total = total
        self._set_filtered(first_page)

        logger.info(
            "Применены фильтры: %d подходит, загружена первая страница (%d)",
            total, len(first_page)
        )

        new_selection = None

        if previous_id is not None:
            new_selection = next(
                (g for g in first_page if g.id == previous_id),
                None
            )

        if new_selection is None and first_page:
            new_selection = first_page[0]

        # ВАЖНО: выбор обновляется ДО generations_changed — иначе
        # обработчики UI, реагирующие на generations_changed (например,
        # перестройка списка карточек с подсветкой текущей строки),
        # увидят ещё не обновившийся current_generation
        self.select_generation(new_selection)

        self.generations_changed.emit()
        self.filter_changed.emit()

    def _set_filtered(self, generations: list[Generation]) -> None:
        """generations и filtered_generations всегда указывают на один
        и тот же объект списка (см. __init__) — единственное место,
        где этот список заменяется целиком (по требованию, при смене
        страницы — см. load_more_filtered() для роста ЭТОГО ЖЕ списка
        без замены объекта)."""

        self.filtered_generations = generations
        self.generations = generations

    def filtered_total(self) -> int:
        """Сколько генераций всего проходит текущие фильтры — может
        быть больше len(filtered_generations), пока не все страницы
        подгружены (см. load_more_filtered)."""

        return self._filtered_total

    def load_more_filtered(self) -> bool:
        """Подгружает ещё одну "страницу" уже отфильтрованного и
        отсортированного результата (задача: настоящая виртуальная
        пагинация) — вызывается UI (см. GenerationList.moreNeeded) при
        прокрутке списка к уже показанному концу, а не заранее в фоне
        для всей папки целиком, как раньше.

        Возвращает True, если что-то подгрузилось (стоит перечитать
        filtered_generations и материализовать новые карточки), False —
        если подгружать больше нечего (в т.ч. если сейчас не открыта
        ни одна папка) или подгрузка уже идёт."""

        if self._loading_more or self.current_folder is None:
            return False

        loaded = len(self.filtered_generations)

        if loaded >= self._filtered_total:
            return False

        self._loading_more = True

        try:
            page_size = self._page_size()

            if self._semantic_ranked is not None:
                # уже полностью ранжировано в памяти (см. apply_filters)
                # — просто отдаём следующий кусок, без обращения к БД
                page = self._semantic_ranked[loaded:loaded + page_size]
            else:
                page = self._repository.load_filtered_page(
                    self.current_folder, self._filter_options, self._sort_mode,
                    offset=loaded, limit=page_size
                )

            if not page:
                return False

            self.filtered_generations.extend(page)
            # self.generations — тот же объект (см. _set_filtered), уже
            # отражает добавленную страницу

            logger.debug(
                "Подгружена страница отфильтрованного списка: +%d "
                "(всего загружено %d из %d)",
                len(page), len(self.filtered_generations), self._filtered_total
            )

            self.more_generations_loaded.emit(page)

            return True

        finally:
            self._loading_more = False

    # ------------------------------------------------------------
    # выбор текущей генерации

    def select_by_index(self, index: int) -> None:
        """Выбирает генерацию по индексу в filtered_generations
        (соответствует индексу строки в GenerationList)."""

        if 0 <= index < len(self.filtered_generations):
            self.select_generation(self.filtered_generations[index])

    def select_generation(self, generation: Generation | None) -> None:

        self.current_generation = generation
        self.selection_changed.emit(generation)

    def get_current_generation(self) -> Generation | None:

        return self.current_generation

    def current_index(self) -> int:
        """Индекс текущей выбранной генерации в filtered_generations,
        либо -1, если ничего не выбрано или выбор не входит в текущий
        отфильтрованный список."""

        if self.current_generation is None:
            return -1

        for i, g in enumerate(self.filtered_generations):
            if g is self.current_generation:
                return i

        return -1

    def _find_by_id(self, generation_id: int) -> Generation | None:

        return next(
            (g for g in self.generations if g.id == generation_id),
            None
        )

    # ------------------------------------------------------------
    # избранное / рейтинг (одна генерация)

    def toggle_favorite(self, generation_id: int) -> None:
        """Переключает избранное у генерации с данным id.

        Персистентность — одна быстрая точечная SQL-команда (не
        блокирует UI). Пересортировка (избранные поднимаются наверх)
        не выполняется немедленно, а планируется через debounce —
        см. _refresh_timer.
        """

        generation = self._find_by_id(generation_id)

        if generation is None:
            logger.warning("toggle_favorite: генерация id=%s не найдена", generation_id)
            return

        generation.favorite = not generation.favorite

        logger.debug(
            "Избранное для id=%s -> %s", generation_id, generation.favorite
        )

        self._repository.set_favorite(generation_id, generation.favorite)
        self._refresh_timer.start()

    def set_rating(self, generation_id: int, value: int) -> None:
        """Выставляет рейтинг (0-5) генерации с данным id."""

        generation = self._find_by_id(generation_id)

        if generation is None:
            logger.warning("set_rating: генерация id=%s не найдена", generation_id)
            return

        generation.rating = value

        logger.debug("Рейтинг для id=%s -> %s", generation_id, value)

        self._repository.set_rating(generation_id, value)
        self._refresh_timer.start()

    # ------------------------------------------------------------
    # пользовательские теги (задача: пользовательские теги)

    def set_custom_tags(self, generation_id: int, tags: list[str]) -> None:
        """Полностью заменяет пользовательские теги одной генерации
        (см. GenerationRepository.set_custom_tags) и обновляет её
        in-memory представление."""

        generation = self._find_by_id(generation_id)

        self._repository.set_custom_tags(generation_id, tags)
        refreshed_tags = self._repository.get_custom_tags(generation_id)

        if generation is not None:
            generation.custom_tags = refreshed_tags

        logger.debug("Теги для id=%s -> %s", generation_id, refreshed_tags)

        self._invalidate_available_cache()
        self._refresh_timer.start()

    def add_tags_to_generations(self, generation_ids: list[int], tags: list[str]) -> None:
        """Массовое добавление тега(ов) сразу нескольким выделенным
        генерациям (задача: пользовательские теги, поддержка массового
        выделения) — в отличие от set_custom_tags, ОБЪЕДИНЯЕТ новые
        теги с уже существующими у каждой генерации, а не заменяет их."""

        count = 0

        for gid in generation_ids:

            generation = self._find_by_id(gid)

            existing = (
                generation.custom_tags if generation is not None
                else self._repository.get_custom_tags(gid)
            )

            self._repository.set_custom_tags(gid, [*existing, *tags])
            refreshed_tags = self._repository.get_custom_tags(gid)

            if generation is not None:
                generation.custom_tags = refreshed_tags

            count += 1

        logger.info(
            "Массовое добавление тегов %s: %d генераций", tags, count
        )

        self._invalidate_available_cache()
        self._refresh_timer.start()

    # ------------------------------------------------------------
    # массовые операции

    def set_multiple_favorite(self, generation_ids: list[int], value: bool) -> None:
        """Проставляет/снимает избранное сразу у нескольких генераций."""

        count = 0

        for gid in generation_ids:

            generation = self._find_by_id(gid)

            if generation is None:
                continue

            generation.favorite = value
            self._repository.set_favorite(gid, value)
            count += 1

        logger.info(
            "Массовое изменение избранного: %d генераций -> %s", count, value
        )

        self._refresh_timer.start()

    def set_multiple_rating(self, generation_ids: list[int], value: int) -> None:
        """Выставляет одинаковый рейтинг сразу нескольким генерациям."""

        count = 0

        for gid in generation_ids:

            generation = self._find_by_id(gid)

            if generation is None:
                continue

            generation.rating = value
            self._repository.set_rating(gid, value)
            count += 1

        logger.info(
            "Массовое изменение рейтинга: %d генераций -> %s", count, value
        )

        self._refresh_timer.start()

    def delete_generations(self, generation_ids: list[int], delete_files: bool = False) -> int:
        """Удаляет несколько генераций из библиотеки (и, если
        delete_files=True, физически с диска). Возвращает количество
        успешно удалённых записей."""

        deleted_ids = set()

        for gid in generation_ids:

            try:
                if self._repository.delete_generation(gid, delete_files=delete_files):
                    deleted_ids.add(gid)
            except OSError as e:
                logger.error("Ошибка при удалении генерации id=%s: %s", gid, e)
                self.error_occurred.emit(f"Не удалось удалить генерацию id={gid}: {e}")

        if deleted_ids:

            self._invalidate_available_cache()

            if (
                self.current_generation is not None
                and self.current_generation.id in deleted_ids
            ):
                self.current_generation = None

            # apply_filters() перезапрашивает страницу из БД заново —
            # удалённые id туда уже не попадут, отдельно вычищать их
            # из self.generations/filtered_generations вручную не нужно
            self.apply_filters()

        logger.info(
            "Удалено %d из %d выбранных генераций (файлы %s)",
            len(deleted_ids), len(generation_ids),
            "удалены" if delete_files else "оставлены"
        )

        return len(deleted_ids)

    def export_generations(self, generation_ids: list[int], target_dir: str) -> int:
        """Копирует JSON-файлы выбранных генераций в target_dir.
        Возвращает количество успешно скопированных файлов."""

        target = Path(target_dir)
        count = 0

        for gid in generation_ids:

            generation = self._find_by_id(gid)

            if generation is None or not generation.path.exists():
                continue

            try:
                shutil.copy2(generation.path, target / generation.path.name)
                count += 1
            except OSError as e:
                logger.error("Не удалось экспортировать %s: %s", generation.path, e)
                self.error_occurred.emit(f"Не удалось экспортировать {generation.path.name}: {e}")

        logger.info("Экспортировано %d из %d генераций в %s", count, len(generation_ids), target_dir)

        return count

    def export_generations_zip(self, generation_ids: list[int], zip_path: str) -> int:
        """Экспортирует выбранные генерации (JSON + изображения +
        превью) в один ZIP-архив (задача 3.4). См.
        GenerationRepository.export_generations_zip."""

        count = self._repository.export_generations_zip(generation_ids, zip_path)

        logger.info(
            "Экспортировано в ZIP %d из %d генераций: %s",
            count, len(generation_ids), zip_path
        )

        return count

    def import_user_data(self, other_db_path: str) -> tuple[int, int]:
        """Импортирует избранное/рейтинг из БД другой машины (задача
        3.4) и обновляет in-memory представление уже загруженных
        генераций, чтобы изменения сразу отразились в UI без повторного
        открытия папки."""

        updated, unmatched = self._repository.import_user_data(other_db_path)

        if updated and self.current_folder is not None:
            self.apply_filters()

        return updated, unmatched

    def add_dropped_files(self, paths: list[str]) -> int:
        """Добавляет в библиотеку JSON-файлы генераций, перетащенные в
        главное окно (задача 3.4) — каждый файл добавляется/обновляется
        в БД напрямую (без пересканирования всей папки), затем список в
        UI перечитывается, если сейчас открыта папка.

        Возвращает количество успешно добавленных файлов."""

        added = 0

        for path in paths:

            try:
                gen_id = self._repository.add_generation_file(path)
            except OSError as e:
                logger.error("add_dropped_files: ошибка при добавлении %s: %s", path, e)
                self.error_occurred.emit(f"Не удалось добавить {path}: {e}")
                continue

            if gen_id is not None:
                added += 1
            else:
                self.error_occurred.emit(f"Не удалось разобрать JSON: {path}")

        logger.info("Перетаскиванием добавлено %d из %d файлов", added, len(paths))

        if added and self.current_folder is not None:
            self._invalidate_available_cache()
            self.apply_filters()

        return added

    def get_statistics(self) -> Statistics:
        """Статистика по тому, что сейчас реально показано в галерее:
        текущая открытая папка (self.current_folder) с уже применёнными
        фильтрами (self.filtered_generations) — а не по всей библиотеке.

        Считается в Python над уже загруженным списком (см.
        compute_statistics) — filtered_generations и так уже в памяти
        и ограничены текущей папкой/выборкой, поэтому лишний SQL-запрос
        по всей БД здесь не нужен."""

        return compute_statistics(self.filtered_generations)

    def get_library_statistics(self) -> Statistics:
        """Статистика по ВСЕЙ библиотеке (всем папкам, когда-либо
        просканированным в БД), без учёта текущей папки/фильтров —
        считается SQL-агрегатами напрямую в БД (см.
        GenerationRepository.get_statistics), поэтому дёшево даже для
        очень большой библиотеки. Используется второй вкладкой
        StatisticsWindow, наряду с get_statistics()."""

        return self._repository.get_statistics()

    # ------------------------------------------------------------
    # редактирование метаданных

    def update_generation_metadata(self, generation_id: int, update_dict: dict) -> bool:
        """Сохраняет отредактированные метаданные генерации (JSON на
        диске + БД) и обновляет её in-memory представление.

        При неудаче эмитит error_occurred с человекочитаемым сообщением
        и возвращает False.
        """

        success = self._repository.update_generation(generation_id, update_dict)

        if not success:
            self.error_occurred.emit(
                "Не удалось сохранить изменения — подробности в логе."
            )
            return False

        refreshed = self._repository.get_generation(generation_id)

        if refreshed is None:
            self.error_occurred.emit(
                "Изменения сохранены, но не удалось перечитать генерацию."
            )
            return False

        self._invalidate_available_cache()

        if (
            self.current_generation is not None
            and self.current_generation.id == generation_id
        ):
            self.current_generation = refreshed

        # модель/сэмплер могли измениться — список доступных значений
        # для фильтров и сам отфильтрованный список могли устареть.
        # apply_filters() перезапрашивает страницу из БД заново (там уже
        # окажется refreshed), отдельно подменять объект в
        # self.generations/filtered_generations вручную не нужно —
        # вызываем ДО эмита metadata_updated, чтобы к моменту, когда UI
        # на него отреагирует, filtered_generations уже отражал
        # результат редактирования (иначе проверка "видна ли ещё
        # генерация" ниже смотрела бы на устаревший список)
        self.apply_filters()

        still_visible = any(
            g.id == generation_id for g in self.filtered_generations
        )

        self.metadata_updated.emit(refreshed)

        if not still_visible:
            # редактирование могло изменить, например, модель или
            # рейтинг так, что генерация больше не проходит активные
            # фильтры — раньше она просто молча пропадала из списка
            # без какого-либо объяснения пользователю
            logger.info(
                "Генерация id=%s отредактирована и скрыта текущими фильтрами",
                generation_id
            )
            self.metadata_updated_hidden_by_filters.emit(refreshed)

        return True

    def update_generations_metadata(
        self, generation_ids: list[int], update_dict: dict
    ) -> bool:
        """Массовое редактирование метаданных (задача: массовое
        редактирование метаданных) — применяет один и тот же
        update_dict сразу к нескольким генерациям (см.
        GenerationRepository.update_generations).

        В отличие от update_generation_metadata эмитит
        bulk_metadata_updated(ids) один раз со списком id, для которых
        сохранение прошло успешно, а не отдельный сигнал на каждую
        генерацию — вызывающему UI (диалог массового редактирования)
        обычно нужно просто закрыться/обновиться один раз по итогу
        всей операции, а не реагировать на каждую генерацию отдельно.

        Возвращает True, если сохранены ВСЕ переданные генерации, и
        False, если хотя бы одна не сохранилась (при этом остальные,
        которые сохранились, всё равно попадают в bulk_metadata_updated
        — частичный успех не откатывается, каждая генерация хранится
        в своём файле независимо от остальных).
        """

        failed_ids = self._repository.update_generations(generation_ids, update_dict)
        succeeded_ids = [gid for gid in generation_ids if gid not in failed_ids]

        if failed_ids:
            self.error_occurred.emit(
                f"Не удалось сохранить изменения для {len(failed_ids)} из "
                f"{len(generation_ids)} генераций — подробности в логе."
            )

        if succeeded_ids:

            self._invalidate_available_cache()

            if (
                self.current_generation is not None
                and self.current_generation.id in succeeded_ids
            ):
                self.current_generation = self._repository.get_generation(
                    self.current_generation.id
                )

            # как и в update_generation_metadata — перезапрашиваем
            # страницу из БД ДО эмита сигнала, чтобы к моменту, когда UI
            # на него отреагирует, filtered_generations уже отражал
            # результат массового редактирования
            self.apply_filters()

            self.bulk_metadata_updated.emit(succeeded_ids)

        return not failed_ids

    def get_metadata_history(self, generation_id: int) -> list[dict]:
        """История изменений метаданных одной генерации (задача:
        история изменений метаданных) — самые новые записи первыми.
        См. GenerationRepository.get_metadata_history."""

        return self._repository.get_metadata_history(generation_id)

    # ------------------------------------------------------------

    def close(self) -> None:
        """Освобождает ресурсы (соединение с БД). Вызывать при закрытии
        приложения.

        Останавливает отложенный debounce-пересчёт (см. _refresh_timer)
        — иначе он мог бы сработать уже после закрытия соединения с БД."""

        self._closed = True
        self._refresh_timer.stop()

        self._repository.close()
