from contextlib import ExitStack

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from comfyui_studio.promptvault.core.generation_filter import FilterOptions
from comfyui_studio.promptvault.ui.tri_state_checkbox import TriStateFilterCheckBox

# максимальные размеры области со списком LoRA, чтобы попап
# не растягивался на весь экран при большом количестве/длине LoRA
LORA_LIST_MAX_HEIGHT = 160
LORA_LIST_MAX_WIDTH = 420


class FilterPopup(QFrame):

    applied = Signal()
    resetRequested = Signal()

    def __init__(self, parent=None, semantic_search_enabled: bool = True):
        """semantic_search_enabled: если False, строка "Semantic search"
        вообще не строится (не просто дизейблится) — семантический
        поиск выключен в настройках PromptVault (см. SettingsWindow,
        раздел Search) или физически недоступен (нет sentence-
        transformers/torch). Раньше поле показывалось всегда, даже
        когда семантический поиск не работал бы — вводило в
        заблуждение. Проверяется один раз при построении (значение
        читается из GalleryManager в MainWindow.__init__) — включение
        семантического поиска обратно в настройках применяется после
        перезапуска PromptVault, как и остальные последствия этого
        переключателя (см. semantic_search_hint в SettingsWindow)."""

        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint)

        self.setObjectName("filterPopup")
        self.setWindowTitle(self.tr("Filters"))
        self._semantic_search_enabled = semantic_search_enabled

        layout = QVBoxLayout(self)

        form = QFormLayout()

        self.semantic_search_box = QLineEdit()
        self.semantic_search_box.setPlaceholderText(
            self.tr("e.g. 'a girl in a forest at night' (ru/en, matches by meaning)")
        )
        self.semantic_search_box.setToolTip(
            self.tr(
                "Semantic search — finds prompts by meaning (works across "
                "Russian/English), not just exact substring matches."
            )
        )

        self.model_box = QComboBox()
        self.model_box.addItem(self.tr("Any"))

        self.sampler_box = QComboBox()
        self.sampler_box.addItem(self.tr("Any"))

        self.favorites_box = QComboBox()
        self.favorites_box.addItems([
            self.tr("Any"), self.tr("Only favorites"), self.tr("Without favorites"),
        ])

        self.rating_box = QComboBox()
        self.rating_box.addItems([
            self.tr("Any"),
            "★ 1+",
            "★★ 2+",
            "★★★ 3+",
            "★★★★ 4+",
            "★★★★★ 5",
        ])

        if semantic_search_enabled:
            form.addRow(self.tr("Semantic search"), self.semantic_search_box)
        form.addRow(self.tr("Model"), self.model_box)
        form.addRow(self.tr("Sampler"), self.sampler_box)
        form.addRow(self.tr("Favorites"), self.favorites_box)
        form.addRow(self.tr("Min rating"), self.rating_box)

        # -------- диапазоны CFG / Steps --------

        self.cfg_min_box = QDoubleSpinBox()
        self.cfg_min_box.setRange(0.0, 100.0)
        self.cfg_min_box.setSingleStep(0.5)
        self.cfg_min_box.setSpecialValueText(self.tr("Any"))

        self.cfg_max_box = QDoubleSpinBox()
        self.cfg_max_box.setRange(0.0, 100.0)
        self.cfg_max_box.setSingleStep(0.5)
        self.cfg_max_box.setValue(100.0)
        self.cfg_max_box.setSpecialValueText(self.tr("Any"))

        cfg_row = QHBoxLayout()
        cfg_row.addWidget(self.cfg_min_box)
        cfg_row.addWidget(QLabel("—"))
        cfg_row.addWidget(self.cfg_max_box)

        form.addRow(self.tr("CFG"), cfg_row)

        self.steps_min_box = QSpinBox()
        self.steps_min_box.setRange(0, 1000)
        self.steps_min_box.setSpecialValueText(self.tr("Any"))

        self.steps_max_box = QSpinBox()
        self.steps_max_box.setRange(0, 1000)
        self.steps_max_box.setValue(1000)
        self.steps_max_box.setSpecialValueText(self.tr("Any"))

        steps_row = QHBoxLayout()
        steps_row.addWidget(self.steps_min_box)
        steps_row.addWidget(QLabel("—"))
        steps_row.addWidget(self.steps_max_box)

        form.addRow(self.tr("Steps"), steps_row)

        layout.addLayout(form)

        # -------- LoRA --------

        layout.addWidget(QLabel(self.tr("LoRA")))

        self.lora_checkboxes = {}

        self.lora_container = QWidget()
        self.lora_layout = QVBoxLayout(self.lora_container)
        self.lora_layout.setContentsMargins(4, 4, 4, 4)
        self.lora_layout.setSpacing(2)
        self.lora_layout.addStretch()

        self.lora_scroll = QScrollArea()
        self.lora_scroll.setWidgetResizable(True)
        self.lora_scroll.setWidget(self.lora_container)
        self.lora_scroll.setMaximumHeight(LORA_LIST_MAX_HEIGHT)
        self.lora_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        layout.addWidget(self.lora_scroll)

        # -------- пользовательские теги (задача: пользовательские теги) --------

        layout.addWidget(QLabel(self.tr("Custom Tags")))

        self.tag_checkboxes = {}

        self.tag_container = QWidget()
        self.tag_layout = QVBoxLayout(self.tag_container)
        self.tag_layout.setContentsMargins(4, 4, 4, 4)
        self.tag_layout.setSpacing(2)
        self.tag_layout.addStretch()

        self.tag_scroll = QScrollArea()
        self.tag_scroll.setWidgetResizable(True)
        self.tag_scroll.setWidget(self.tag_container)
        self.tag_scroll.setMaximumHeight(LORA_LIST_MAX_HEIGHT)
        self.tag_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        layout.addWidget(self.tag_scroll)

        self.reset_btn = QPushButton(self.tr("Reset"))
        self.apply_btn = QPushButton(self.tr("Apply"))
        self.apply_btn.setObjectName("primaryButton")

        buttons = QVBoxLayout()

        buttons.addWidget(self.apply_btn)
        buttons.addWidget(self.reset_btn)

        layout.addLayout(buttons)

        self.apply_btn.clicked.connect(self.apply)

        self.reset_btn.clicked.connect(self.reset)

    def apply(self):
        self.applied.emit()
        self.hide()

    def reset(self):

        self.semantic_search_box.clear()
        self.model_box.setCurrentIndex(0)
        self.sampler_box.setCurrentIndex(0)
        self.favorites_box.setCurrentIndex(0)
        self.rating_box.setCurrentIndex(0)

        self.cfg_min_box.setValue(self.cfg_min_box.minimum())
        self.cfg_max_box.setValue(self.cfg_max_box.maximum())
        self.steps_min_box.setValue(self.steps_min_box.minimum())
        self.steps_max_box.setValue(self.steps_max_box.maximum())

        for checkbox in self.lora_checkboxes.values():
            checkbox.set_state(included=False, excluded=False)

        for checkbox in self.tag_checkboxes.values():
            checkbox.set_state(included=False, excluded=False)

        self.resetRequested.emit()

        self.hide()

    def set_models(self, models):

        current = self.model_box.currentText()

        self.model_box.clear()
        self.model_box.addItem(self.tr("Any"))

        for model in sorted(models):
            self.model_box.addItem(model)

        index = self.model_box.findText(current)

        if index >= 0:
            self.model_box.setCurrentIndex(index)

    def set_samplers(self, samplers):

        current = self.sampler_box.currentText()

        self.sampler_box.clear()
        self.sampler_box.addItem(self.tr("Any"))

        for sampler in sorted(samplers):
            self.sampler_box.addItem(sampler)

        index = self.sampler_box.findText(current)

        if index >= 0:
            self.sampler_box.setCurrentIndex(index)

    def favorites_only(self):
        """Индекс, а не currentText() — пункты 0/1/2 ("Any"/"Only
        favorites"/"Without favorites", см. __init__) переводятся через
        self.tr() и после перевода на другой язык уже не равны
        английским литералам, так что сравнение текста сломало бы
        логику фильтра при переключении языка (задача: полный аудит
        строк UI под self.tr())."""

        index = self.favorites_box.currentIndex()

        if index == 1:
            return True

        if index == 2:
            return False

        return None

    def min_rating(self):

        index = self.rating_box.currentIndex()

        # индекс 0 = "Any", 1..5 = "★ 1+".."★★★★★ 5"
        if index <= 0:
            return None

        return index

    def set_loras(self, loras):

        included = {
            name
            for name, checkbox in self.lora_checkboxes.items()
            if checkbox.is_included()
        }
        excluded = {
            name
            for name, checkbox in self.lora_checkboxes.items()
            if checkbox.is_excluded()
        }

        # убрать текущие чекбоксы (кроме финального stretch)
        while self.lora_layout.count() > 1:
            item = self.lora_layout.takeAt(0)

            if item.widget():
                item.widget().deleteLater()

        self.lora_checkboxes = {}

        for name in sorted(loras):

            checkbox = TriStateFilterCheckBox(name)
            checkbox.set_state(included=name in included, excluded=name in excluded)

            # без этого чекбокс растягивается на всю ширину контейнера
            # (под самое длинное имя LoRA), а кликабельной остаётся только
            # область под собственным текстом — короткие имена было
            # невозможно отметить кликом правее их текста
            checkbox.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

            self.lora_layout.insertWidget(
                self.lora_layout.count() - 1,
                checkbox
            )

            self.lora_checkboxes[name] = checkbox

        # Qt кеширует geometry всплывающего окна после первого показа,
        # поэтому просто adjustSize() не подхватывает новый размер
        # контента, добавленный, пока попап был скрыт. Пересчитываем
        # нужную высоту и ширину списка LoRA и принудительно
        # активируем layout.
        content_height = self.lora_container.sizeHint().height()
        target_height = min(content_height, LORA_LIST_MAX_HEIGHT)
        self.lora_scroll.setMinimumHeight(target_height)

        metrics = self.fontMetrics()
        # запас под чекбокс-индикатор, внутренние отступы и полосу прокрутки
        text_padding = 60

        longest_text = max(
            (metrics.horizontalAdvance(name) for name in loras),
            default=0
        )

        target_width = min(longest_text + text_padding, LORA_LIST_MAX_WIDTH)
        self.lora_scroll.setMinimumWidth(target_width)

        self.layout().invalidate()
        self.layout().activate()
        self.adjustSize()

    def loras(self):

        selected = [
            name
            for name, checkbox in self.lora_checkboxes.items()
            if checkbox.is_included()
        ]

        return selected or None

    def excluded_loras(self):

        selected = [
            name
            for name, checkbox in self.lora_checkboxes.items()
            if checkbox.is_excluded()
        ]

        return selected or None

    def set_custom_tags(self, tags):
        """См. set_loras — то же самое построение списка чекбоксов,
        только для пользовательских тегов."""

        included = {
            name
            for name, checkbox in self.tag_checkboxes.items()
            if checkbox.is_included()
        }
        excluded = {
            name
            for name, checkbox in self.tag_checkboxes.items()
            if checkbox.is_excluded()
        }

        while self.tag_layout.count() > 1:
            item = self.tag_layout.takeAt(0)

            if item.widget():
                item.widget().deleteLater()

        self.tag_checkboxes = {}

        for name in sorted(tags):

            checkbox = TriStateFilterCheckBox(name)
            checkbox.set_state(included=name in included, excluded=name in excluded)
            checkbox.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

            self.tag_layout.insertWidget(
                self.tag_layout.count() - 1,
                checkbox
            )

            self.tag_checkboxes[name] = checkbox

        content_height = self.tag_container.sizeHint().height()
        target_height = min(content_height, LORA_LIST_MAX_HEIGHT)
        self.tag_scroll.setMinimumHeight(target_height)

        metrics = self.fontMetrics()
        text_padding = 60

        longest_text = max(
            (metrics.horizontalAdvance(name) for name in tags),
            default=0
        )

        target_width = min(longest_text + text_padding, LORA_LIST_MAX_WIDTH)
        self.tag_scroll.setMinimumWidth(target_width)

        self.layout().invalidate()
        self.layout().activate()
        self.adjustSize()

    def custom_tags(self):

        selected = [
            name
            for name, checkbox in self.tag_checkboxes.items()
            if checkbox.is_included()
        ]

        return selected or None

    def excluded_custom_tags(self):

        selected = [
            name
            for name, checkbox in self.tag_checkboxes.items()
            if checkbox.is_excluded()
        ]

        return selected or None

    def semantic_query(self) -> str:
        """Пустая строка, если строка "Semantic search" не показана
        (см. __init__, semantic_search_enabled) — даже если в самом
        self.semantic_search_box случайно оказался непустой текст
        (например, restore() ниже подставил его из ранее сохранённого
        состояния фильтров, записанного, пока семантический поиск ещё
        был включён): раз поле не видно и недоступно для
        редактирования, оно не должно незаметно продолжать влиять на
        результат фильтрации."""

        if not self._semantic_search_enabled:
            return ""
        return self.semantic_search_box.text().strip()

    def model(self):
        """Индекс 0 = "Any" (см. favorites_only — тот же повод не
        сравнивать currentText() с непереведённым литералом)."""

        if self.model_box.currentIndex() == 0:
            return None

        return self.model_box.currentText()

    def sampler(self):
        """См. model() — тот же приём и та же причина."""

        if self.sampler_box.currentIndex() == 0:
            return None

        return self.sampler_box.currentText()

    # --------------------------------------------------
    # диапазоны CFG / Steps

    def min_cfg(self) -> float | None:

        value = self.cfg_min_box.value()

        return None if value <= self.cfg_min_box.minimum() else value

    def max_cfg(self) -> float | None:

        value = self.cfg_max_box.value()

        return None if value >= self.cfg_max_box.maximum() else value

    def min_steps(self) -> int | None:

        value = self.steps_min_box.value()

        return None if value <= self.steps_min_box.minimum() else value

    def max_steps(self) -> int | None:

        value = self.steps_max_box.value()

        return None if value >= self.steps_max_box.maximum() else value

    # --------------------------------------------------

    def apply_options(self, options: FilterOptions) -> None:
        """Восстанавливает состояние виджетов по уже сохранённым
        параметрам фильтрации (например, при восстановлении из
        QSettings при старте приложения).

        Ничего из этого не должно интерпретироваться как ввод
        пользователя — каждый setCurrentIndex/setValue/setChecked ниже
        иначе эмитит собственный сигнал изменения (currentIndexChanged,
        valueChanged, stateChanged), на которые сейчас никто не
        подписан, но которые легко случайно перепутать с реальным
        действием пользователя, если однажды здесь появится "живое"
        превью числа отфильтрованных генераций. Блокируем сигналы на
        время программного восстановления, чтобы это не стало
        неожиданностью в будущем.
        """

        widgets = (
            self.semantic_search_box, self.model_box, self.sampler_box,
            self.favorites_box, self.rating_box, self.cfg_min_box,
            self.cfg_max_box, self.steps_min_box, self.steps_max_box,
            *self.lora_checkboxes.values(),
            *self.tag_checkboxes.values(),
        )

        with ExitStack() as blockers:

            for w in widgets:
                blockers.enter_context(QSignalBlocker(w))

            self.semantic_search_box.setText(options.semantic_query)

            if options.model:
                index = self.model_box.findText(options.model)
                if index >= 0:
                    self.model_box.setCurrentIndex(index)

            if options.sampler:
                index = self.sampler_box.findText(options.sampler)
                if index >= 0:
                    self.sampler_box.setCurrentIndex(index)

            if options.favorites_only is True:
                self.favorites_box.setCurrentIndex(1)
            elif options.favorites_only is False:
                self.favorites_box.setCurrentIndex(2)
            else:
                self.favorites_box.setCurrentIndex(0)

            self.rating_box.setCurrentIndex(options.min_rating or 0)

            self.cfg_min_box.setValue(
                options.min_cfg if options.min_cfg is not None else self.cfg_min_box.minimum()
            )
            self.cfg_max_box.setValue(
                options.max_cfg if options.max_cfg is not None else self.cfg_max_box.maximum()
            )
            self.steps_min_box.setValue(
                options.min_steps if options.min_steps is not None else self.steps_min_box.minimum()
            )
            self.steps_max_box.setValue(
                options.max_steps if options.max_steps is not None else self.steps_max_box.maximum()
            )

            if options.loras or options.excluded_loras:
                for name, checkbox in self.lora_checkboxes.items():
                    checkbox.set_state(
                        included=bool(options.loras and name in options.loras),
                        excluded=bool(options.excluded_loras and name in options.excluded_loras),
                    )

            if options.custom_tags or options.excluded_custom_tags:
                for name, checkbox in self.tag_checkboxes.items():
                    checkbox.set_state(
                        included=bool(options.custom_tags and name in options.custom_tags),
                        excluded=bool(
                            options.excluded_custom_tags
                            and name in options.excluded_custom_tags
                        ),
                    )
