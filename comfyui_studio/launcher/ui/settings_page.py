"""
Экран настроек: параметры запуска ComfyUI, аргументы командной строки,
запуск/остановка сервера, запуск остальных инструментов комплекта.

Вынесено из comfyui_launcher.py (этап 1 дорожной карты).
"""

import gc

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from comfyui_studio.themes.theme_manager import ThemeManager

from ..core.comfy_process import (
    EXTERNAL_APPS,
    ExternalApp,
    launch_external_app,
    resolve_external_launch,
)
from ..core.config import LAUNCH_ARG_DEFS, find_run_scripts, guess_default_script, save_config, validate_portable_root
from ..core.logging_setup import log
from ..integration.tool_registry import IN_PROCESS_WINDOW_FACTORIES
from .widgets.log_panel import LogPanel
from .widgets.resource_bar import ResourceBar


class LaunchArgsDialog(QDialog):
    def __init__(self, cfg, loc=None, parent=None):
        super().__init__(parent)
        self.loc = loc
        self.setWindowTitle(self._tr("Аргументы запуска ComfyUI"))
        self.setMinimumWidth(560)

        outer = QVBoxLayout(self)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self.script_combo = QComboBox()
        self.script_row_label = QLabel(self._tr("Скрипт запуска:"))
        form.addRow(self.script_row_label, self.script_combo)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(int(cfg.get("port", 8188)))
        self.port_row_label = QLabel(self._tr("Порт:"))
        form.addRow(self.port_row_label, self.port_spin)

        self.disable_auto_launch_check = QCheckBox(
            self._tr("Не давать ComfyUI открывать системный браузер при старте")
        )
        self.disable_auto_launch_check.setChecked(cfg.get("disable_auto_launch", True))
        form.addRow(self.disable_auto_launch_check)

        outer.addLayout(form)

        self.args_heading = self._heading(self._tr("Дополнительные аргументы командной строки"))
        outer.addWidget(self.args_heading)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        args_container = QWidget()
        args_layout = QVBoxLayout(args_container)
        scroll.setWidget(args_container)
        outer.addWidget(scroll, 1)

        saved_args = cfg.get("launch_args", {})
        # id -> {"check": QCheckBox, "desc": QLabel, "value": QLineEdit|None}
        self.arg_widgets = {}
        for d in LAUNCH_ARG_DEFS:
            row_box = QGroupBox()
            row_layout = QVBoxLayout(row_box)
            check = QCheckBox(d["flag"])
            saved_entry = saved_args.get(d["id"], {})
            check.setChecked(bool(saved_entry.get("enabled")))
            row_layout.addWidget(check)

            desc = QLabel(self._tr(d["desc_ru"]))
            desc.setObjectName("mutedLabel")
            desc.setWordWrap(True)
            row_layout.addWidget(desc)

            value_edit = None
            if d.get("takes_value"):
                value_edit = QLineEdit(str(saved_entry.get("value", "")))
                value_edit.setPlaceholderText(d.get("placeholder", ""))
                row_layout.addWidget(value_edit)

            args_layout.addWidget(row_box)
            self.arg_widgets[d["id"]] = {"check": check, "desc": desc, "value": value_edit,
                                          "box": row_box}

        args_layout.addStretch(1)

        self.close_btn = QPushButton(self._tr("Закрыть"))
        self.close_btn.clicked.connect(self.close)
        outer.addWidget(self.close_btn)

    def _heading(self, text):
        lbl = QLabel(text)
        lbl.setObjectName("headingLabel")
        return lbl

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def collect_launch_args(self):
        """Возвращает cfg["launch_args"] — {id: {"enabled": bool, "value": str}}."""
        result = {}
        for arg_id, widgets in self.arg_widgets.items():
            value_edit = widgets["value"]
            result[arg_id] = {
                "enabled": widgets["check"].isChecked(),
                "value": value_edit.text().strip() if value_edit is not None else "",
            }
        return result

    def set_extra_widgets_enabled(self, enabled):
        """Блокирует именно доп.-аргументные чекбоксы/поля (скрипт/порт/
        браузер блокируются отдельно, там же, где раньше — см.
        SettingsPage.set_server_running/_set_launch_controls_enabled)."""
        for widgets in self.arg_widgets.values():
            widgets["check"].setEnabled(enabled)
            if widgets["value"] is not None:
                widgets["value"].setEnabled(enabled)

    def retranslate_ui(self):
        self.setWindowTitle(self._tr("Аргументы запуска ComfyUI"))
        self.script_row_label.setText(self._tr("Скрипт запуска:"))
        self.port_row_label.setText(self._tr("Порт:"))
        self.disable_auto_launch_check.setText(
            self._tr("Не давать ComfyUI открывать системный браузер при старте")
        )
        self.args_heading.setText(self._tr("Дополнительные аргументы командной строки"))
        self.close_btn.setText(self._tr("Закрыть"))
        for d in LAUNCH_ARG_DEFS:
            self.arg_widgets[d["id"]]["desc"].setText(self._tr(d["desc_ru"]))
            value_edit = self.arg_widgets[d["id"]]["value"]
            if value_edit is not None:
                value_edit.setPlaceholderText(d.get("placeholder", ""))


# --------------------------------------------------------------------------
# Страница настроек
# --------------------------------------------------------------------------



class SettingsPage(QWidget):
    launch_requested = Signal(dict)
    open_running_requested = Signal()
    stop_requested = Signal()
    cancel_requested = Signal()
    language_changed = Signal(str)

    AUTOSAVE_DEBOUNCE_MS = 400

    def __init__(self, cfg, theme_manager: ThemeManager, loc=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.theme_manager = theme_manager
        self.loc = loc
        self._loading_fields = False
        # Держит живые ссылки на окна остальных инструментов комплекта,
        # открытые В ЭТОМ ЖЕ процессе (см. IN_PROCESS_WINDOW_FACTORIES) --
        # без этого объект окна был бы собран сборщиком мусора Python сразу
        # после выхода из _launch_external() и окно бы тут же закрылось.
        #
        # ВАЖНО: раньше запись из этого словаря никогда не удалялась --
        # окно (а с ним и всё, что оно загрузило в память, для PromptVault
        # это модели torch/transformers) жило до самого закрытия ВСЕГО
        # приложения, даже если пользователь закрывал только окно
        # инструмента крестиком. См. _open_in_process_window() ниже --
        # там теперь окно реально уничтожается по закрытию и запись
        # удаляется из кэша, а не просто "скрывается" навсегда.
        self._child_windows = {}

        root = QVBoxLayout(self)

        self.resource_bar = ResourceBar(loc=self.loc)
        root.addWidget(self.resource_bar)

        # Полоса "ComfyUI уже запущен" — видна только пока процесс жив,
        # и именно тут разведены по смыслу кнопки "Настройки" и "Стоп":
        # эта страница сама по себе больше не останавливает сервер.
        self.running_bar = QWidget()
        running_row = QHBoxLayout(self.running_bar)
        running_row.setContentsMargins(0, 0, 0, 0)
        self.running_label = QLabel(self._tr("ComfyUI уже запущен"))
        self.running_label.setStyleSheet("color: #6fbf73; font-weight: bold;")
        running_row.addWidget(self.running_label)
        running_row.addStretch(1)
        self.open_running_btn = QPushButton(self._tr("Открыть ComfyUI"))
        self.open_running_btn.clicked.connect(self.open_running_requested.emit)
        running_row.addWidget(self.open_running_btn)
        self.stop_running_btn = QPushButton(self._tr("Остановить"))
        self.stop_running_btn.clicked.connect(self.stop_requested.emit)
        running_row.addWidget(self.stop_running_btn)
        root.addWidget(self.running_bar)
        self.running_bar.setVisible(False)

        form = QFormLayout()
        form.setSpacing(10)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(theme_manager.available_themes())
        self.theme_combo.setCurrentText(theme_manager.current_theme())
        self.theme_combo.currentTextChanged.connect(self._on_theme_changed)
        theme_manager.theme_changed_externally.connect(self._on_theme_changed_externally)
        self.theme_row_label = QLabel(self._tr("Тема оформления:"))
        form.addRow(self.theme_row_label, self.theme_combo)

        # Язык — общий на весь комплект (см. i18n.py / shared_language.py):
        # переключатель есть только здесь, PromptConfigEditor и PromptVault
        # только применяют выбор, сделанный тут.
        self.language_combo = QComboBox()
        if self.loc is not None:
            self.language_combo.addItems(self.loc.available_languages())
            self._sync_language_combo_display()
            self.language_combo.currentTextChanged.connect(self._on_language_changed)
            self.loc.language_changed_externally.connect(self._on_language_changed_externally)
        self.language_row_label = QLabel(self._tr("Язык интерфейса:"))
        form.addRow(self.language_row_label, self.language_combo)

        self.sync_comfy_theme_check = QCheckBox(
            self._tr(
                "Синхронизировать тему ComfyUI с темой приложения (ближайший встроенный вариант)"
            )
        )
        self.sync_comfy_theme_check.setToolTip(
            self._tr(
                "Синхронизирует встроенную палитру ComfyUI (Comfy.ColorPalette) с "
                "темой приложения — вживую, пока ComfyUI уже открыт, и при "
                "следующем запуске. Не идентично Qt-теме — у ComfyUI своя "
                "цветовая система узлов."
            )
        )
        self.sync_comfy_theme_check.setChecked(cfg.get("sync_comfy_theme", False))
        self.sync_comfy_theme_check.stateChanged.connect(self._schedule_autosave)
        form.addRow(self.sync_comfy_theme_check)

        path_row = QHBoxLayout()
        self.path_edit = QLineEdit(cfg.get("root_path", ""))
        self.path_edit.editingFinished.connect(self._refresh_scripts)
        self.path_edit.textChanged.connect(self._schedule_autosave)
        self.browse_btn = QPushButton(self._tr("Обзор..."))
        self.browse_btn.clicked.connect(self._browse)
        path_row.addWidget(self.path_edit)
        path_row.addWidget(self.browse_btn)
        self.path_row_label = QLabel(self._tr("Папка ComfyUI_windows_portable:"))
        form.addRow(self.path_row_label, path_row)

        self.launch_args_dialog = LaunchArgsDialog(cfg, loc=self.loc, parent=self)
        # Скрипт запуска/порт/чекбокс браузера физически живут в диалоге
        # (LaunchArgsDialog), но остальной код этой страницы (автосохранение,
        # запуск, включение/выключение полей во время работы сервера)
        # по-прежнему обращается к ним как self.script_combo/self.port_spin/
        # self.disable_auto_launch_check — так с этим кодом ничего больше
        # менять не пришлось.
        self.script_combo = self.launch_args_dialog.script_combo
        self.port_spin = self.launch_args_dialog.port_spin
        self.disable_auto_launch_check = self.launch_args_dialog.disable_auto_launch_check
        self.script_combo.currentIndexChanged.connect(self._schedule_autosave)
        self.port_spin.valueChanged.connect(self._schedule_autosave)
        self.disable_auto_launch_check.stateChanged.connect(self._schedule_autosave)
        for widgets in self.launch_args_dialog.arg_widgets.values():
            widgets["check"].stateChanged.connect(self._schedule_autosave)
            if widgets["value"] is not None:
                widgets["value"].textChanged.connect(self._schedule_autosave)

        self.launch_args_btn = QPushButton(self._tr("Аргументы запуска ComfyUI..."))
        self.launch_args_btn.clicked.connect(self.launch_args_dialog.exec)
        form.addRow(self.launch_args_btn)

        root.addLayout(form)

        # -- Другие инструменты комплекта: запускаются как отдельные,
        # независимые процессы (см. ExternalApp/launch_external_app выше).
        # Путь к ним не настраивается — оба поставляются в одном архиве
        # с лаунчером, в фиксированной подпапке tools/.
        self.tools_box = QGroupBox(self._tr("Другие инструменты"))
        tools_layout = QVBoxLayout(self.tools_box)
        self.external_status_labels = {}
        self.external_launch_btns = {}
        for app in EXTERNAL_APPS:
            row = QHBoxLayout()
            row.addWidget(QLabel(app.label))
            row.addStretch(1)

            launch_btn = QPushButton(self._tr("Запустить"))
            launch_btn.clicked.connect(
                lambda _checked=False, a=app: self._launch_external(a)
            )
            row.addWidget(launch_btn)
            self.external_launch_btns[app.subdir] = launch_btn

            tools_layout.addLayout(row)

            status_label = QLabel("")
            status_label.setWordWrap(True)
            self.external_status_labels[app.subdir] = status_label
            tools_layout.addWidget(status_label)

        root.addWidget(self.tools_box)
        self._refresh_external_status()

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #d9534f;")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self.log_panel = LogPanel(loc)
        self.log_panel.setMinimumHeight(160)
        root.addWidget(self.log_panel, 1)

        # Полоса прогресса запуска — вместо отдельного экрана "Ожидание".
        # Остаёмся на экране настроек, чтобы был виден живой лог сверху.
        self.progress_row = QWidget()
        progress_layout = QHBoxLayout(self.progress_row)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        self.progress_status_label = QLabel("")
        progress_layout.addWidget(self.progress_status_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFixedWidth(200)
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addStretch(1)
        self.cancel_launch_btn = QPushButton(self._tr("Отмена"))
        self.cancel_launch_btn.clicked.connect(self.cancel_requested.emit)
        progress_layout.addWidget(self.cancel_launch_btn)
        root.addWidget(self.progress_row)
        self.progress_row.setVisible(False)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.launch_btn = QPushButton(self._tr("Запустить"))
        self.launch_btn.setDefault(True)
        self.launch_btn.clicked.connect(self._on_launch)
        btn_row.addWidget(self.launch_btn)
        root.addLayout(btn_row)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(self.AUTOSAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self._auto_save)

        self._refresh_scripts()

    # -- состояние "сервер уже запущен" --------------------------------

    def set_server_running(self, running: bool, port=None):
        self.running_bar.setVisible(running)
        if running and port:
            self.running_label.setText(f"ComfyUI уже запущен на порту {port}")
        for w in (self.path_edit, self.script_combo, self.port_spin,
                  self.disable_auto_launch_check, self.sync_comfy_theme_check,
                  self.launch_btn, self.launch_args_btn):
            w.setEnabled(not running)
        self.launch_args_dialog.set_extra_widgets_enabled(not running)

    # -- прогресс запуска (вместо отдельной страницы) --------------------

    def show_launch_progress(self, text):
        self.progress_status_label.setText(text)
        self.progress_row.setVisible(True)
        self._set_launch_controls_enabled(False)

    def update_launch_progress(self, text):
        self.progress_status_label.setText(text)

    def hide_launch_progress(self):
        self.progress_row.setVisible(False)
        self._set_launch_controls_enabled(True)

    def _set_launch_controls_enabled(self, enabled):
        for w in (self.path_edit, self.script_combo, self.port_spin,
                  self.disable_auto_launch_check, self.sync_comfy_theme_check,
                  self.launch_btn, self.launch_args_btn):
            w.setEnabled(enabled)
        self.launch_args_dialog.set_extra_widgets_enabled(enabled)

    # -- автосохранение --------------------------------------------------

    def _schedule_autosave(self, *_args):
        if self._loading_fields:
            return
        self._save_timer.start()

    def _auto_save(self):
        cfg = dict(self.cfg)
        cfg.update(
            {
                "root_path": self.path_edit.text().strip(),
                "script": self.script_combo.currentText(),
                "port": self.port_spin.value(),
                "disable_auto_launch": self.disable_auto_launch_check.isChecked(),
                "sync_comfy_theme": self.sync_comfy_theme_check.isChecked(),
                "launch_args": self.launch_args_dialog.collect_launch_args(),
            }
        )
        self.cfg = cfg
        save_config(cfg)
        log.debug("Настройки автосохранены")

    # -- прочее ------------------------------------------------------

    def set_status(self, text):
        self.status_label.setText(text)

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def _sync_language_combo_display(self):
        from comfyui_studio.i18n import AVAILABLE_LANGUAGES

        code = self.loc.current_language()
        display = next((n for n, c in AVAILABLE_LANGUAGES.items() if c == code), None)
        if display is not None:
            self.language_combo.blockSignals(True)
            self.language_combo.setCurrentText(display)
            self.language_combo.blockSignals(False)

    def _on_language_changed(self, display_name):
        from comfyui_studio.i18n import AVAILABLE_LANGUAGES

        code = AVAILABLE_LANGUAGES.get(display_name)
        if code is None:
            return
        self.loc.apply_language(code)
        self.retranslate_ui()
        self.language_changed.emit(code)

    def _on_language_changed_externally(self, _code):
        """Язык поменялся в PromptConfigEditor или PromptVault, пока
        лаунчер уже открыт (тут это теоретическая возможность — обычно
        переключатель есть только тут — но на всякий случай тоже
        подхватываем и обновляем видимые тексты)."""
        self._sync_language_combo_display()
        self.retranslate_ui()

    def retranslate_ui(self):
        """Перевыставляет уже построенные тексты этой страницы (и её
        LogPanel) после смены языка — сам по себе выбор языка не
        обновляет текст уже созданных виджетов."""
        self.running_label.setText(self._tr("ComfyUI уже запущен"))
        self.open_running_btn.setText(self._tr("Открыть ComfyUI"))
        self.stop_running_btn.setText(self._tr("Остановить"))
        self.theme_row_label.setText(self._tr("Тема оформления:"))
        self.language_row_label.setText(self._tr("Язык интерфейса:"))
        self.sync_comfy_theme_check.setText(
            self._tr(
                "Синхронизировать тему ComfyUI с темой приложения (ближайший встроенный вариант)"
            )
        )
        self.sync_comfy_theme_check.setToolTip(
            self._tr(
                "Синхронизирует встроенную палитру ComfyUI (Comfy.ColorPalette) с "
                "темой приложения — вживую, пока ComfyUI уже открыт, и при "
                "следующем запуске. Не идентично Qt-теме — у ComfyUI своя "
                "цветовая система узлов."
            )
        )
        self.browse_btn.setText(self._tr("Обзор..."))
        self.path_row_label.setText(self._tr("Папка ComfyUI_windows_portable:"))
        self.launch_args_btn.setText(self._tr("Аргументы запуска ComfyUI..."))
        self.launch_args_dialog.retranslate_ui()
        self.tools_box.setTitle(self._tr("Другие инструменты"))
        for btn in self.external_launch_btns.values():
            btn.setText(self._tr("Запустить"))
        self.launch_btn.setText(self._tr("Запустить"))
        self.cancel_launch_btn.setText(self._tr("Отмена"))
        self._refresh_external_status()
        self.log_panel.retranslate_ui()
        self.resource_bar.retranslate_ui()

    def _on_theme_changed(self, name):
        self.theme_manager.apply_theme(name)
        log.info("Тема оформления изменена на: %s", name)

    def _on_theme_changed_externally(self, name):
        """Тема была изменена в PromptConfigEditor или PromptVault, пока
        лаунчер уже открыт — applying уже сделан в ThemeManager, здесь
        только подтягиваем видимое состояние комбобокса."""
        self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentText(name)
        self.theme_combo.blockSignals(False)

    def _browse(self):
        chosen = QFileDialog.getExistingDirectory(
            self, self._tr("Выберите папку ComfyUI_windows_portable")
        )
        if chosen:
            self.path_edit.setText(chosen)
            self._refresh_scripts()

    def _refresh_external_status(self):
        """Показывает, готово ли каждое приложение к запуску (и как именно
        оно будет запущено) — чисто информационно, путь не редактируется."""
        for app in EXTERNAL_APPS:
            status_label = self.external_status_labels[app.subdir]
            if app.subdir in IN_PROCESS_WINDOW_FACTORIES:
                # Монолитная сборка ComfyUIStudio: инструмент открывается
                # окном этого же процесса, отдельный exe/подпроцесс не
                # ищется и не нужен.
                status_label.setText(self._tr("Готово — откроется в этом же приложении."))
                status_label.setStyleSheet("color: #6fbf73;")
                continue
            cmd, cwd, error = resolve_external_launch(app)
            if error:
                status_label.setText(error)
                status_label.setStyleSheet("color: #d9534f;")
            else:
                status_label.setText(self._tr("Найдено: {}").format(cwd))
                status_label.setStyleSheet("color: #6fbf73;")

    def _launch_external(self, app: "ExternalApp"):
        status_label = self.external_status_labels[app.subdir]

        factory = IN_PROCESS_WINDOW_FACTORIES.get(app.subdir)
        if factory is not None:
            self._open_in_process_window(app, factory, status_label)
            return

        ok, message = launch_external_app(app)
        if ok:
            status_label.setText(
                self._tr("{} запущен в отдельном процессе.").format(app.label)
            )
            status_label.setStyleSheet("color: #6fbf73;")
        else:
            status_label.setText(message)
            status_label.setStyleSheet("color: #d9534f;")

    def _open_in_process_window(self, app: "ExternalApp", factory, status_label):
        """Открывает окно инструмента комплекта в текущем процессе (см.
        IN_PROCESS_WINDOW_FACTORIES/register_in_process_app выше). Если
        окно уже было открыто и просто свёрнуто/скрыто за другими окнами —
        поднимает существующее вместо создания второго.

        Когда пользователь ЗАКРЫВАЕТ окно (крестиком), оно не просто
        прячется: WA_DeleteOnClose заставляет Qt реально уничтожить его
        C++-объект после closeEvent, сигнал destroyed чистит запись в
        self._child_windows, а gc.collect() сразу забирает то, что окно
        держало в памяти (для PromptVault — загруженные модели
        torch/transformers). Раньше запись из кэша не удалялась никогда,
        и вся эта память оставалась занятой до закрытия всего приложения,
        даже если было закрыто только окно инструмента.
        """
        window = self._child_windows.get(app.subdir)
        if window is None:
            try:
                window = factory()
            except Exception as e:
                log.exception("Не удалось открыть окно %s", app.label)
                status_label.setText(f"Не удалось открыть {app.label}: {e}")
                status_label.setStyleSheet("color: #d9534f;")
                return
            window.setAttribute(Qt.WA_DeleteOnClose, True)
            window.destroyed.connect(
                lambda _obj=None, subdir=app.subdir: self._on_child_window_destroyed(subdir)
            )
            self._child_windows[app.subdir] = window

        window.show()
        window.raise_()
        window.activateWindow()

        status_label.setText(self._tr("{} открыт.").format(app.label))
        status_label.setStyleSheet("color: #6fbf73;")

    def _on_child_window_destroyed(self, subdir):
        self._child_windows.pop(subdir, None)
        # Явный gc.collect() — на объекте окна почти наверняка были
        # цикличные ссылки (сигналы/слоты, родитель/потомок в Qt), которые
        # обычный refcounting сам по себе не всегда убирает сразу же.
        gc.collect()
        log.info(
            "Окно инструмента '%s' закрыто и удалено из памяти процесса",
            subdir,
        )

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

    def _on_launch(self):
        root_path = self.path_edit.text().strip()
        ok, msg = validate_portable_root(root_path)
        if not ok:
            self.set_status(self._tr(msg))
            log.warning("Проверка папки не пройдена: %s", msg)
            return
        script = self.script_combo.currentText()
        if not script:
            self.set_status(self._tr("Выберите скрипт запуска"))
            return

        self.set_status("")
        self.log_panel.text.clear()
        cfg = {
            "root_path": root_path,
            "script": script,
            "port": self.port_spin.value(),
            "disable_auto_launch": self.disable_auto_launch_check.isChecked(),
            "sync_comfy_theme": self.sync_comfy_theme_check.isChecked(),
            "launch_args": self.launch_args_dialog.collect_launch_args(),
        }
        self.cfg = cfg
        save_config(cfg)
        self.launch_requested.emit(cfg)


# --------------------------------------------------------------------------
# Наблюдатель за запуском сервера (без своей страницы — прогресс теперь
# показывается прямо на экране настроек, чтобы был виден живой лог)
# --------------------------------------------------------------------------


