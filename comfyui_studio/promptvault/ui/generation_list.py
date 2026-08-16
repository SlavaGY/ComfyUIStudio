from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from comfyui_studio.promptvault.config import BUFFER_ROWS, CARD_HEIGHT, MAX_RATING
from comfyui_studio.promptvault.core.generation import Generation
from comfyui_studio.promptvault.core.thumbnails import make_thumb
from comfyui_studio.promptvault.ui.star_rating import StarRatingWidget


class GenerationList(QListWidget):
    """Список карточек генераций с виртуализацией: виджеты создаются и
    уничтожаются только для видимой области + небольшой запас (см.
    BUFFER_ROWS в app/config.py), а не для всех элементов сразу — это
    критично для больших библиотек (тысячи генераций).
    """

    generationSelected = Signal(int)
    favoriteToggled = Signal(int)
    ratingChanged = Signal(int, int)
    # см. set_page/append_generations (задача: настоящая виртуальная
    # пагинация) — эмитится, когда пользователь долистал до конца уже
    # ПОДГРУЖЕННОЙ части списка, но по данным set_page есть ещё
    # непоказанные строки (total_count > len(generations)).
    # Обработчик (см. MainWindow) должен вызвать
    # GalleryManager.load_more_filtered() и передать результат сюда же
    # через append_generations().
    moreNeeded = Signal()

    # массовые операции над выделенными генерациями (несут list[int] id)
    multipleFavoriteChanged = Signal(list, bool)
    multipleRatingChanged = Signal(list, int)
    deleteFromLibraryRequested = Signal(list)
    deleteFilesRequested = Signal(list)
    exportRequested = Signal(list)
    exportZipRequested = Signal(list)
    openJsonRequested = Signal(list)
    openInFolderRequested = Signal(list)
    editRequested = Signal(int)
    # массовое редактирование метаданных (задача: массовое
    # редактирование метаданных) — несёт список id, в отличие от
    # editRequested (одна генерация); показывается вместо editRequested
    # в контекстном меню, когда выделено больше одной генерации (см.
    # _build_context_menu)
    bulkEditRequested = Signal(list)
    addTagsRequested = Signal(list)

    def __init__(self):
        super().__init__()

        # ПРЕФИКС уже подгруженного отфильтрованного/отсортированного
        # результата — может быть короче total_count() (см. set_page/
        # append_generations); полный список никогда не держится в
        # памяти разом ради самого факта пагинации (задача: настоящая
        # виртуальная пагинация)
        self.generations: list[Generation] = []
        self._total_count = 0

        # не даёт заваливать GalleryManager повторными moreNeeded, пока
        # запрошенная страница ещё не подгрузилась (см. _request_more/
        # append_generations)
        self._more_requested = False

        # row_index -> GenerationCard (или _LoadingPlaceholder — для ещё
        # не подгруженных строк, см. _create_card), только для реально
        # созданных (видимых + буфер) виджетов
        self._active_widgets: dict[int, QWidget] = {}

        self.setSpacing(6)
        self.setIconSize(QSize(90, 90))
        self.setSelectionMode(QListWidget.ExtendedSelection)

        self.currentRowChanged.connect(
            self.on_selected
        )

        # отслеживаем прокрутку — по ней и определяем, какие карточки
        # нужно материализовать, а какие можно уничтожить
        self.verticalScrollBar().valueChanged.connect(
            self.update_visible_cards
        )

    # --------------------------------------------------

    def total_count(self) -> int:
        """Сколько строк всего должно быть в списке по данным
        последнего set_page — может быть больше len(self.generations),
        пока не все страницы подгружены (см. append_generations)."""

        return self._total_count

    def set_generations(
        self,
        generations: list[Generation],
        current_id: int | None = None,
    ) -> None:
        """set_page(...) с total_count == len(generations) — т.е. "уже
        загружено абсолютно всё, подгружать больше нечего". Более
        простое имя для вызывающего кода, которому виртуальная
        пагинация не нужна (используется и в тестах)."""

        self.set_page(generations, total_count=len(generations), current_id=current_id)

    def set_page(
        self,
        generations: list[Generation],
        total_count: int,
        current_id: int | None = None,
    ) -> None:
        """Полностью пересобирает список карточек (задача: настоящая
        виртуальная пагинация — Этап 2).

        total_count — сколько генераций всего проходит текущие фильтры
        (см. GalleryManager.filtered_total); generations — уже
        подгруженный ПРЕФИКС этого результата, может быть короче
        total_count. Под ещё не подгруженные строки создаются пустые
        элементы списка — нужны только чтобы прокрутка/скроллбар сразу
        отражали настоящий общий размер — их содержимое подгружается
        по мере прокрутки (см. moreNeeded/_create_card), а не сразу всё
        целиком: сам список никогда не держит в памяти больше, чем
        реально уже показано пользователю.

        На время пересборки сигналы виджета блокируются: clear(),
        addItem() и setItemWidget() потенциально могут провоцировать
        побочные currentRowChanged (конкретный механизм зависит от
        платформы/версии Qt — например, из-за фокуса на кнопке
        избранного внутри только что созданной карточки), которые
        через реентерабельную цепочку currentRowChanged ->
        generationSelected -> GalleryManager.select_by_index могли бы
        самопроизвольно подменить текущий выбор.

        current_id, если передан, восстанавливает выбор (по id
        генерации, а не по индексу строки — индексы после
        пересортировки/перефильтровки могут не соответствовать тем же
        генерациям) ВНУТРИ этой же заблокированной секции, одной
        атомарной операцией вместе с самой пересборкой. Раньше выбор
        восстанавливался отдельным вызовом setCurrentRow() снаружи, уже
        после разблокировки сигналов (см. MainWindow.
        _on_generations_changed) — список успевал ненадолго оказаться
        в промежуточном состоянии без корректного выделения, и именно
        в этом зазоре при быстром редактировании/автосинхронизации
        иногда проскакивал лишний currentRowChanged с "прыжком"
        выделения. Совмещение обеих операций в одну убирает этот зазор.
        """

        self.blockSignals(True)

        try:
            # если фокус сейчас на каком-то дочернем виджете карточки
            # (например, на только что нажатой кнопке избранного),
            # снимаем его ПЕРЕД clear() — QAbstractItemView внутренне
            # отслеживает виджеты элементов похоже на редакторы, и
            # уничтожение виджета, всё ещё держащего фокус, может
            # запутать это состояние
            focused = QApplication.focusWidget()

            if focused is not None and self.isAncestorOf(focused):
                self.setFocus()

            self.clear()

            self._active_widgets = {}
            # КОПИЯ, а не ссылка на тот же список: generations здесь —
            # обычно gallery.filtered_generations, а GalleryManager
            # растит его дальше через .extend() (см.
            # GalleryManager.load_more_filtered). Если бы это был один
            # и тот же объект, moreNeeded, вызванный из
            # update_visible_cards() чуть ниже ЕЩЁ ДО выхода из этого
            # метода, успел бы дозаписать туда новую страницу — и
            # append_generations затем задвоил бы её, приплюсовав ещё
            # раз то, что сюда уже "просочилось" через мутацию общего
            # списка.
            self.generations = list(generations)
            self._total_count = total_count
            self._more_requested = False

            for _ in range(total_count):
                item = QListWidgetItem()
                item.setSizeHint(QSize(0, CARD_HEIGHT))
                self.addItem(item)

            # загрузить видимые карточки
            self.update_visible_cards()

            target_row = -1

            if current_id is not None:
                for i, g in enumerate(generations):
                    if g.id == current_id:
                        target_row = i
                        break

            if target_row >= 0:
                # сигналы всё ещё заблокированы — currentRowChanged из
                # этого вызова наружу не уйдёт
                self.setCurrentRow(target_row)

            self._update_selection_highlight(self.currentRow())

        finally:
            self.blockSignals(False)

        # moreNeeded — обычный сигнал этого же QListWidget, поэтому
        # внутри blockSignals(True) выше (нужного для currentRowChanged)
        # он тоже подавлялся бы — _request_more() при этом всё равно
        # успевает выставить _more_requested=True молча, поэтому сбрасываем
        # его перед повторной проверкой, иначе она решит, что запрос
        # уже отправлен, и промолчит. Materialize/destroy карточек в
        # этом повторном вызове уже идемпотентны (состояние не
        # изменилось), интересен здесь только сам возможный moreNeeded
        self._more_requested = False
        self.update_visible_cards()

    def append_generations(self, new_generations: list[Generation]) -> None:
        """Добавляет уже подгруженную GalleryManager'ом следующую
        порцию отфильтрованного результата в конец уже показанного
        списка (см. moreNeeded/GalleryManager.load_more_filtered) — НЕ
        пересобирает список целиком, просто расширяет self.generations
        и перематериализует попавшие в текущую видимую область
        карточки-заглушки настоящими данными (задача: настоящая
        виртуальная пагинация)."""

        if not new_generations:
            return

        start = len(self.generations)
        self.generations = self.generations + new_generations
        end = len(self.generations)

        self._more_requested = False

        # карточки-заглушки ("Loading…"), уже материализованные в этом
        # диапазоне ДО подгрузки — пересоздаём настоящими данными
        for i in range(start, end):
            if i in self._active_widgets:
                self._destroy_card(i)

        self.update_visible_cards()

    # --------------------------------------------------

    def _request_more(self) -> None:
        """Просит GalleryManager подгрузить ещё одну страницу (см.
        moreNeeded/GalleryManager.load_more_filtered) — не чаще одного
        раза, пока не подгрузится хоть что-то новое (см.
        append_generations), чтобы не заваливать GalleryManager
        повторными запросами при каждой мельчайшей прокрутке."""

        if self._more_requested:
            return

        self._more_requested = True
        self.moreNeeded.emit()

    # --------------------------------------------------

    def on_selected(self, index: int) -> None:

        self._update_selection_highlight(index)

        if index >= 0:

            self.generationSelected.emit(
                index
            )

    # --------------------------------------------------

    def _update_selection_highlight(self, selected_index: int) -> None:

        # обновляем только реально существующие (видимые) карточки —
        # для остальных правильное состояние выставится в _create_card
        # в момент их материализации
        for i, widget in self._active_widgets.items():
            widget.set_selected(i == selected_index)

    # --------------------------------------------------

    def update_visible_cards(self) -> None:

        if self.count() == 0:
            return

        viewport = self.viewport().rect()

        first_visible = None
        last_visible = None

        for i in range(self.count()):

            item = self.item(i)
            item_rect = self.visualItemRect(item)

            if viewport.intersects(item_rect):

                if first_visible is None:
                    first_visible = i

                last_visible = i

        if first_visible is None:
            return

        keep_from = max(0, first_visible - BUFFER_ROWS)
        keep_to = min(self.count() - 1, last_visible + BUFFER_ROWS)

        # материализуем карточки, попавшие в видимую область + буфер
        for i in range(keep_from, keep_to + 1):

            if i not in self._active_widgets:
                self._create_card(i)

        # уничтожаем карточки, вышедшие за пределы буфера — освобождает
        # память на больших библиотеках вместо хранения всех карточек
        # разом
        for i in list(self._active_widgets.keys()):

            if i < keep_from or i > keep_to:
                self._destroy_card(i)

        # видимая область (+ буфер) дотянулась до конца уже
        # подгруженного префикса, но по total_count есть ещё
        # непоказанные строки — пора подгрузить следующую страницу (см.
        # moreNeeded/задача: настоящая виртуальная пагинация)
        if keep_to >= len(self.generations) - 1 and len(self.generations) < self._total_count:
            self._request_more()

    # --------------------------------------------------

    def _create_card(self, index: int) -> None:

        item = self.item(index)

        if index >= len(self.generations):
            # строка ещё не подгружена (см. set_page/append_generations)
            # — временная заглушка вместо настоящей карточки
            widget = _LoadingPlaceholder()

            self.setItemWidget(item, widget)
            self._active_widgets[index] = widget

            return

        generation = self.generations[index]

        widget = GenerationCard(generation)

        widget.favoriteToggled.connect(self.favoriteToggled.emit)
        widget.ratingChanged.connect(self.ratingChanged.emit)

        widget.set_selected(index == self.currentRow())

        self.setItemWidget(item, widget)
        self._active_widgets[index] = widget

        widget.load_preview()

    # --------------------------------------------------

    def _destroy_card(self, index: int) -> None:

        widget = self._active_widgets.pop(index, None)

        if widget is None:
            return

        item = self.item(index)

        if item is not None:
            self.setItemWidget(item, None)

        widget.deleteLater()

    # --------------------------------------------------
    # множественное выделение / контекстное меню

    def selected_ids(self) -> list[int]:
        """id генераций, выделенных в данный момент (в порядке строк)."""

        rows = sorted({index.row() for index in self.selectedIndexes()})

        return [
            self.generations[row].id
            for row in rows
            if row < len(self.generations)
        ]

    def contextMenuEvent(self, event) -> None:

        ids = self.selected_ids()

        if not ids:
            return

        menu = self._build_context_menu(ids)

        menu.exec(event.globalPos())

    def _build_context_menu(self, ids: list[int]) -> QMenu:
        """Строит контекстное меню для переданных id (вынесено отдельно
        от contextMenuEvent, чтобы можно было протестировать состав
        меню без блокирующего QMenu.exec())."""

        menu = QMenu(self)

        # общий суффикс "(N)" для всех пунктов меню, действующих сразу
        # на все выделенные генерации — вынесен один раз вместо
        # повторения f"...({len(ids)})" в каждом отдельном addAction
        count_suffix = f" ({len(ids)})"

        # "Open JSON" — раньше была отдельная кнопка на тулбаре,
        # работавшая только с текущей ОДНОЙ выделенной генерацией;
        # перенесено сюда с поддержкой массового выделения — каждый
        # выбранный JSON открывается в ассоциированном приложении ОС
        # (см. MainWindow._on_open_json_requested)
        menu.addAction(
            self.tr("Open JSON{}").format(count_suffix),
            lambda: self.openJsonRequested.emit(ids)
        )
        # "Open in folder" — выделяет файлы в файловом менеджере ОС;
        # если выбранные генерации лежат в разных папках, открывается
        # отдельное окно на каждую папку (см.
        # app.utils.reveal_in_file_manager)
        menu.addAction(
            self.tr("Open in folder{}").format(count_suffix),
            lambda: self.openInFolderRequested.emit(ids)
        )

        menu.addSeparator()

        if len(ids) == 1:
            menu.addAction(
                self.tr("Edit metadata..."), lambda: self.editRequested.emit(ids[0])
            )
            menu.addSeparator()
        else:
            menu.addAction(
                self.tr("Bulk edit metadata{}...").format(count_suffix),
                lambda: self.bulkEditRequested.emit(ids)
            )
            menu.addSeparator()

        menu.addAction(
            self.tr("Add to favorites{}").format(count_suffix),
            lambda: self.multipleFavoriteChanged.emit(ids, True)
        )
        menu.addAction(
            self.tr("Remove from favorites{}").format(count_suffix),
            lambda: self.multipleFavoriteChanged.emit(ids, False)
        )

        rating_menu = menu.addMenu(self.tr("Set rating"))

        for value in range(MAX_RATING, 0, -1):
            rating_menu.addAction(
                "★" * value,
                lambda v=value: self.multipleRatingChanged.emit(ids, v)
            )

        rating_menu.addAction(
            self.tr("Clear rating"),
            lambda: self.multipleRatingChanged.emit(ids, 0)
        )

        menu.addSeparator()

        # "Add tag(s)..." — работает как с одной, так и с несколькими
        # выделенными генерациями сразу (задача: пользовательские
        # теги, поддержка массового выделения); сам ввод тега(ов) —
        # ответственность MainWindow (см. _on_add_tags_requested), это
        # меню лишь передаёт список id
        menu.addAction(
            self.tr("Add tag(s){}...").format(count_suffix),
            lambda: self.addTagsRequested.emit(ids)
        )

        menu.addSeparator()

        menu.addAction(
            self.tr("Export JSON{}...").format(count_suffix),
            lambda: self.exportRequested.emit(ids)
        )
        menu.addAction(
            self.tr("Export as ZIP (with images){}...").format(count_suffix),
            lambda: self.exportZipRequested.emit(ids)
        )

        menu.addSeparator()

        menu.addAction(
            self.tr("Remove from library{}").format(count_suffix),
            lambda: self.deleteFromLibraryRequested.emit(ids)
        )
        menu.addAction(
            self.tr("Delete files + record{}").format(count_suffix),
            lambda: self.deleteFilesRequested.emit(ids)
        )

        return menu


# ======================================================
# Заглушка для ещё не подгруженной строки
# ======================================================


class _LoadingPlaceholder(QWidget):
    """Временный виджет для строки, которую GenerationList уже создал в
    списке (ради корректного размера прокрутки — см.
    GenerationList.set_page), но данные для неё ещё не подгружены (см.
    moreNeeded/GalleryManager.load_more_filtered) — задача: настоящая
    виртуальная пагинация.

    Виден только на долю секунды, пока не подгрузится следующая
    страница — после этого GenerationList.append_generations
    пересоздаёт эту строку уже настоящей GenerationCard."""

    def __init__(self):
        super().__init__()

        layout = QHBoxLayout(self)
        layout.addWidget(QLabel(self.tr("Loading…")))

    def set_selected(self, selected: bool) -> None:
        # заглушка не может быть "текущей выбранной" строкой сколь-либо
        # осмысленно — но _update_selection_highlight обходит ВСЕ
        # активные виджеты без разбора их типа, так что метод должен
        # существовать хотя бы как no-op
        pass


# ======================================================
# Card
# ======================================================


class GenerationCard(QWidget):
    """Карточка одной генерации: превью + избранное + рейтинг + инфо.

    Мутацию Generation.favorite/Generation.rating теперь выполняет
    GalleryManager (единый источник истины) — карточка лишь эмитит id
    и, для мгновенного отклика на клик (не дожидаясь debounce-пересборки
    списка в GalleryManager), сама обновляет свой внешний вид локально.
    """

    favoriteToggled = Signal(int)
    ratingChanged = Signal(int, int)

    def __init__(self, generation: Generation):

        super().__init__()

        self.setObjectName("generationCard")

        self.generation = generation
        self.preview_loaded = False

        self.setMinimumHeight(
            110
        )

        layout = QHBoxLayout(self)

        layout.setContentsMargins(
            5,
            5,
            5,
            5
        )

        # -------- preview --------

        self.preview = QLabel()

        self.preview.setFixedSize(
            90,
            90
        )

        self.preview.setAlignment(
            Qt.AlignCenter
        )

        layout.addWidget(
            self.preview
        )

        # -------- info --------

        text_layout = QVBoxLayout()

        # -------- избранное + рейтинг --------

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)

        self.favorite_btn = QPushButton()
        self.favorite_btn.setObjectName("favoriteButton")
        self.favorite_btn.setFlat(True)
        self.favorite_btn.setCursor(Qt.PointingHandCursor)
        self.favorite_btn.setToolTip(self.tr("Favorite"))
        self.favorite_btn.clicked.connect(self._on_favorite_clicked)

        self.rating_widget = StarRatingWidget(generation.rating)
        self.rating_widget.ratingChanged.connect(self._on_rating_changed)

        top_row.addWidget(self.favorite_btn)
        top_row.addStretch()
        top_row.addWidget(self.rating_widget)

        text_layout.addLayout(top_row)

        self._render_favorite(generation.favorite)

        timestamp = QLabel(
            generation.timestamp
        )

        model = QLabel(
            self.tr("Model: {}").format(generation.model)
        )

        images = QLabel(
            self.tr("Images: {}").format(len(generation.images))
        )

        loras = QLabel(
            self.tr("LoRA: {}").format(len(generation.loras))
        )

        text_layout.addWidget(
            timestamp
        )

        text_layout.addWidget(
            model
        )

        text_layout.addWidget(
            images
        )

        text_layout.addWidget(
            loras
        )

        # -------- пользовательские теги (задача: пользовательские теги) --------
        # небольшим серым текстом, и только если у генерации вообще
        # есть теги — чтобы не занимать место пустой строкой в
        # подавляющем большинстве карточек, у которых тегов нет

        if generation.custom_tags:

            tags_label = QLabel(
                self.tr("Tags: {}").format(
                    ", ".join(sorted(generation.custom_tags, key=str.lower))
                )
            )
            tags_label.setStyleSheet("color: gray; font-size: 10px;")
            tags_label.setWordWrap(True)

            text_layout.addWidget(
                tags_label
            )

        layout.addLayout(
            text_layout
        )

    # --------------------------------------------------

    def set_selected(self, selected: bool) -> None:

        # карточка — непрозрачный виджет поверх элемента списка,
        # поэтому подсвечиваем выделение сами через динамическое
        # свойство, а не полагаемся на QListWidget::item:selected
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    # --------------------------------------------------

    def _on_favorite_clicked(self) -> None:

        # мгновенный визуальный отклик, не дожидаясь debounce-пересборки
        # списка в GalleryManager — реальное состояние (и его сохранение
        # в БД) полностью на стороне GalleryManager.toggle_favorite
        new_value = not self.generation.favorite

        self._render_favorite(new_value)

        self.favoriteToggled.emit(self.generation.id)

    # --------------------------------------------------

    def _on_rating_changed(self, value: int) -> None:

        self.ratingChanged.emit(self.generation.id, value)

    # --------------------------------------------------

    def _render_favorite(self, is_favorite: bool) -> None:

        self.favorite_btn.setText(
            self.tr("★ Favorite") if is_favorite else self.tr("☆ Favorite")
        )

    # --------------------------------------------------

    def load_preview(self) -> None:

        if self.preview_loaded:
            return

        self.preview_loaded = True

        if not self.generation.images:
            return

        image_path = (
            self.generation.path.parent /
            self.generation.images[0].file
        )

        if not image_path.exists():
            return

        thumb_path = make_thumb(
            image_path
        )

        if thumb_path is None:
            return

        pixmap = QPixmap(
            str(thumb_path)
        )

        self.preview.setPixmap(
            pixmap
        )
