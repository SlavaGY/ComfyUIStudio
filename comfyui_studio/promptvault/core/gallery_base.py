"""Состояние, сигналы, жизненный цикл, выбор и пагинация галереи.

Миксины GalleryManager используют общий кэш доступных фильтров и один
список загруженных генераций. Публичный класс доступен из gallery_manager."""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QObject, QSettings, QTimer, Signal

from comfyui_studio.promptvault.config import (
    GENERATIONS_PAGE_SIZE,
    REFRESH_DEBOUNCE_MS,
)
from comfyui_studio.promptvault.core.generation import Generation
from comfyui_studio.promptvault.core.generation_filter import FilterOptions
from comfyui_studio.promptvault.core.repository import GenerationRepository
from comfyui_studio.promptvault.core.sort_options import SortMode

logger = logging.getLogger(__name__)


class GalleryBase(QObject):
    """Общая основа GalleryManager: сигналы, состояние и конвейер
    фильтрации/пагинации.

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
            прошло успешно.
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

    # Общие атрибуты, которыми пользуются миксины (аналогично
    # RepositoryBase._conn): объявлены здесь, чтобы mypy видел их во всех
    # частях класса. Присваиваются в __init__.
    _repository: GenerationRepository
    _settings: QSettings
    filtered_generations: list[Generation]
    generations: list[Generation]
    _filtered_total: int
    _loading_more: bool
    current_generation: Generation | None
    current_folder: str | None
    _closed: bool
    _available_models_cache: set[str]
    _available_samplers_cache: set[str]
    _available_loras_cache: set[str]
    _available_custom_tags_cache: set[str]
    _available_cache_dirty: bool
    _filter_options: FilterOptions
    _sort_mode: SortMode
    _refresh_timer: QTimer

    def __init__(
        self,
        repository: GenerationRepository,
        parent: QObject | None = None,
    ):
        super().__init__(parent)

        self._repository = repository
        self._settings = QSettings("PromptVault", "PromptVault")

        # В памяти находятся только загруженные страницы SQL-выборки.
        # generations — совместимый псевдоним того же списка; заменять
        # список следует через _set_filtered, сохраняя эту связь.
        self.filtered_generations = []
        self.generations = self.filtered_generations

        # см. filtered_total() — сколько генераций всего проходит
        # текущие фильтры (может быть больше len(filtered_generations),
        # пока не все страницы подгружены)
        self._filtered_total = 0
        self._loading_more = False

        self.current_generation = None
        self.current_folder = None
        self._closed = False

        # Значения фильтров получаем через SQL DISTINCT; кэш
        # инвалидируется при изменении содержимого папки.
        self._available_models_cache = set()
        self._available_samplers_cache = set()
        self._available_loras_cache = set()
        self._available_custom_tags_cache = set()
        self._available_cache_dirty = True

        self._filter_options = self._load_filter_state()
        self._sort_mode = self._load_sort_state()

        # см. REFRESH_DEBOUNCE_MS — быстрые повторные клики по
        # избранному/рейтингу не должны пересобирать список на каждый
        # клик, поэтому пересортировка/перерисовка откладывается и
        # схлопывает все клики за интервал в один пересчёт
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(REFRESH_DEBOUNCE_MS)
        self._refresh_timer.timeout.connect(self.apply_filters)

    # ------------------------------------------------------------
    # доступные значения для фильтров: общий кэш (getter'ы — в
    # gallery_filters, инвалидация — в мутациях)

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

    # ------------------------------------------------------------
    # размер страницы ленивой загрузки, настраивается через SettingsWindow

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
    # load_folder() (gallery_filters), здесь только чтение для
    # MainWindow, который решает, восстанавливать ли её при старте

    def last_folder(self) -> str | None:
        value = self._settings.value("last_folder", None)

        if not value:
            return None

        return str(value)

    # ------------------------------------------------------------
    # фильтры (не сохраняются между запусками) / сортировка (сохраняется,
    # QSettings)

    def _load_filter_state(self) -> FilterOptions:
        """Каждая сессия начинается с пустых FilterOptions; фильтры не сохраняются."""

        return FilterOptions()

    def _load_sort_state(self) -> SortMode:
        name = str(self._settings.value("sort_mode", SortMode.NEWEST.name))

        try:
            return SortMode[name]
        except KeyError:
            return SortMode.NEWEST

    # ------------------------------------------------------------
    # конвейер фильтрации/пагинации

    def apply_filters(self) -> None:
        """Загружает первую страницу текущей папки заново из БД с учётом
        фильтров (FilterOptions) и сортировки (SortMode) — целиком в SQL
        (см. GenerationFilterSQL/GenerationSorterSQL). Остальные страницы
        грузятся по требованию (load_more_filtered). В конце пытается
        сохранить текущий выбор (по id, см. select_generation)."""

        # если пересчёт запущен не через сам _refresh_timer, то ранее
        # запланированный debounce больше не нужен — эта пересборка его
        # уже покрывает
        self._refresh_timer.stop()

        # запоминаем текущую выделенную генерацию по её id (а не по
        # identity объекта — после перезагрузки из БД это уже новые
        # объекты)
        previous_id = (
            self.current_generation.id
            if self.current_generation is not None
            else None
        )

        if self.current_folder is None:
            self._set_filtered([])
            self._filtered_total = 0
            self.select_generation(None)
            self.generations_changed.emit()
            self.filter_changed.emit()
            return

        page_size = self._page_size()

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
        # обработчики UI увидят ещё не обновившийся current_generation
        self.select_generation(new_selection)

        self.generations_changed.emit()
        self.filter_changed.emit()

    def _set_filtered(self, generations: list[Generation]) -> None:
        """generations и filtered_generations всегда указывают на один и
        тот же объект списка (см. __init__) — единственное место, где этот
        список заменяется целиком."""

        self.filtered_generations = generations
        self.generations = generations

    def filtered_total(self) -> int:
        """Сколько генераций всего проходит текущие фильтры — может быть
        больше len(filtered_generations), пока не все страницы
        подгружены (см. load_more_filtered)."""

        return self._filtered_total

    def load_more_filtered(self) -> bool:
        """Подгружает ещё одну "страницу" уже отфильтрованного результата
        (задача: настоящая виртуальная пагинация) — вызывается UI при
        прокрутке списка к уже показанному концу. Возвращает True, если
        что-то подгрузилось."""

        if self._loading_more or self.current_folder is None:
            return False

        loaded = len(self.filtered_generations)

        if loaded >= self._filtered_total:
            return False

        self._loading_more = True

        try:
            page_size = self._page_size()

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

    def close(self) -> None:
        """Освобождает ресурсы (соединение с БД). Вызывать при закрытии
        приложения. Останавливает отложенный debounce-пересчёт — иначе он
        мог бы сработать уже после закрытия соединения с БД."""

        self._closed = True
        self._refresh_timer.stop()

        self._repository.close()
