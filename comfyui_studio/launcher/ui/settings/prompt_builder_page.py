"""Раздел "Prompt Builder" единого дерева настроек: папка расширения,
папка с файлами LoRA, максимальное число резервных копий.

До этой правки страница была пустой заготовкой — у Prompt Builder не
было собственных настроек за пределами общих темы/языка. Теперь есть:
редактор лишился верхней панели меню "Файл"/"Справка" (см.
comfyui_studio/prompt_builder/main.py, докстринг _build_toolbar) —
выбор папки расширения и папки LoRA, которые раньше делались изнутри
самого редактора по одному разу через это меню, переехали сюда;
Prompt Builder сам эти папки больше не меняет, только читает уже
настроенное отсюда (см. pb_settings.py).

Пишем НАПРЯМУЮ в QSettings("PromptConfigEditor", "PromptConfigEditor")
Prompt Builder (см. pb_settings.py/lora_combo.py) — как и Database в
promptvault_page.py, это собственное хранилище настроек другого
инструмента, а не cfg/config.json лаунчера, поэтому в общий
debounce-автосейв AppSettingsDialog эта страница не включена: пишет
сразу же на каждое изменение (сами эти QSettings-вызовы дешёвые).

Часть этапа 4 дорожной карты рефакторинга ("Единое дерево настроек").
"""

from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from comfyui_studio.prompt_builder.lora_combo import get_lora_folder, set_lora_folder
from comfyui_studio.prompt_builder.pb_settings import (
    DEFAULT_BACKUP_KEEP,
    get_backup_keep,
    get_extension_folder,
    set_backup_keep,
    set_extension_folder,
)


class PromptBuilderSettingsPage(QWidget):
    def __init__(self, loc=None, parent=None):
        super().__init__(parent)
        self.loc = loc

        root = QVBoxLayout(self)

        # -- Папки ----------------------------------------------------
        folders_box = QGroupBox(self._tr("Папки"))
        self.folders_box = folders_box
        folders_form = QFormLayout(folders_box)

        ext_row = QHBoxLayout()
        self.extension_folder_edit = QLineEdit(get_extension_folder())
        self.extension_folder_edit.editingFinished.connect(self._on_extension_folder_edited)
        self.extension_folder_browse_btn = QPushButton(self._tr("Обзор..."))
        self.extension_folder_browse_btn.clicked.connect(self._browse_extension_folder)
        ext_row.addWidget(self.extension_folder_edit)
        ext_row.addWidget(self.extension_folder_browse_btn)
        self.extension_folder_row_label = QLabel(self._tr("Папка расширения:"))
        folders_form.addRow(self.extension_folder_row_label, ext_row)

        self.extension_folder_hint = QLabel(
            self._tr(
                "Папка с characters.json и prompt_builder_config.json — "
                "Prompt Builder подхватывает файлы из неё автоматически "
                "при следующем открытии."
            )
        )
        self.extension_folder_hint.setWordWrap(True)
        self.extension_folder_hint.setObjectName("mutedLabel")
        folders_form.addRow(self.extension_folder_hint)

        lora_row = QHBoxLayout()
        self.lora_folder_edit = QLineEdit(get_lora_folder())
        self.lora_folder_edit.editingFinished.connect(self._on_lora_folder_edited)
        self.lora_folder_browse_btn = QPushButton(self._tr("Обзор..."))
        self.lora_folder_browse_btn.clicked.connect(self._browse_lora_folder)
        lora_row.addWidget(self.lora_folder_edit)
        lora_row.addWidget(self.lora_folder_browse_btn)
        self.lora_folder_row_label = QLabel(self._tr("Папка с файлами LoRA:"))
        folders_form.addRow(self.lora_folder_row_label, lora_row)

        self.lora_folder_hint = QLabel(
            self._tr(
                "Список LoRA в редакторе пересканирует эту папку заново "
                "при каждом открытии выпадающего списка — изменение здесь "
                "применяется сразу же, без перезапуска Prompt Builder."
            )
        )
        self.lora_folder_hint.setWordWrap(True)
        self.lora_folder_hint.setObjectName("mutedLabel")
        folders_form.addRow(self.lora_folder_hint)

        root.addWidget(folders_box)

        # -- Резервные копии --------------------------------------------
        backup_box = QGroupBox(self._tr("Резервные копии"))
        self.backup_box = backup_box
        backup_form = QFormLayout(backup_box)

        self.backup_keep_spin = QSpinBox()
        self.backup_keep_spin.setRange(0, 200)
        self.backup_keep_spin.setValue(get_backup_keep())
        self.backup_keep_spin.valueChanged.connect(self._on_backup_keep_changed)
        self.backup_keep_row_label = QLabel(self._tr("Хранить резервных копий (на файл):"))
        backup_form.addRow(self.backup_keep_row_label, self.backup_keep_spin)

        self.backup_keep_hint = QLabel(
            self._tr(
                "Резервная копия (*.bak-ГГГГММДД-ЧЧММСС) создаётся при "
                "каждом сохранении; лишние сверх этого числа удаляются "
                "сразу же (самые старые — первыми). 0 — не хранить "
                "резервные копии вовсе."
            )
        )
        self.backup_keep_hint.setWordWrap(True)
        self.backup_keep_hint.setObjectName("mutedLabel")
        backup_form.addRow(self.backup_keep_hint)

        root.addWidget(backup_box)
        root.addStretch(1)

    # -- папки --------------------------------------------------------

    def _browse_extension_folder(self):
        start = self.extension_folder_edit.text().strip() or ""
        chosen = QFileDialog.getExistingDirectory(
            self, self._tr("Выберите папку расширения"), start
        )
        if chosen:
            self.extension_folder_edit.setText(chosen)
            self._on_extension_folder_edited()

    def _on_extension_folder_edited(self):
        set_extension_folder(self.extension_folder_edit.text().strip())

    def _browse_lora_folder(self):
        start = self.lora_folder_edit.text().strip() or ""
        chosen = QFileDialog.getExistingDirectory(
            self, self._tr("Выберите папку с файлами LoRA"), start
        )
        if chosen:
            self.lora_folder_edit.setText(chosen)
            self._on_lora_folder_edited()

    def _on_lora_folder_edited(self):
        set_lora_folder(self.lora_folder_edit.text().strip())

    # -- бэкапы -------------------------------------------------------

    def _on_backup_keep_changed(self, value):
        set_backup_keep(value)

    # -- прочее -----------------------------------------------------

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def retranslate_ui(self):
        self.folders_box.setTitle(self._tr("Папки"))
        self.extension_folder_row_label.setText(self._tr("Папка расширения:"))
        self.extension_folder_browse_btn.setText(self._tr("Обзор..."))
        self.extension_folder_hint.setText(
            self._tr(
                "Папка с characters.json и prompt_builder_config.json — "
                "Prompt Builder подхватывает файлы из неё автоматически "
                "при следующем открытии."
            )
        )
        self.lora_folder_row_label.setText(self._tr("Папка с файлами LoRA:"))
        self.lora_folder_browse_btn.setText(self._tr("Обзор..."))
        self.lora_folder_hint.setText(
            self._tr(
                "Список LoRA в редакторе пересканирует эту папку заново "
                "при каждом открытии выпадающего списка — изменение здесь "
                "применяется сразу же, без перезапуска Prompt Builder."
            )
        )
        self.backup_box.setTitle(self._tr("Резервные копии"))
        self.backup_keep_row_label.setText(self._tr("Хранить резервных копий (на файл):"))
        self.backup_keep_hint.setText(
            self._tr(
                "Резервная копия (*.bak-ГГГГММДД-ЧЧММСС) создаётся при "
                "каждом сохранении; лишние сверх этого числа удаляются "
                "сразу же (самые старые — первыми). 0 — не хранить "
                "резервные копии вовсе."
            )
        )
