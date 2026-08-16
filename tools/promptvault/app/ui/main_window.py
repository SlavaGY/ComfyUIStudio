from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QIcon, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.config import APP_VERSION, ICON_PATH
from app.core.folder_sync import FolderSync
from app.core.gallery_manager import GalleryManager
from app.core.generation import Generation
from app.core.hotkeys import HOTKEY_ACTIONS, HotkeyManager
from app.core.repository import GenerationRepository
from app.i18n import LocalizationManager
from app.themes.theme_manager import ThemeManager
from app.ui.bulk_metadata_editor import BulkMetadataEditor
from app.ui.filter_popup import FilterPopup
from app.ui.generation_list import GenerationList
from app.ui.image_viewer import ImageViewer
from app.ui.info_panel import InfoPanel
from app.ui.metadata_editor import MetadataEditor
from app.ui.settings_window import SettingsWindow
from app.ui.sort_popup import SortPopup
from app.ui.statistics_window import StatisticsWindow
from app.ui.thumbnail_panel import ThumbnailPanel
from app.ui.toolbar import Toolbar
from app.utils import open_file_externally, reveal_in_file_manager

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Главное окно приложения.

    Отвечает только за создание UI-компонентов, подключение сигналов
    между ними и обработку чисто UI-событий (горячие клавиши и т.п.).
    Вся бизнес-логика (загрузка папки, фильтрация, сортировка,
    избранное/рейтинг, массовые операции, редактирование метаданных) —
    в GalleryManager (self.gallery).
    """

    def __init__(self):
        super().__init__()

        self.setWindowTitle(f"PromptVault")
        self.resize(1600, 900)

        # задача 3.4: drag & drop JSON-файлов генераций прямо в главное
        # окно (см. dragEnterEvent/dropEvent ниже)
        self.setAcceptDrops(True)

        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))
        else:
            logger.warning("Иконка приложения не найдена: %s", ICON_PATH)

        self.theme_manager = ThemeManager()

        # применяется ДО конструирования Toolbar/остальных виджетов —
        # self.tr() внутри их __init__ должен сразу увидеть сохранённый
        # язык, а не только после первого ручного retranslate_ui()
        # (см. app/ui/settings_window.py — окно настроек, куда теперь
        # перенесено переключение языка/темы/семантического поиска)
        self.localization_manager = LocalizationManager()
        self.localization_manager.restore_saved_language()
        self.localization_manager.language_changed_externally.connect(
            self._on_language_changed_externally
        )

        self.repository = GenerationRepository()
        self.gallery = GalleryManager(self.repository, self)
        self.folder_sync = FolderSync(self.repository, self)

        self.toolbar = Toolbar()
        self.filter_popup = FilterPopup(self)
        self.sort_popup = SortPopup()

        self.generation_list = GenerationList()
        self.info_panel = InfoPanel()
        self.image_viewer = ImageViewer()
        self.thumbnail_panel = ThumbnailPanel()

        # окно статистики создаётся лениво, при первом открытии (см.
        # show_statistics) — держим ссылку, чтобы повторный клик по
        # кнопке поднимал уже открытое окно, а не плодил новые
        self.statistics_window: StatisticsWindow | None = None

        # то же самое для окна настроек (задача: перенос темы/языка/
        # семантического поиска из тулбара в отдельное окно)
        self.settings_window: SettingsWindow | None = None

        # см. restart_application/closeEvent — выставляется перед
        # self.close(), чтобы closeEvent знал, что после обычной
        # процедуры закрытия нужно ещё и перезапустить процесс
        self._pending_restart = False

        # задача: настраиваемые горячие клавиши — см. app/core/hotkeys.py
        self.hotkey_manager = HotkeyManager()
        self._shortcuts: dict[str, QShortcut] = {}

        self.init_ui()
        self.connect_events()
        self._register_hotkeys()

        # применяем тему, сохранённую с прошлого запуска (или Dark по умолчанию)
        self.theme_manager.apply_theme(self.theme_manager.current_theme())

        # задача: сохранение пути к папке просмотра между сессиями —
        # последний шаг __init__, после того как все виджеты и
        # обработчики уже готовы (см. _restore_last_folder)
        self._restore_last_folder()

    # --------------------------------------------------

    def init_ui(self) -> None:

        root = QWidget()
        self.setCentralWidget(root)

        main_layout = QVBoxLayout(root)
        main_layout.addWidget(self.toolbar)

        splitter = QSplitter(Qt.Horizontal)

        # ================= LEFT =================

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.addWidget(self.generation_list)

        splitter.addWidget(left_panel)

        # ================= CENTER =================

        center_panel = QWidget()
        center_layout = QVBoxLayout(center_panel)
        center_layout.addWidget(self.image_viewer, 3)
        center_layout.addWidget(self.info_panel, 2)

        splitter.addWidget(center_panel)

        # ================= RIGHT =================

        splitter.addWidget(self.thumbnail_panel)

        # размеры колонок
        splitter.setSizes([350, 950, 300])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)
        splitter.setStretchFactor(2, 1)

        main_layout.addWidget(splitter, 1)

    # --------------------------------------------------

    def connect_events(self) -> None:

        # -------- toolbar --------

        self.toolbar.openFolder.connect(self.open_folder)
        self.toolbar.searchRequested.connect(
            lambda: self.gallery.set_search(self.toolbar.search_text())
        )
        self.toolbar.filtersRequested.connect(self.show_filters)
        self.toolbar.sortRequested.connect(self.show_sort)
        self.toolbar.statisticsRequested.connect(self.show_statistics)
        self.toolbar.importRatingsRequested.connect(self.import_ratings_from_db)
        self.toolbar.settingsRequested.connect(self.show_settings)

        # -------- список генераций: одиночные действия --------

        self.generation_list.generationSelected.connect(self.gallery.select_by_index)
        self.generation_list.favoriteToggled.connect(self.gallery.toggle_favorite)
        self.generation_list.ratingChanged.connect(self.gallery.set_rating)
        self.generation_list.editRequested.connect(self._on_edit_requested)
        self.generation_list.bulkEditRequested.connect(self._on_bulk_edit_requested)
        self.thumbnail_panel.imageSelected.connect(self.on_image_selected)

        # -------- виртуальная пагинация (задача: настоящая виртуальная
        # пагинация) — список просит ещё одну страницу, когда
        # прокрутка доходит до уже показанного конца, GalleryManager
        # грузит её из БД и отдаёт назад ту же самую (новую) порцию --------

        self.generation_list.moreNeeded.connect(self.gallery.load_more_filtered)
        self.gallery.more_generations_loaded.connect(self.generation_list.append_generations)

        # -------- список генераций: массовые операции --------

        self.generation_list.multipleFavoriteChanged.connect(self.gallery.set_multiple_favorite)
        self.generation_list.multipleRatingChanged.connect(self.gallery.set_multiple_rating)
        self.generation_list.addTagsRequested.connect(self._on_add_tags_requested)
        self.generation_list.deleteFromLibraryRequested.connect(self._on_delete_from_library)
        self.generation_list.deleteFilesRequested.connect(self._on_delete_files)
        self.generation_list.exportRequested.connect(self._on_export_requested)
        self.generation_list.exportZipRequested.connect(self._on_export_zip_requested)
        self.generation_list.openJsonRequested.connect(self._on_open_json_requested)
        self.generation_list.openInFolderRequested.connect(self._on_open_in_folder_requested)

        # -------- попапы фильтров/сортировки --------

        self.filter_popup.applied.connect(self._push_filters_to_gallery)
        self.filter_popup.resetRequested.connect(self._push_filters_to_gallery)
        self.sort_popup.changed.connect(self._push_sort_to_gallery)

        # -------- автосинхронизация папки --------

        self.folder_sync.changed.connect(self.gallery.resync)

        # -------- GalleryManager -> UI --------

        self.gallery.generations_changed.connect(self._on_generations_changed)
        self.gallery.selection_changed.connect(self._on_selection_changed)
        self.gallery.error_occurred.connect(self._on_gallery_error)
        self.gallery.metadata_updated_hidden_by_filters.connect(
            self._on_metadata_updated_hidden_by_filters
        )

    # --------------------------------------------------
    # мосты между UI-виджетами попапов и состоянием GalleryManager
    #
    # filter_popup/sort_popup остаются чисто UI-виджетами (не знают о
    # GalleryManager); MainWindow лишь читает их текущие значения и
    # передаёт в GalleryManager при возникновении соответствующего
    # события (Apply/Reset/смена режима сортировки)

    def _push_filters_to_gallery(self) -> None:

        options = self.gallery.filter_options()
        options.semantic_query = self.filter_popup.semantic_query()
        options.model = self.filter_popup.model()
        options.sampler = self.filter_popup.sampler()
        options.loras = self.filter_popup.loras()
        options.excluded_loras = self.filter_popup.excluded_loras()
        options.custom_tags = self.filter_popup.custom_tags()
        options.excluded_custom_tags = self.filter_popup.excluded_custom_tags()
        options.favorites_only = self.filter_popup.favorites_only()
        options.min_rating = self.filter_popup.min_rating()
        options.min_cfg = self.filter_popup.min_cfg()
        options.max_cfg = self.filter_popup.max_cfg()
        options.min_steps = self.filter_popup.min_steps()
        options.max_steps = self.filter_popup.max_steps()

        self.gallery.set_filter_options(options)

    def _push_sort_to_gallery(self) -> None:

        self.gallery.set_sort_mode(self.sort_popup.current_mode())

    # --------------------------------------------------
    # GalleryManager -> UI

    def _on_generations_changed(self) -> None:

        generation = self.gallery.get_current_generation()

        self.generation_list.set_page(
            self.gallery.filtered_generations,
            total_count=self.gallery.filtered_total(),
            current_id=generation.id if generation is not None else None,
        )

        # окно статистики показывает текущую папку/фильтры (см.
        # StatisticsWindow) — если оно сейчас открыто, держим его в
        # актуальном состоянии при смене папки, фильтров или сортировки
        if self.statistics_window is not None and self.statistics_window.isVisible():
            self.statistics_window.refresh()

    def _on_selection_changed(self, generation: Generation | None) -> None:

        if generation is None:
            self.info_panel.clear()
            self.thumbnail_panel.clear()
            return

        self.info_panel.set_generation(generation)
        self.thumbnail_panel.set_generation(generation)

        if generation.images:
            self.image_viewer.set_image(generation.image_path(0))

    def _on_gallery_error(self, message: str) -> None:

        logger.error("Ошибка GalleryManager: %s", message)

        QMessageBox.warning(self, "PromptVault", message)

    # --------------------------------------------------
    # редактирование метаданных

    def _on_edit_requested(self, generation_id: int) -> None:

        generation = next(
            (g for g in self.gallery.generations if g.id == generation_id),
            None
        )

        if generation is None:
            return

        editor = MetadataEditor(
            generation,
            known_models=self.gallery.available_models(),
            known_samplers=self.gallery.available_samplers(),
            history=self.gallery.get_metadata_history(generation_id),
            parent=self,
        )

        editor.saved.connect(self.gallery.update_generation_metadata)
        editor.tagsChanged.connect(self.gallery.set_custom_tags)

        # диалог сам себя не закрывает (см. MetadataEditor) — закрываем
        # его отсюда явным accept(), и только когда GalleryManager
        # подтвердил успешное сохранение именно ЭТОЙ генерации. При
        # ошибке metadata_updated не эмитится, error_occurred уже
        # показал причину (см. _on_gallery_error), а диалог остаётся
        # открытым с введёнными пользователем данными, чтобы он не
        # терялся и можно было попробовать сохранить снова.
        def _close_on_success(updated: Generation) -> None:
            if updated.id == generation_id:
                editor.accept()

        self.gallery.metadata_updated.connect(_close_on_success)

        try:
            editor.exec()
        finally:
            self.gallery.metadata_updated.disconnect(_close_on_success)

    def _on_bulk_edit_requested(self, generation_ids: list[int]) -> None:
        """Массовое редактирование метаданных (задача: массовое
        редактирование метаданных) — тот же паттерн закрытия по
        подтверждённому успеху, что и в _on_edit_requested, но через
        bulk_metadata_updated (несёт список id, а не одну Generation)."""

        editor = BulkMetadataEditor(
            generation_ids,
            known_models=self.gallery.available_models(),
            known_samplers=self.gallery.available_samplers(),
            parent=self,
        )

        editor.saved.connect(self.gallery.update_generations_metadata)

        def _close_on_success(updated_ids: list[int]) -> None:
            if set(updated_ids) == set(generation_ids):
                editor.accept()

        self.gallery.bulk_metadata_updated.connect(_close_on_success)

        try:
            editor.exec()
        finally:
            self.gallery.bulk_metadata_updated.disconnect(_close_on_success)

    def _on_add_tags_requested(self, generation_ids: list[int]) -> None:
        """Добавляет тег(и) сразу всем переданным (обычно — всем
        выделенным) генерациям (задача: пользовательские теги,
        поддержка массового выделения). Несколько тегов можно ввести
        через запятую за один раз."""

        text, ok = QInputDialog.getText(
            self,
            self.tr("Add tag(s)"),
            self.tr("Tag(s) (comma-separated for multiple):"),
        )

        if not ok:
            return

        tags = [t.strip() for t in text.split(",") if t.strip()]

        if not tags:
            return

        self.gallery.add_tags_to_generations(generation_ids, tags)

    def _on_metadata_updated_hidden_by_filters(self, generation: Generation) -> None:

        QMessageBox.information(
            self,
            "PromptVault",
            self.tr(
                "Changes were saved, but this generation no longer matches "
                "the current filters and is now hidden from the list.\n"
                "Clear or change the filters to see it again."
            )
        )

    # --------------------------------------------------
    # массовое удаление / экспорт

    def _on_delete_from_library(self, generation_ids: list[int]) -> None:

        answer = QMessageBox.question(
            self,
            self.tr("Remove from library"),
            self.tr(
                "Remove {} generations from the library?\n"
                "Files on disk will not be touched."
            ).format(len(generation_ids)),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if answer != QMessageBox.Yes:
            return

        deleted = self.gallery.delete_generations(generation_ids, delete_files=False)

        if deleted < len(generation_ids):
            # частичный сбой уже был отдельно объяснён по каждому
            # файлу через error_occurred (см. GalleryManager.
            # delete_generations) — здесь просто честная сводка,
            # вместо молчаливого использования только части выбора
            QMessageBox.warning(
                self,
                self.tr("Remove from library"),
                self.tr(
                    "Removed {} of {} generations — "
                    "see previous messages for details on the rest."
                ).format(deleted, len(generation_ids))
            )

    def _on_delete_files(self, generation_ids: list[int]) -> None:

        answer = QMessageBox.question(
            self,
            self.tr("Delete files + record"),
            self.tr(
                "Permanently delete {} generations "
                "together with their files (JSON and images) from disk?\n"
                "This action cannot be undone."
            ).format(len(generation_ids)),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if answer != QMessageBox.Yes:
            return

        deleted = self.gallery.delete_generations(generation_ids, delete_files=True)

        if deleted < len(generation_ids):
            QMessageBox.warning(
                self,
                self.tr("Delete files + record"),
                self.tr(
                    "Removed {} of {} generations — "
                    "see previous messages for details on the rest."
                ).format(deleted, len(generation_ids))
            )

    def _on_export_requested(self, generation_ids: list[int]) -> None:

        target_dir = QFileDialog.getExistingDirectory(self, self.tr("Export JSON to..."))

        if not target_dir:
            return

        count = self.gallery.export_generations(generation_ids, target_dir)

        QMessageBox.information(
            self,
            self.tr("Export JSON"),
            self.tr("Exported {} of {} files to:\n{}").format(
                count, len(generation_ids), target_dir
            )
        )

    def _on_export_zip_requested(self, generation_ids: list[int]) -> None:
        """Экспорт выбранных генераций в ZIP (JSON + изображения +
        превью), задача 3.4."""

        zip_path, _filter = QFileDialog.getSaveFileName(
            self, self.tr("Export as ZIP..."), "promptvault_export.zip",
            self.tr("ZIP archives (*.zip)")
        )

        if not zip_path:
            return

        if not zip_path.lower().endswith(".zip"):
            zip_path += ".zip"

        count = self.gallery.export_generations_zip(generation_ids, zip_path)

        QMessageBox.information(
            self,
            self.tr("Export as ZIP"),
            self.tr("Exported {} of {} generations to:\n{}").format(
                count, len(generation_ids), zip_path
            )
        )

    def import_ratings_from_db(self) -> None:
        """Импорт избранного/рейтинга из БД другой машины/копии
        приложения (задача 3.4)."""

        db_path, _filter = QFileDialog.getOpenFileName(
            self, self.tr("Import favorites/ratings from..."), "",
            self.tr("SQLite database (*.db)")
        )

        if not db_path:
            return

        updated, unmatched = self.gallery.import_user_data(db_path)

        QMessageBox.information(
            self,
            self.tr("Import favorites/ratings"),
            self.tr(
                "Records updated: {}\n"
                "No match found in the current library: {}"
            ).format(updated, unmatched)
        )

    # --------------------------------------------------

    def _on_language_changed(self, _language_code: str) -> None:
        """SettingsWindow уже перевела тексты тулбара (см.
        Toolbar.retranslate_ui) — здесь достаточно перестроить окно
        статистики, если оно сейчас открыто, чтобы оно тоже
        подхватило новый язык (см. StatisticsWindow.refresh)."""

        if self.statistics_window is not None and self.statistics_window.isVisible():
            self.statistics_window.refresh()

    def _on_language_changed_externally(self, language_code: str) -> None:
        """Язык поменялся в ComfyUI Launcher или PromptConfigEditor, пока
        это приложение уже открыто — applying уже сделан в
        LocalizationManager, здесь только перевыставляем уже показанные
        тексты (тулбар и, если оно открыто, окно настроек/статистики)."""

        self.toolbar.retranslate_ui()
        if self.settings_window is not None:
            self.settings_window.retranslate_ui()
        self._on_language_changed(language_code)

    # --------------------------------------------------

    def show_statistics(self) -> None:
        """Открывает окно статистики (задача 3.2) — считается по тому,
        что сейчас реально показано в галерее: текущая открытая папка
        с учётом активных фильтров (см. StatisticsWindow), а не по
        всей библиотеке. Повторный клик поднимает уже открытое окно и
        пересчитывает статистику заново."""

        if self.statistics_window is None:
            self.statistics_window = StatisticsWindow(self.gallery, self)

        self.statistics_window.refresh()
        self.statistics_window.show()
        self.statistics_window.raise_()
        self.statistics_window.activateWindow()

    # --------------------------------------------------

    def show_settings(self) -> None:
        """Открывает окно настроек — тема, язык, семантический поиск,
        производительность, автоочистка (перенесено из тулбара, чтобы
        не перегружать его кнопками). Повторный клик поднимает уже
        открытое окно, а не плодит новые."""

        if self.settings_window is None:

            self.settings_window = SettingsWindow(
                gallery=self.gallery,
                theme_manager=self.theme_manager,
                localization_manager=self.localization_manager,
                toolbar=self.toolbar,
                parent=self,
            )
            self.settings_window.restartRequested.connect(self.restart_application)
            self.settings_window.quitRequested.connect(self.quit_application)
            self.settings_window.hotkeyChanged.connect(self._on_hotkey_changed)

        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    # --------------------------------------------------

    def restart_application(self) -> None:
        """Перезапускает приложение целиком (задача: кнопка "Restart"
        в настройках) — закрывает текущий процесс так же аккуратно,
        как обычное закрытие окна (см. closeEvent — останавливает
        FolderSync, закрывает соединение с БД), а затем заменяет
        процесс новым запуском `python -m app.main` (см. closeEvent —
        именно как `-m app.main`, а не переиспользованием sys.argv "как
        есть", иначе запуск через `python -m app.main` ломается).

        Подтверждение уже было запрошено в SettingsWindow — сюда
        попадаем, только если пользователь согласился."""

        logger.info("Перезапуск приложения по запросу пользователя")

        self._pending_restart = True
        self.close()

    def quit_application(self) -> None:
        """Полностью закрывает приложение (задача: кнопка "Quit" в
        настройках) — то же самое, что закрыть главное окно обычным
        способом. Подтверждение уже было запрошено в SettingsWindow."""

        logger.info("Завершение работы приложения по запросу пользователя")

        self.close()

    # --------------------------------------------------

    def show_sort(self) -> None:

        if self.sort_popup.isVisible():
            self.sort_popup.hide()
            return

        self.sort_popup.adjustSize()

        popup_size = self.sort_popup.sizeHint()

        button = self.toolbar.sort_btn
        button_bottom_left = button.mapToGlobal(button.rect().bottomLeft())

        window = self.window()
        window_pos = window.mapToGlobal(QPoint(0, 0))
        window_right = window_pos.x() + window.width()

        # по умолчанию левый край попапа = левый край кнопки — раньше
        # попап был жёстко прижат к правой стенке окна, а сама кнопка
        # Sort с тех пор уехала левее (добавились Stats/Import
        # ratings/язык), так что попап открывался далеко от кнопки.
        # min(...) — та же защита от вылезания за правый край окна,
        # из-за отсутствия которой раньше был баг.
        x = min(button_bottom_left.x(), window_right - popup_size.width() - 10)
        x = max(x, window_pos.x() + 10)

        y = button_bottom_left.y() + 5

        self.sort_popup.move(x, y)
        self.sort_popup.show()

    # --------------------------------------------------

    def show_filters(self) -> None:

        popup = self.filter_popup

        if popup.isVisible():
            popup.hide()
            return

        # размер popup уже известен после show/adjustSize
        popup.adjustSize()

        popup_size = popup.sizeHint()

        button = self.toolbar.filters_btn
        button_bottom_left = button.mapToGlobal(button.rect().bottomLeft())

        window = self.window()
        window_pos = window.mapToGlobal(QPoint(0, 0))
        window_right = window_pos.x() + window.width()

        # тот же баг и то же исправление, что и в show_sort: раньше
        # попап был жёстко прижат к правому краю ОКНА, а не к кнопке
        # Filters — из-за этого при смене языка (другие кнопки
        # тулбара меняют ширину при переводе, Filters сдвигается) или
        # просто при изменении набора кнопок попап всё равно рисовался
        # у правой стенки, оторванным от самой кнопки. min(...) —
        # защита от вылезания за правый край окна.
        x = min(button_bottom_left.x(), window_right - popup_size.width() - 10)
        x = max(x, window_pos.x() + 10)

        y = button_bottom_left.y() + 5

        popup.move(x, y)
        popup.show()
        popup.raise_()
        popup.activateWindow()

    # --------------------------------------------------

    def open_folder(self) -> None:

        folder = QFileDialog.getExistingDirectory(self, self.tr("Select folder"))

        if not folder:
            return

        self._open_folder_path(folder)

    def _restore_last_folder(self) -> None:
        """Задача: сохранение пути к папке просмотра между сессиями —
        при старте пробует открыть папку, использовавшуюся в прошлый
        раз (см. GalleryManager.last_folder/load_folder — запись туда
        происходит при КАЖДОМ успешном открытии папки, не только
        вручную через диалог).

        Если папки больше нет на диске (переименовали, отключили
        внешний накопитель и т.п.) — молча ничего не делаем, без
        всплывающего диалога об ошибке при каждом запуске: это
        ожидаемая, не редкая ситуация, а не что-то, требующее внимания
        пользователя именно сейчас (открыть нужную папку вручную он
        всё равно может в любой момент). См. show_errors=False ниже —
        та же логика "не беспокоить" распространяется и на прочие
        ошибки самой загрузки (например, потерянные права доступа)."""

        folder = self.gallery.last_folder()

        if folder is None:
            return

        if not Path(folder).is_dir():
            logger.info(
                "Папка с прошлого запуска больше не существует, "
                "пропускаю восстановление: %s", folder
            )
            return

        self._open_folder_path(folder, show_errors=False)

    def _open_folder_path(self, folder: str, *, show_errors: bool = True) -> bool:
        """Общая часть открытия папки — и для open_folder() (после
        выбора через диалог), и для _restore_last_folder() (при
        старте). show_errors=False используется только вторым
        вызывающим — см. его docstring."""

        self.info_panel.clear()
        self.thumbnail_panel.clear()

        try:
            self.gallery.load_folder(folder)
        except OSError as e:
            logger.exception("Не удалось открыть папку %s", folder)
            if show_errors:
                QMessageBox.critical(
                    self, "PromptVault",
                    self.tr("Could not open folder:\n{}\n\n{}").format(folder, e)
                )
            return False

        models = self.gallery.available_models()
        samplers = self.gallery.available_samplers()
        loras = self.gallery.available_loras()
        custom_tags = self.gallery.available_custom_tags()

        self.filter_popup.set_models(models)
        self.filter_popup.set_samplers(samplers)
        self.filter_popup.set_loras(loras)
        self.filter_popup.set_custom_tags(custom_tags)

        # восстанавливаем состояние фильтров, сохранённое с прошлого
        # запуска (значения, отсутствующие в новой папке, попапу
        # безопасно проигнорировать — set_models/set_samplers уже
        # обработали это выше)
        self.filter_popup.apply_options(self.gallery.filter_options())

        # начинаем следить за изменениями в папке
        self.folder_sync.watch(folder)

        return True

    # --------------------------------------------------

    def _on_open_json_requested(self, generation_ids: list[int]) -> None:
        """Открывает JSON-файл(ы) выбранных генераций в ассоциированном
        приложении ОС — по одному вызову на файл, так что при
        нескольких выбранных генерациях открывается несколько окон
        (сколько файлов, столько и открытий; порядок открытия окон
        определяет уже сама ОС/приложение по умолчанию)."""

        for generation_id in generation_ids:

            generation = self.repository.get_generation(generation_id)

            if generation is not None:
                open_file_externally(generation.path)

    def _on_open_in_folder_requested(self, generation_ids: list[int]) -> None:
        """Открывает файловый менеджер ОС с выделенными файлами
        выбранных генераций — JSON и ВСЕ относящиеся к ней изображения
        (не только сам JSON). Если генерации лежат в разных папках,
        открывается отдельное окно на каждую папку, и в нём выделяются
        только файлы из этой папки (см. app.utils.reveal_in_file_manager)."""

        paths: list[Path] = []
        seen: set[Path] = set()

        for generation_id in generation_ids:

            generation = self.repository.get_generation(generation_id)

            if generation is None:
                continue

            candidates = [generation.path]
            candidates.extend(
                generation.directory / image.file for image in generation.images
            )

            for path in candidates:
                if path not in seen:
                    seen.add(path)
                    paths.append(path)

        if paths:
            reveal_in_file_manager(paths)

    # --------------------------------------------------

    def _register_hotkeys(self) -> None:
        """Создаёт QShortcut на каждое действие из HOTKEY_ACTIONS, с
        комбинацией, назначенной через HotkeyManager (пользовательской
        или по умолчанию — задача: настраиваемые горячие клавиши, см.
        app/core/hotkeys.py).

        Контекст по умолчанию (Qt.WindowShortcut) — тот же охват, что
        неявно давал прежний MainWindow.keyPressEvent (F11/R/стрелки,
        см. git-историю): срабатывает в пределах этого окна и его
        дочерних виджетов, но не мешает обычному текстовому вводу — Qt
        сначала посылает событие ShortcutOverride, и QLineEdit/
        QTextEdit сами "забирают" его себе для букв/цифр без
        модификаторов, так что, например, хоткей "F" (toggle_favorite)
        не срабатывает, пока печатаешь в поле поиска.

        action_id, для которого нет обработчика ниже (сейчас такого
        нет — все ключи HOTKEY_ACTIONS перечислены), просто
        пропускается, а не падает — так добавление нового action_id в
        hotkeys.py без соответствующей записи здесь не роняет
        приложение, только тихо не создаёт хоткей."""

        handlers: dict[str, Callable[[], None]] = {
            "open_folder": self.open_folder,
            "focus_search": self._focus_search,
            "toggle_filters": self.show_filters,
            "toggle_sort": self.show_sort,
            "show_statistics": self.show_statistics,
            "show_settings": self.show_settings,
            "toggle_favorite": self._hotkey_toggle_favorite,
            "edit_metadata": self._hotkey_edit_metadata,
            "add_tags": self._hotkey_add_tags,
            "export_json": self._hotkey_export_json,
            "export_zip": self._hotkey_export_zip,
            "open_json_externally": self._hotkey_open_json,
            "open_in_file_manager": self._hotkey_open_in_folder,
            "delete_from_library": self._hotkey_delete_from_library,
            "delete_files": self._hotkey_delete_files,
            "toggle_fullscreen": self._toggle_fullscreen,
            "reset_image_view": self.image_viewer.reset_view,
            "next_image": self.thumbnail_panel.next,
            "previous_image": self.thumbnail_panel.previous,
        }

        for action_id in HOTKEY_ACTIONS:

            handler = handlers.get(action_id)

            if handler is None:
                continue

            shortcut = QShortcut(self.hotkey_manager.sequence(action_id), self)
            shortcut.activated.connect(handler)

            self._shortcuts[action_id] = shortcut

    def _on_hotkey_changed(self, action_id: str) -> None:
        """SettingsWindow уже сохранила новую комбинацию через
        HotkeyManager (см. SettingsWindow._on_hotkey_edited) — здесь
        только обновляется уже существующий QShortcut на месте, без
        пересоздания (пересоздание потребовало бы заново подключать
        activated и аккуратно снимать старый QShortcut)."""

        shortcut = self._shortcuts.get(action_id)

        if shortcut is not None:
            shortcut.setKey(self.hotkey_manager.sequence(action_id))

    def _focus_search(self) -> None:

        self.toolbar.search.setFocus()
        self.toolbar.search.selectAll()

    def _toggle_fullscreen(self) -> None:

        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # -------- хоткеи, действующие на текущее выделение в списке --------
    #
    # все они read-only читают GenerationList.selected_ids() и просто
    # ничего не делают при пустом выделении (то же самое, что делает
    # contextMenuEvent — там пункты меню тоже недоступны без
    # выделения), вместо того чтобы показывать сообщение об ошибке.

    def _hotkey_toggle_favorite(self) -> None:
        """У одиночного выделения — обычный toggle (как клик по
        звёздочке в карточке). При множественном выделении
        однозначного "toggle" не существует (что делать, если часть
        уже избранная, а часть — нет?) — вместо этого хоткей добавляет
        ВСЕ выделенные в избранное, как пункт контекстного меню "Add to
        favorites"; чтобы убрать несколько сразу из избранного,
        по-прежнему нужно контекстное меню ("Remove from favorites")."""

        ids = self.generation_list.selected_ids()

        if not ids:
            return

        if len(ids) == 1:
            self.gallery.toggle_favorite(ids[0])
        else:
            self.gallery.set_multiple_favorite(ids, True)

    def _hotkey_edit_metadata(self) -> None:

        ids = self.generation_list.selected_ids()

        if not ids:
            return

        if len(ids) == 1:
            self._on_edit_requested(ids[0])
        else:
            self._on_bulk_edit_requested(ids)

    def _hotkey_add_tags(self) -> None:

        ids = self.generation_list.selected_ids()

        if ids:
            self._on_add_tags_requested(ids)

    def _hotkey_export_json(self) -> None:

        ids = self.generation_list.selected_ids()

        if ids:
            self._on_export_requested(ids)

    def _hotkey_export_zip(self) -> None:

        ids = self.generation_list.selected_ids()

        if ids:
            self._on_export_zip_requested(ids)

    def _hotkey_open_json(self) -> None:

        ids = self.generation_list.selected_ids()

        if ids:
            self._on_open_json_requested(ids)

    def _hotkey_open_in_folder(self) -> None:

        ids = self.generation_list.selected_ids()

        if ids:
            self._on_open_in_folder_requested(ids)

    def _hotkey_delete_from_library(self) -> None:

        ids = self.generation_list.selected_ids()

        if ids:
            self._on_delete_from_library(ids)

    def _hotkey_delete_files(self) -> None:

        ids = self.generation_list.selected_ids()

        if ids:
            self._on_delete_files(ids)

    # --------------------------------------------------

    def on_image_selected(self, index: int) -> None:

        generation = self.gallery.get_current_generation()

        if generation is None:
            return

        if index < 0 or index >= len(generation.images):
            return

        self.image_viewer.set_image(
            generation.image_path(index)
        )

    # --------------------------------------------------

    def keyPressEvent(self, event) -> None:
        """F11/R/стрелки и прочие горячие клавиши теперь регистрируются
        как QShortcut в _register_hotkeys (задача: настраиваемые
        горячие клавиши) — этот метод больше не перехватывает их
        руками, QMainWindow.keyPressEvent просто получает событие,
        которое ни один QShortcut/виджет не забрал себе."""

        super().keyPressEvent(event)

    # --------------------------------------------------
    # drag & drop JSON-файлов (задача 3.4)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:

        if self._extract_json_paths_from_mime_data(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:

        paths = self._extract_json_paths_from_mime_data(event.mimeData())

        if not paths:
            event.ignore()
            return

        event.acceptProposedAction()

        added = self.gallery.add_dropped_files(paths)

        QMessageBox.information(
            self,
            self.tr("Import dropped files"),
            self.tr("Added {} of {} files.").format(added, len(paths))
        )

    @staticmethod
    def _extract_json_paths_from_mime_data(mime_data) -> list[str]:
        """Из QMimeData перетаскивания достаёт пути только к локальным
        .json-файлам, игнорируя прочие типы перетаскивания (текст,
        изображения из браузера, ссылки и т.п.)."""

        if not mime_data.hasUrls():
            return []

        paths = []

        for url in mime_data.urls():

            if not url.isLocalFile():
                continue

            local_path = url.toLocalFile()

            if local_path.lower().endswith(".json"):
                paths.append(local_path)

        return paths

    # --------------------------------------------------

    def closeEvent(self, event) -> None:

        logger.info("Закрытие приложения")

        self.folder_sync.stop()
        self.gallery.close()

        # окна статистики/настроек — отдельные top-level окна; если их
        # не закрыть явно, приложение не завершится само по себе даже
        # после закрытия главного окна (Qt выходит из event loop только
        # когда закрыты ВСЕ top-level окна, а не только главное)
        if self.statistics_window is not None:
            self.statistics_window.close()

        if self.settings_window is not None:
            self.settings_window.close()

        super().closeEvent(event)

        if self._pending_restart:
            python = sys.executable

            # os.execv с "сырым" sys.argv не работает, если приложение
            # было запущено через `python -m app.main`: -m подставляет
            # в sys.argv[0] путь к уже РАЗРЕШЁННОМУ файлу модуля
            # (например, C:\...\app\main.py), и повторный запуск этого
            # пути КАК ОБЫЧНОГО СКРИПТА (без -m) кладёт в sys.path
            # директорию app/, а не корень проекта — импорт `from
            # app.core...` в самом же main.py тут же падает с
            # ModuleNotFoundError: No module named 'app'.
            #
            # Поэтому пересобираем вызов явно как `-m app.main`
            # (единственный документированный способ запуска — см.
            # CONTRIBUTING.md) вместо повторного использования
            # sys.argv[0] как есть. sys.argv[1:] (дополнительные CLI-
            # аргументы, если они когда-нибудь появятся) сохраняются —
            # они не зависят от того, использовался -m или нет.
            os.execv(python, [python, "-m", "app.main"] + sys.argv[1:])
