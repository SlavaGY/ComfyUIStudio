"""Фильтры, сортировка и загрузка папки GalleryManager.

Синхронизация выполняется через GenerationRepository. Конвейер SQL-фильтрации
и постраничной загрузки находится в gallery_base."""

from __future__ import annotations

import logging

from comfyui_studio.promptvault.core.gallery_base import GalleryBase
from comfyui_studio.promptvault.core.generation_filter import FilterOptions
from comfyui_studio.promptvault.core.sort_options import SortMode

logger = logging.getLogger(__name__)


class FiltersMixin(GalleryBase):
    # ------------------------------------------------------------
    # загрузка папки

    def load_folder(self, folder: str) -> None:
        """Открывает папку: синхронизирует БД с диском и загружает первую
        отфильтрованную страницу (см. apply_filters).

        Первый вызов для новой папки может быть небыстрым (парсинг всех
        JSON-файлов), повторные — почти мгновенные, т.к. пересканируются
        только новые/изменившиеся файлы (GenerationRepository.sync_folder).

        Сам список из БД в память загружается лениво/постранично: сразу
        отображается только первая страница текущего фильтра/сортировки,
        остальные подгружаются по требованию (прокрутка списка).
        """

        folder = str(folder)

        logger.info("Открытие папки: %s", folder)

        self.current_generation = None
        self.current_folder = folder

        # задача: сохранение пути к папке просмотра между сессиями —
        # запоминаем КАЖДУЮ успешно открытую папку. Пишем ДО
        # sync_folder/apply_filters ниже — даже если сама загрузка упадёт
        # на середине, путь всё равно успеет сохраниться.
        self._settings.setValue("last_folder", folder)

        self._repository.sync_folder(folder)

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
    # (кэш — в gallery_base)

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
        """Устанавливает текст поиска (несколько слов ищутся через И) и
        сразу применяет фильтры. Не сохраняется между запусками."""

        self._filter_options.search = text
        self.apply_filters()

    def set_filter_options(self, options: FilterOptions) -> None:
        """Заменяет все параметры фильтрации разом (используется попапом
        фильтров) и сразу применяет. Не сохраняется между запусками."""

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
