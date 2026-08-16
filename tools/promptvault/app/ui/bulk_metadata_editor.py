from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class BulkMetadataEditor(QDialog):
    """Диалог массового редактирования метаданных (задача: массовое
    редактирование метаданных) — применяет ОДНО и то же значение поля
    сразу ко всем переданным id.

    В отличие от MetadataEditor (одна генерация, все поля всегда
    редактируются) каждое поле здесь сопровождается чекбоксом
    "применить": по умолчанию все чекбоксы сняты, а сами поля
    заблокированы — так пользователь явно указывает, какие поля он
    действительно хочет перезаписать у всех выделенных генераций, а
    не рискует случайно затереть, например, CFG нулём по умолчанию у
    сотни генераций, тронув только Model.

    Позитивный/негативный промпт сознательно НЕ включены — перезапись
    промпта одинаковым текстом сразу у нескольких (обычно разных по
    содержимому) генераций почти никогда не осмысленна, в отличие от
    model/sampler/cfg/steps, где массовая унификация значений —
    обычный сценарий (например, проставить одну и ту же модель пачке
    генераций после того, как выяснилось, что она была неверно
    распознана при импорте).

    Как и MetadataEditor — сам ничего не сохраняет, только собирает
    update_dict из отмеченных полей и эмитит saved(generation_ids,
    update_dict). Сохранение делает GalleryManager.
    update_generations_metadata.
    """

    saved = Signal(list, dict)

    def __init__(
        self,
        generation_ids: list[int],
        known_models: set[str] | None = None,
        known_samplers: set[str] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)

        self.generation_ids = list(generation_ids)

        self.setWindowTitle(
            self.tr("Bulk edit metadata — {} generations").format(len(self.generation_ids))
        )
        self.resize(420, 260)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(
            self.tr(
                "Applies to {} selected generations. "
                "Only checked fields are changed."
            ).format(len(self.generation_ids))
        ))

        form = QFormLayout()

        self.model_enabled = QCheckBox(self.tr("Model"))
        self.model_box = QComboBox()
        self.model_box.setEditable(True)
        self.model_box.addItems(sorted(known_models or []))
        self.model_box.setEnabled(False)
        self.model_enabled.toggled.connect(self.model_box.setEnabled)
        form.addRow(self.model_enabled, self.model_box)

        self.sampler_enabled = QCheckBox(self.tr("Sampler"))
        self.sampler_box = QComboBox()
        self.sampler_box.setEditable(True)
        self.sampler_box.addItems(sorted(known_samplers or []))
        self.sampler_box.setEnabled(False)
        self.sampler_enabled.toggled.connect(self.sampler_box.setEnabled)
        form.addRow(self.sampler_enabled, self.sampler_box)

        self.cfg_enabled = QCheckBox(self.tr("CFG"))
        self.cfg_box = QDoubleSpinBox()
        self.cfg_box.setRange(0.0, 100.0)
        self.cfg_box.setSingleStep(0.5)
        self.cfg_box.setEnabled(False)
        self.cfg_enabled.toggled.connect(self.cfg_box.setEnabled)
        form.addRow(self.cfg_enabled, self.cfg_box)

        self.steps_enabled = QCheckBox(self.tr("Steps"))
        self.steps_box = QSpinBox()
        self.steps_box.setRange(0, 1000)
        self.steps_box.setEnabled(False)
        self.steps_enabled.toggled.connect(self.steps_box.setEnabled)
        form.addRow(self.steps_enabled, self.steps_box)

        layout.addLayout(form)

        layout.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._on_save_clicked)
        buttons.rejected.connect(self.reject)

        layout.addWidget(buttons)

    def _on_save_clicked(self) -> None:
        """Собирает update_dict только из полей с отмеченным
        чекбоксом и эмитит saved() — как и MetadataEditor, диалог сам
        себя не закрывает (см. тот же docstring там); закрытие —
        ответственность вызывающего кода после подтверждённого успеха."""

        update_dict: dict[str, object] = {}

        if self.model_enabled.isChecked():
            update_dict["model"] = self.model_box.currentText().strip()

        if self.sampler_enabled.isChecked():
            update_dict["sampler"] = self.sampler_box.currentText().strip()

        if self.cfg_enabled.isChecked():
            update_dict["cfg"] = self.cfg_box.value()

        if self.steps_enabled.isChecked():
            update_dict["steps"] = self.steps_box.value()

        if not update_dict:
            return

        self.saved.emit(self.generation_ids, update_dict)
