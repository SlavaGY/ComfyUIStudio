from __future__ import annotations

import datetime
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.core.generation import Generation


class MetadataEditor(QDialog):
    """Диалог редактирования метаданных одной генерации.

    Не сохраняет ничего сам — только собирает изменения пользователя и
    эмитит saved(generation_id, update_dict). Собственно сохранение
    (перезапись JSON-файла + обновление БД) выполняет
    GalleryManager.update_generation_metadata.

    Идентичность генерации (timestamp, generation_time) через этот
    диалог не редактируется намеренно — это ключ, по которому запись
    находится в БД при синхронизации с диском.

    Диалог НЕ закрывается сам по нажатию Save — он лишь эмитит saved()
    и остаётся открытым. Закрыть его (вызвав accept()) должен вызывающий
    код, и только после того, как убедится, что сохранение прошло
    успешно (например, дождавшись GalleryManager.metadata_updated).
    Если сохранение упадёт, диалог остаётся на экране вместе с уже
    введёнными пользователем изменениями — ничего не теряется, и
    сообщение об ошибке (см. GalleryManager.error_occurred) увидит тот
    же пользователь, который ещё смотрит на этот диалог, а не пустой
    экран позади уже закрывшегося окна.

    Пользовательские теги (задача: пользовательские теги) сохраняются
    ОТДЕЛЬНО от update_dict — это не часть исходного JSON-файла (как
    model/sampler/cfg/...), а чисто пользовательские данные в БД (как
    favorite/rating), поэтому диалог эмитит их через отдельный сигнал
    tagsChanged(generation_id, tags), который вызывающий код (см.
    MainWindow._on_edit_requested) подключает напрямую к
    GalleryManager.set_custom_tags, минуя update_generation_metadata.

    История изменений (задача: история изменений метаданных) диалогом
    сама не запрашивается — repository/GalleryManager ему недоступны
    (см. архитектурное правило в CONTRIBUTING.md: UI-виджеты не дёргают
    репозиторий напрямую). Вызывающий код (MainWindow) сам вызывает
    GalleryManager.get_metadata_history() и передаёт результат через
    параметр history — кнопка "History..." появляется, только если он
    непустой.
    """

    saved = Signal(int, dict)
    tagsChanged = Signal(int, list)

    def __init__(
        self,
        generation: Generation,
        known_models: set[str] | None = None,
        known_samplers: set[str] | None = None,
        history: list[dict[str, Any]] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)

        self.generation = generation
        self._history = history or []

        self.setWindowTitle(self.tr("Edit metadata — {}").format(generation.timestamp))
        self.resize(520, 480)

        layout = QVBoxLayout(self)

        form = QFormLayout()

        self.model_box = QComboBox()
        self.model_box.setEditable(True)
        self.model_box.addItems(sorted(known_models or []))
        self.model_box.setCurrentText(generation.model)

        self.sampler_box = QComboBox()
        self.sampler_box.setEditable(True)
        self.sampler_box.addItems(sorted(known_samplers or []))
        self.sampler_box.setCurrentText(generation.sampler)

        self.cfg_box = QDoubleSpinBox()
        self.cfg_box.setRange(0.0, 100.0)
        self.cfg_box.setSingleStep(0.5)
        self.cfg_box.setValue(generation.cfg)

        self.steps_box = QSpinBox()
        self.steps_box.setRange(0, 1000)
        self.steps_box.setValue(generation.steps)

        form.addRow(self.tr("Model"), self.model_box)
        form.addRow(self.tr("Sampler"), self.sampler_box)
        form.addRow(self.tr("CFG"), self.cfg_box)
        form.addRow(self.tr("Steps"), self.steps_box)

        layout.addLayout(form)

        layout.addWidget(QLabel(self.tr("Positive prompt")))

        self.positive_edit = QPlainTextEdit(generation.positive)
        layout.addWidget(self.positive_edit)

        layout.addWidget(QLabel(self.tr("Negative prompt")))

        self.negative_edit = QPlainTextEdit(generation.negative)
        layout.addWidget(self.negative_edit)

        # -------- пользовательские теги (задача: пользовательские теги) --------

        layout.addWidget(QLabel(self.tr("Custom Tags")))

        self.tags_list = QListWidget()
        self.tags_list.addItems(sorted(generation.custom_tags, key=str.lower))
        self.tags_list.setMaximumHeight(100)
        layout.addWidget(self.tags_list)

        tag_input_row = QHBoxLayout()

        self.tag_input = QLineEdit()
        self.tag_input.setPlaceholderText(self.tr("New tag"))
        self.tag_input.returnPressed.connect(self._on_add_tag_clicked)

        self.add_tag_btn = QPushButton(self.tr("Add"))
        self.add_tag_btn.clicked.connect(self._on_add_tag_clicked)

        self.remove_tag_btn = QPushButton(self.tr("Remove"))
        self.remove_tag_btn.clicked.connect(self._on_remove_tag_clicked)

        tag_input_row.addWidget(self.tag_input)
        tag_input_row.addWidget(self.add_tag_btn)
        tag_input_row.addWidget(self.remove_tag_btn)

        layout.addLayout(tag_input_row)

        # -------- история изменений (задача: история изменений метаданных) --------

        bottom_row = QHBoxLayout()

        self.history_btn = QPushButton(self.tr("History ({})...").format(len(self._history)))
        self.history_btn.setEnabled(bool(self._history))
        self.history_btn.clicked.connect(self._on_history_clicked)
        bottom_row.addWidget(self.history_btn)

        bottom_row.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._on_save_clicked)
        buttons.rejected.connect(self.reject)

        bottom_row.addWidget(buttons)

        layout.addLayout(bottom_row)

    # --------------------------------------------------
    # пользовательские теги

    def _on_add_tag_clicked(self) -> None:

        tag = self.tag_input.text().strip()

        if not tag:
            return

        existing = {
            self.tags_list.item(i).text().lower()
            for i in range(self.tags_list.count())
        }

        if tag.lower() not in existing:
            self.tags_list.addItem(tag)

        self.tag_input.clear()

    def _on_remove_tag_clicked(self) -> None:

        for item in self.tags_list.selectedItems():
            self.tags_list.takeItem(self.tags_list.row(item))

    def _current_tags(self) -> list[str]:

        return [
            self.tags_list.item(i).text()
            for i in range(self.tags_list.count())
        ]

    # --------------------------------------------------
    # история изменений

    def _on_history_clicked(self) -> None:

        dialog = MetadataHistoryDialog(self._history, parent=self)
        dialog.exec()

    # --------------------------------------------------

    def _on_save_clicked(self) -> None:
        """Собирает изменения и эмитит saved() — но НЕ закрывает диалог.

        Раньше это был override accept(), который безусловно вызывал
        super().accept() сразу после emit(). Из-за этого диалог
        закрывался даже если сохранение (GalleryManager.
        update_generation_metadata) падало: пользователь видел
        сообщение об ошибке, нажимал OK — а диалог с его
        отредактированными полями всё равно исчезал, и изменения
        приходилось вводить заново. Теперь закрытие — ответственность
        вызывающего кода (см. MainWindow._on_edit_requested), который
        вызывает accept() только после подтверждения успеха.
        """

        update_dict = {
            "model": self.model_box.currentText().strip(),
            "sampler": self.sampler_box.currentText().strip(),
            "cfg": self.cfg_box.value(),
            "steps": self.steps_box.value(),
            "positive": self.positive_edit.toPlainText(),
            "negative": self.negative_edit.toPlainText(),
        }

        self.saved.emit(self.generation.id, update_dict)
        self.tagsChanged.emit(self.generation.id, self._current_tags())


class MetadataHistoryDialog(QDialog):
    """Read-only просмотр истории изменений метаданных одной генерации
    (задача: история изменений метаданных) — список записей вида
    "поле: было -> стало", самые новые сверху (порядок уже определён
    GalleryManager.get_metadata_history / GenerationRepository.
    get_metadata_history — этот диалог его не переупорядочивает)."""

    def __init__(self, history: list[dict[str, Any]], parent: QWidget | None = None):
        super().__init__(parent)

        self.setWindowTitle(self.tr("Metadata history"))
        self.resize(480, 360)

        layout = QVBoxLayout(self)

        self.list_widget = QListWidget()

        for entry in history:
            self.list_widget.addItem(self._format_entry(entry))

        layout.addWidget(self.list_widget)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        # Close — единственная кнопка, но QDialogButtonBox эмитит
        # rejected() для RejectRole (к которому относится Close) —
        # accepted() тут не используется, но подключаем на случай
        # смены стиля/платформы кнопок
        layout.addWidget(buttons)

    def _format_entry(self, entry: dict[str, Any]) -> str:
        """Не @staticmethod — используем self.tr() для переводимых
        частей строки (см. задачу: полный аудит строк UI под
        self.tr()).

        entry["field"] — это имя колонки в БД (см.
        GenerationRepository._record_metadata_history), а не то, что
        должен видеть пользователь, поэтому отображаем через
        _FIELD_LABELS, а не как есть."""

        changed_at = entry.get("changed_at")
        when = (
            datetime.datetime.fromtimestamp(changed_at).strftime("%Y-%m-%d %H:%M:%S")
            if changed_at is not None
            else "?"
        )

        field = entry.get("field")
        field_label = self._field_labels().get(field, field)

        return self.tr("[{}] {}: {!r} -> {!r}").format(
            when, field_label, entry.get("old_value"), entry.get("new_value")
        )

    def _field_labels(self) -> dict[str, str]:

        return {
            "model": self.tr("Model"),
            "sampler": self.tr("Sampler"),
            "cfg": self.tr("CFG"),
            "steps": self.tr("Steps"),
            "positive": self.tr("Positive prompt"),
            "negative": self.tr("Negative prompt"),
        }
