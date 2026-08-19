"""Раздел "ComfyUI" единого дерева настроек: папка установки, порт,
скрипт запуска, аргументы командной строки, переменные окружения.

Installation/Port/Startup script/Arguments -- перенос содержимого
старого LaunchArgsDialog (см. ui/settings_page.py до этапа 4) сюда, в
виде обычных секций страницы, а не отдельного модального диалога --
запуска отдельным окном они больше не требуют, раз сама Settings уже
дерево разделов. Логика самих аргументов (LAUNCH_ARG_DEFS,
build_extra_launch_args) не менялась, как и исходные (русские) строки
их описаний (d["desc_ru"]) -- переводы для них уже были в TRANSLATIONS.

Environment -- НОВОЕ (этап 4): переменные окружения процесса ComfyUI,
см. cfg["env_vars"] и ComfyProcess.start() (core/comfy_process.py).

Все строки на этой странице -- исходные на русском (см. пояснение в
general_page.py про TRANSLATIONS/loc.tr()). Где формулировка уже
существовала в TRANSLATIONS (перенесённые из LaunchArgsDialog поля —
папка/скрипт/порт/чекбокс браузера/тема ComfyUI) используются ТЕ ЖЕ
исходные строки, чтобы не плодить два перевода одного смысла.

Часть этапа 4 дорожной карты рефакторинга ("Единое дерево настроек").
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.config import LAUNCH_ARG_DEFS, find_run_scripts, guess_default_script, validate_portable_root


class ComfyUISettingsPage(QWidget):
    # эмитится при ЛЮБОМ изменении любого поля этой страницы --
    # AppSettingsDialog подписывается и планирует автосохранение
    # (тот же debounce-паттерн, что был в старом SettingsPage._schedule_autosave)
    changed = Signal()

    def __init__(self, cfg: dict, loc=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.loc = loc
        self._loading_fields = False
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(scroll)
        container = QWidget()
        scroll.setWidget(container)
        root = QVBoxLayout(container)

        # -- Installation -------------------------------------------------
        install_box = QGroupBox(self._tr("Установка"))
        self.install_box = install_box
        install_form = QFormLayout(install_box)

        path_row = QHBoxLayout()
        self.path_edit = QLineEdit(cfg.get("root_path", ""))
        self.path_edit.editingFinished.connect(self._refresh_scripts)
        self.path_edit.textChanged.connect(self._on_field_changed)
        self.browse_btn = QPushButton(self._tr("Обзор..."))
        self.browse_btn.clicked.connect(self._browse)
        path_row.addWidget(self.path_edit)
        path_row.addWidget(self.browse_btn)
        self.path_row_label = QLabel(self._tr("Папка ComfyUI_windows_portable:"))
        install_form.addRow(self.path_row_label, path_row)

        self.script_combo = QComboBox()
        self.script_combo.currentIndexChanged.connect(self._on_field_changed)
        self.script_row_label = QLabel(self._tr("Скрипт запуска:"))
        install_form.addRow(self.script_row_label, self.script_combo)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(int(cfg.get("port", 8188)))
        self.port_spin.valueChanged.connect(self._on_field_changed)
        self.port_row_label = QLabel(self._tr("Порт:"))
        install_form.addRow(self.port_row_label, self.port_spin)

        self.disable_auto_launch_check = QCheckBox(
            self._tr("Не давать ComfyUI открывать системный браузер при старте")
        )
        self.disable_auto_launch_check.setChecked(cfg.get("disable_auto_launch", True))
        self.disable_auto_launch_check.stateChanged.connect(self._on_field_changed)
        install_form.addRow(self.disable_auto_launch_check)

        self.sync_comfy_theme_check = QCheckBox(
            self._tr(
                "Синхронизировать тему ComfyUI с темой приложения "
                "(ближайший встроенный вариант)"
            )
        )
        self.sync_comfy_theme_check.setToolTip(
            self._tr(
                "Синхронизирует встроенную палитру ComfyUI "
                "(Comfy.ColorPalette) с темой приложения — вживую, пока "
                "ComfyUI уже открыт, и при следующем запуске. Не идентично "
                "Qt-теме — у ComfyUI своя цветовая система узлов."
            )
        )
        self.sync_comfy_theme_check.setChecked(cfg.get("sync_comfy_theme", False))
        self.sync_comfy_theme_check.stateChanged.connect(self._on_field_changed)
        install_form.addRow(self.sync_comfy_theme_check)

        root.addWidget(install_box)

        # -- Arguments (бывший LaunchArgsDialog) ---------------------------
        args_box = QGroupBox(self._tr("Аргументы запуска ComfyUI"))
        self.args_box = args_box
        args_outer = QVBoxLayout(args_box)

        saved_args = cfg.get("launch_args", {})
        self.arg_widgets = {}
        for d in LAUNCH_ARG_DEFS:
            row_box = QGroupBox()
            row_layout = QVBoxLayout(row_box)
            check = QCheckBox(d["flag"])
            saved_entry = saved_args.get(d["id"], {})
            check.setChecked(bool(saved_entry.get("enabled")))
            check.stateChanged.connect(self._on_field_changed)
            row_layout.addWidget(check)

            desc = QLabel(self._tr(d["desc_ru"]))
            desc.setObjectName("mutedLabel")
            desc.setWordWrap(True)
            row_layout.addWidget(desc)

            value_edit = None
            if d.get("takes_value"):
                value_edit = QLineEdit(str(saved_entry.get("value", "")))
                value_edit.setPlaceholderText(d.get("placeholder", ""))
                value_edit.textChanged.connect(self._on_field_changed)
                row_layout.addWidget(value_edit)

            args_outer.addWidget(row_box)
            self.arg_widgets[d["id"]] = {
                "check": check, "desc": desc, "value": value_edit, "box": row_box,
            }

        root.addWidget(args_box)

        # -- Environment (НОВОЕ, этап 4) -----------------------------------
        env_box = QGroupBox(self._tr("Переменные окружения"))
        self.env_box = env_box
        env_layout = QVBoxLayout(env_box)

        self.env_hint = QLabel(
            self._tr(
                "Дополнительные переменные окружения только для процесса "
                "ComfyUI (добавляются поверх обычного окружения — "
                "например, HF_HOME, чтобы перенести кэш HuggingFace, или "
                "CUDA_VISIBLE_DEVICES, чтобы выбрать видеокарту)."
            )
        )
        self.env_hint.setWordWrap(True)
        self.env_hint.setObjectName("mutedLabel")
        env_layout.addWidget(self.env_hint)

        self.env_table = QTableWidget(0, 2)
        self.env_table.setHorizontalHeaderLabels(
            [self._tr("Имя"), self._tr("Значение")]
        )
        self.env_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.env_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.env_table.setMinimumHeight(120)
        for name, value in sorted(cfg.get("env_vars", {}).items()):
            self._append_env_row(name, value)
        self.env_table.itemChanged.connect(self._on_field_changed)
        env_layout.addWidget(self.env_table)

        env_btn_row = QHBoxLayout()
        self.env_add_btn = QPushButton(self._tr("Добавить переменную"))
        self.env_add_btn.clicked.connect(lambda: self._append_env_row("", ""))
        env_btn_row.addWidget(self.env_add_btn)
        self.env_remove_btn = QPushButton(self._tr("Удалить выбранные"))
        self.env_remove_btn.clicked.connect(self._remove_selected_env_rows)
        env_btn_row.addWidget(self.env_remove_btn)
        env_btn_row.addStretch(1)
        env_layout.addLayout(env_btn_row)

        root.addWidget(env_box)
        root.addStretch(1)

        self._refresh_scripts()

    # -- Installation / scripts -----------------------------------------

    def _browse(self):
        chosen = QFileDialog.getExistingDirectory(
            self, self._tr("Выберите папку ComfyUI_windows_portable")
        )
        if chosen:
            self.path_edit.setText(chosen)
            self._refresh_scripts()

    def _refresh_scripts(self):
        scripts = find_run_scripts(self.path_edit.text().strip())
        self._loading_fields = True
        current = self.script_combo.currentText()
        self.script_combo.clear()
        self.script_combo.addItems(scripts)
        if current in scripts:
            self.script_combo.setCurrentText(current)
        elif self.cfg.get("script") in scripts:
            self.script_combo.setCurrentText(self.cfg["script"])
        else:
            self.script_combo.setCurrentText(guess_default_script(scripts))
        self._loading_fields = False
        self.changed.emit()

    def validate(self):
        """Возвращает (ok, message) -- та же проверка, что раньше делал
        SettingsPage._on_launch() перед запуском."""
        return validate_portable_root(self.path_edit.text().strip())

    # -- Environment table ------------------------------------------------

    def _append_env_row(self, name, value):
        row = self.env_table.rowCount()
        self.env_table.insertRow(row)
        self.env_table.setItem(row, 0, QTableWidgetItem(name))
        self.env_table.setItem(row, 1, QTableWidgetItem(value))

    def _remove_selected_env_rows(self):
        rows = sorted({idx.row() for idx in self.env_table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.env_table.removeRow(row)
        if rows:
            self.changed.emit()

    def collect_env_vars(self) -> dict:
        result = {}
        for row in range(self.env_table.rowCount()):
            name_item = self.env_table.item(row, 0)
            value_item = self.env_table.item(row, 1)
            name = (name_item.text().strip() if name_item else "")
            if not name:
                continue
            result[name] = value_item.text() if value_item else ""
        return result

    # -- сбор состояния для автосохранения --------------------------------

    def _on_field_changed(self, *_args):
        """Общий приёмник для сигналов вроде textChanged(str)/
        valueChanged(int)/stateChanged(int)/itemChanged(QTableWidgetItem)
        (см. подключения в __init__ выше) -- сам сигнал `changed` этой
        страницы объявлен как Signal() (без аргументов, см. класс выше),
        а AppSettingsDialog._schedule_autosave всё равно не использует
        значение аргумента, ему важен только сам факт изменения.

        Раньше эти сигналы были подключены НАПРЯМУЮ к self.changed.emit
        -- PySide в таком случае вызывает emit() с тем же количеством
        аргументов, что прислал источник, а Signal() принимает ровно 0 --
        отсюда `TypeError: changed() only accepts 0 argument(s), 1
        given!`, печатавшийся (но не приводивший к падению — исключения
        в слотах Qt перехватывает и печатает сам, не пробрасывая дальше)
        при каждом изменении любого поля этой страницы, включая
        построение самой страницы (см. _refresh_scripts() ниже, которая
        трогает script_combo уже в конце __init__)."""
        self.changed.emit()

    def collect_launch_args(self):
        result = {}
        for arg_id, widgets in self.arg_widgets.items():
            value_edit = widgets["value"]
            result[arg_id] = {
                "enabled": widgets["check"].isChecked(),
                "value": value_edit.text().strip() if value_edit is not None else "",
            }
        return result

    def collect(self) -> dict:
        """Всё, что нужно записать в cfg по данным этой страницы --
        AppSettingsDialog подмешивает это в общий cfg при автосохранении."""
        return {
            "root_path": self.path_edit.text().strip(),
            "script": self.script_combo.currentText(),
            "port": self.port_spin.value(),
            "disable_auto_launch": self.disable_auto_launch_check.isChecked(),
            "sync_comfy_theme": self.sync_comfy_theme_check.isChecked(),
            "launch_args": self.collect_launch_args(),
            "env_vars": self.collect_env_vars(),
        }

    # -- блокировка полей, пока ComfyUI уже запущен ------------------------

    def set_editable(self, editable: bool) -> None:
        for w in (self.path_edit, self.browse_btn, self.script_combo, self.port_spin,
                  self.disable_auto_launch_check, self.sync_comfy_theme_check):
            w.setEnabled(editable)
        for widgets in self.arg_widgets.values():
            widgets["check"].setEnabled(editable)
            if widgets["value"] is not None:
                widgets["value"].setEnabled(editable)
        self.env_table.setEnabled(editable)
        self.env_add_btn.setEnabled(editable)
        self.env_remove_btn.setEnabled(editable)

    # -- прочее -----------------------------------------------------

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def retranslate_ui(self):
        self.install_box.setTitle(self._tr("Установка"))
        self.path_row_label.setText(self._tr("Папка ComfyUI_windows_portable:"))
        self.browse_btn.setText(self._tr("Обзор..."))
        self.script_row_label.setText(self._tr("Скрипт запуска:"))
        self.port_row_label.setText(self._tr("Порт:"))
        self.disable_auto_launch_check.setText(
            self._tr("Не давать ComfyUI открывать системный браузер при старте")
        )
        self.sync_comfy_theme_check.setText(
            self._tr(
                "Синхронизировать тему ComfyUI с темой приложения "
                "(ближайший встроенный вариант)"
            )
        )
        self.sync_comfy_theme_check.setToolTip(
            self._tr(
                "Синхронизирует встроенную палитру ComfyUI "
                "(Comfy.ColorPalette) с темой приложения — вживую, пока "
                "ComfyUI уже открыт, и при следующем запуске. Не идентично "
                "Qt-теме — у ComfyUI своя цветовая система узлов."
            )
        )
        self.args_box.setTitle(self._tr("Аргументы запуска ComfyUI"))
        for d in LAUNCH_ARG_DEFS:
            self.arg_widgets[d["id"]]["desc"].setText(self._tr(d["desc_ru"]))
            value_edit = self.arg_widgets[d["id"]]["value"]
            if value_edit is not None:
                value_edit.setPlaceholderText(d.get("placeholder", ""))
        self.env_box.setTitle(self._tr("Переменные окружения"))
        self.env_hint.setText(
            self._tr(
                "Дополнительные переменные окружения только для процесса "
                "ComfyUI (добавляются поверх обычного окружения — "
                "например, HF_HOME, чтобы перенести кэш HuggingFace, или "
                "CUDA_VISIBLE_DEVICES, чтобы выбрать видеокарту)."
            )
        )
        self.env_table.setHorizontalHeaderLabels([self._tr("Имя"), self._tr("Значение")])
        self.env_add_btn.setText(self._tr("Добавить переменную"))
        self.env_remove_btn.setText(self._tr("Удалить выбранные"))
