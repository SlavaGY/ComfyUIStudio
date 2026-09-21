"""Раздел \"Генератор промптов\" единого дерева настроек: путь к llama.cpp,
файл модели GGUF, необязательный mmproj (изображения), текст-префикс перед
запросом пользователя и несколько дополнительных параметров запуска.

Сама генерация живёт в Imagine (кнопка под полем промпта, см.
comfyui_studio/imagine/backend/promptgen.py) -- эта страница только
редактирует настройки и живьём проверяет, что указанные пути реально
существуют. Настройки пишутся в общий файл
%APPDATA%\\ComfyUIStudio\\prompt_generator.json (см. shared_promptgen.py),
а не в config.json лаунчера: Imagine -- отдельный процесс и читает именно
его, при каждом запуске генерации, поэтому перезапуск Imagine после правок
не нужен.

Запись -- через AppSettingsDialog: страница эмитит `changed`, диалог
откладывает автосохранение (тот же debounce-паттерн, что у остальных
страниц) и вызывает save() этой страницы.

Все строки на этой странице -- исходные на русском (см. пояснение в
general_page.py про TRANSLATIONS/loc.tr()). Сообщения о проблемах с путями
берутся из shared_promptgen.MESSAGES и переводятся теми же шаблонами.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QRegularExpression, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from comfyui_studio import shared_promptgen

_GGUF_FILTER = "Модели GGUF (*.gguf);;Все файлы (*)"


class PromptGeneratorSettingsPage(QWidget):
    # эмитится при ЛЮБОМ изменении любого поля -- AppSettingsDialog
    # планирует автосохранение (см. докстринг модуля)
    changed = Signal()

    def __init__(self, loc=None, parent=None):
        super().__init__(parent)
        self.loc = loc
        # пока строим виджеты и заполняем их из файла, сигналы полей не
        # должны считаться "изменением пользователя" (иначе открытие
        # страницы перезаписывало бы файл)
        self._loading = True
        cfg = shared_promptgen.read_settings()

        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(scroll)
        container = QWidget()
        scroll.setWidget(container)
        root = QVBoxLayout(container)

        self.intro = QLabel()
        self.intro.setWordWrap(True)
        self.intro.setObjectName("mutedLabel")
        root.addWidget(self.intro)

        # -- Модель и llama.cpp -------------------------------------------
        self.model_box = QGroupBox()
        model_form = QFormLayout(self.model_box)

        self.llama_dir_edit = QLineEdit(cfg["llama_dir"])
        self.llama_dir_browse = QPushButton()
        self.llama_dir_browse.clicked.connect(self._browse_llama_dir)
        self.llama_dir_label = QLabel()
        model_form.addRow(self.llama_dir_label, self._path_row(self.llama_dir_edit, self.llama_dir_browse))

        self.model_edit = QLineEdit(cfg["model_path"])
        self.model_browse = QPushButton()
        self.model_browse.clicked.connect(self._browse_model)
        self.model_label = QLabel()
        model_form.addRow(self.model_label, self._path_row(self.model_edit, self.model_browse))

        self.mmproj_edit = QLineEdit(cfg["mmproj_path"])
        self.mmproj_edit.setClearButtonEnabled(True)
        self.mmproj_browse = QPushButton()
        self.mmproj_browse.clicked.connect(self._browse_mmproj)
        self.mmproj_label = QLabel()
        model_form.addRow(self.mmproj_label, self._path_row(self.mmproj_edit, self.mmproj_browse))

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        model_form.addRow(self.status_label)
        self.dll_hint_label = QLabel()
        self.dll_hint_label.setWordWrap(True)
        self.dll_hint_label.setObjectName("mutedLabel")
        model_form.addRow(self.dll_hint_label)
        root.addWidget(self.model_box)

        # -- Текст перед запросом -----------------------------------------
        self.prefix_box = QGroupBox()
        prefix_layout = QVBoxLayout(self.prefix_box)
        self.prefix_hint = QLabel()
        self.prefix_hint.setWordWrap(True)
        self.prefix_hint.setObjectName("mutedLabel")
        prefix_layout.addWidget(self.prefix_hint)
        self.prefix_edit = QPlainTextEdit()
        self.prefix_edit.setPlainText(cfg["prefix"])
        self.prefix_edit.setMinimumHeight(220)
        prefix_layout.addWidget(self.prefix_edit)
        reset_row = QHBoxLayout()
        reset_row.addStretch(1)
        self.prefix_reset_btn = QPushButton()
        self.prefix_reset_btn.clicked.connect(self._reset_prefix)
        reset_row.addWidget(self.prefix_reset_btn)
        prefix_layout.addLayout(reset_row)
        root.addWidget(self.prefix_box)

        # -- Дополнительно ---------------------------------------------------
        self.advanced_box = QGroupBox()
        adv_form = QFormLayout(self.advanced_box)

        self.ctx_spin = QSpinBox()
        self.ctx_spin.setRange(0, 1_048_576)
        self.ctx_spin.setSingleStep(1024)
        self.ctx_spin.setValue(int(cfg["ctx_size"]))
        self.ctx_label = QLabel()
        adv_form.addRow(self.ctx_label, self.ctx_spin)

        self.gpu_layers_edit = QLineEdit(cfg["gpu_layers"])
        self.gpu_layers_edit.setValidator(
            QRegularExpressionValidator(QRegularExpression(r"^(auto|all|\d{0,4})$"))
        )
        self.gpu_layers_label = QLabel()
        adv_form.addRow(self.gpu_layers_label, self.gpu_layers_edit)

        self.dll_dir_edit = QLineEdit(cfg["extra_dll_dir"])
        self.dll_dir_edit.setClearButtonEnabled(True)
        self.dll_dir_browse = QPushButton()
        self.dll_dir_browse.clicked.connect(self._browse_dll_dir)
        self.dll_dir_label = QLabel()
        adv_form.addRow(self.dll_dir_label, self._path_row(self.dll_dir_edit, self.dll_dir_browse))

        self.extra_args_edit = QLineEdit(cfg["extra_args"])
        self.extra_args_label = QLabel()
        adv_form.addRow(self.extra_args_label, self.extra_args_edit)

        self.free_comfy_combo = QComboBox()
        for mode in shared_promptgen.FREE_COMFY_MODES:  # auto / always / never
            self.free_comfy_combo.addItem("", mode)
        self.free_comfy_combo.setCurrentIndex(
            max(0, self.free_comfy_combo.findData(cfg["free_comfy_mode"]))
        )
        self.free_comfy_label = QLabel()
        adv_form.addRow(self.free_comfy_label, self.free_comfy_combo)

        self.image_side_spin = QSpinBox()
        self.image_side_spin.setRange(*shared_promptgen.IMAGE_MAX_SIDE_RANGE)
        self.image_side_spin.setSingleStep(128)
        self.image_side_spin.setValue(int(cfg["image_max_side"]))
        self.image_side_label = QLabel()
        adv_form.addRow(self.image_side_label, self.image_side_spin)

        logs_row = QHBoxLayout()
        self.open_logs_btn = QPushButton()
        self.open_logs_btn.clicked.connect(self._open_logs_folder)
        logs_row.addWidget(self.open_logs_btn)
        logs_row.addStretch(1)
        adv_form.addRow(logs_row)
        root.addWidget(self.advanced_box)

        root.addStretch(1)

        self.retranslate_ui()

        for edit in (
            self.llama_dir_edit, self.model_edit, self.mmproj_edit,
            self.gpu_layers_edit, self.dll_dir_edit, self.extra_args_edit,
        ):
            edit.textChanged.connect(self._on_field_changed)
        self.prefix_edit.textChanged.connect(self._on_field_changed)
        self.ctx_spin.valueChanged.connect(self._on_field_changed)
        self.free_comfy_combo.currentIndexChanged.connect(self._on_field_changed)
        self.image_side_spin.valueChanged.connect(self._on_field_changed)

        self._loading = False
        self._refresh_status()

    # -- построение --------------------------------------------------------

    @staticmethod
    def _path_row(edit: QLineEdit, button: QPushButton) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(edit, 1)
        row.addWidget(button)
        return row

    # -- обзор -------------------------------------------------------------

    def _start_dir(self, edit: QLineEdit) -> str:
        text = edit.text().strip()
        if not text:
            return ""
        return text if os.path.isdir(text) else os.path.dirname(text)

    def _browse_llama_dir(self):
        chosen = QFileDialog.getExistingDirectory(
            self, self._tr("Выберите папку с llama-server.exe"), self._start_dir(self.llama_dir_edit)
        )
        if chosen:
            self.llama_dir_edit.setText(os.path.normpath(chosen))

    def _browse_model(self):
        chosen, _ = QFileDialog.getOpenFileName(
            self, self._tr("Выберите файл модели GGUF"), self._start_dir(self.model_edit),
            self._tr(_GGUF_FILTER),
        )
        if chosen:
            self.model_edit.setText(os.path.normpath(chosen))

    def _browse_mmproj(self):
        chosen, _ = QFileDialog.getOpenFileName(
            self, self._tr("Выберите файл mmproj"),
            self._start_dir(self.mmproj_edit) or self._start_dir(self.model_edit),
            self._tr(_GGUF_FILTER),
        )
        if chosen:
            self.mmproj_edit.setText(os.path.normpath(chosen))

    def _browse_dll_dir(self):
        chosen = QFileDialog.getExistingDirectory(
            self, self._tr("Выберите папку с DLL"), self._start_dir(self.dll_dir_edit)
        )
        if chosen:
            self.dll_dir_edit.setText(os.path.normpath(chosen))

    def _open_logs_folder(self):
        os.makedirs(shared_promptgen.LOG_DIR, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(shared_promptgen.LOG_DIR))

    def _reset_prefix(self):
        self.prefix_edit.setPlainText(shared_promptgen.DEFAULT_PREFIX)

    # -- состояние -----------------------------------------------------------

    def current_settings(self) -> dict:
        return {
            "llama_dir": self.llama_dir_edit.text().strip(),
            "model_path": self.model_edit.text().strip(),
            "mmproj_path": self.mmproj_edit.text().strip(),
            "prefix": self.prefix_edit.toPlainText(),
            "ctx_size": self.ctx_spin.value(),
            "gpu_layers": self.gpu_layers_edit.text().strip(),
            "extra_dll_dir": self.dll_dir_edit.text().strip(),
            "extra_args": self.extra_args_edit.text().strip(),
            "free_comfy_mode": self.free_comfy_combo.currentData(),
            "image_max_side": self.image_side_spin.value(),
        }

    def save(self) -> bool:
        """Пишет настройки в общий файл (вызывается AppSettingsDialog при
        автосохранении)."""
        return shared_promptgen.write_settings(self.current_settings())

    def _on_field_changed(self, *_args):
        """Общий приёмник для сигналов с аргументами (textChanged(str),
        valueChanged(int)…): сам `changed` объявлен как Signal() без
        аргументов -- см. то же пояснение в ComfyUISettingsPage."""
        if self._loading:
            return
        self._refresh_status()
        self.changed.emit()

    def _refresh_status(self):
        cfg = self.current_settings()
        if not shared_promptgen.is_configured(cfg):
            self.status_label.setText(
                self._tr(
                    "Укажите папку llama.cpp и файл модели — пока они не заданы, "
                    "кнопка генератора в Imagine скрыта."
                )
            )
            self.status_label.setObjectName("mutedLabel")
            self.dll_hint_label.setText("")
            self._restyle(self.status_label)
            return

        problem = shared_promptgen.check(cfg)
        if problem is None:
            self.status_label.setText(self._tr("✔ Всё найдено — генератор готов к работе."))
        else:
            code, detail = problem
            template = self._tr(shared_promptgen.MESSAGES[code])
            self.status_label.setText("✖ " + template.format(detail))
        self.status_label.setObjectName("")
        self._restyle(self.status_label)

        # какие папки уйдут в PATH процесса llama-server, кроме самой папки
        # llama.cpp -- чтобы автоподхваченную папку vendor LM Studio было видно
        extra = shared_promptgen.dll_dirs(cfg)[1:]
        self.dll_hint_label.setText(
            self._tr("Папки DLL, добавляемые в PATH при запуске: {}").format("; ".join(extra))
            if extra else ""
        )

    @staticmethod
    def _restyle(widget: QWidget):
        # смена objectName не перечитывает QSS сама -- переприменяем стиль
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    # -- прочее -----------------------------------------------------

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def retranslate_ui(self):
        self.intro.setText(
            self._tr(
                "Кнопка генератора в Imagine запускает локальную модель GGUF через "
                "llama.cpp, отправляет ей ваш текст (и изображение, если указан "
                "mmproj), вставляет ответ в поле промпта и сразу выключает модель. "
                "Изменения применяются без перезапуска Imagine."
            )
        )
        self.model_box.setTitle(self._tr("Модель и llama.cpp"))
        self.llama_dir_label.setText(self._tr("Папка llama.cpp:"))
        self.model_label.setText(self._tr("Файл модели (GGUF):"))
        self.mmproj_label.setText(self._tr("Файл mmproj (необязательно):"))
        self.mmproj_edit.setToolTip(
            self._tr(
                "Нужен только для работы с изображениями: в окне генератора в "
                "Imagine появится кнопка «Прикрепить изображение»."
            )
        )
        for button in (self.llama_dir_browse, self.model_browse, self.mmproj_browse, self.dll_dir_browse):
            button.setText(self._tr("Обзор..."))

        self.prefix_box.setTitle(self._tr("Текст перед запросом"))
        self.prefix_hint.setText(
            self._tr(
                "Этот текст добавляется перед тем, что вы напишете в окне генератора "
                "(это не системный промпт — всё уходит одним сообщением). Метка "
                "{input} задаёт место, куда подставится ваш текст; если метки нет, "
                "текст дописывается в конец."
            )
        )
        self.prefix_reset_btn.setText(self._tr("Сбросить на значение по умолчанию"))

        self.advanced_box.setTitle(self._tr("Дополнительно"))
        self.ctx_label.setText(self._tr("Размер контекста (токенов):"))
        self.ctx_spin.setSpecialValueText(self._tr("из модели"))
        self.ctx_spin.setToolTip(
            self._tr(
                "Должен вмещать текст запроса, изображение и ответ (до 8192 "
                "токенов). Чем больше, тем больше видеопамяти занимает KV-кэш — "
                "а нехватка VRAM заставляет llama-server оставить часть слоёв "
                "на CPU, и генерация замедляется. 0 — взять значение из самой "
                "модели."
            )
        )
        self.gpu_layers_label.setText(self._tr("Слои на GPU (-ngl):"))
        self.gpu_layers_edit.setPlaceholderText(self._tr("авто"))
        self.gpu_layers_edit.setToolTip(
            self._tr(
                "Пусто — llama-server сам подберёт под доступную видеопамять. "
                "Можно указать число слоёв, all или auto."
            )
        )
        self.dll_dir_label.setText(self._tr("Доп. папка с DLL:"))
        self.dll_dir_edit.setToolTip(
            self._tr(
                "Добавляется в PATH процесса llama-server. Для llama-server из "
                "LM Studio папка backends\\vendor\\… с CUDA-библиотеками "
                "находится автоматически; сюда нужно писать, только если "
                "автопоиск не сработал."
            )
        )
        self.extra_args_label.setText(self._tr("Доп. аргументы llama-server:"))
        self.extra_args_edit.setPlaceholderText("--reasoning off")
        self.extra_args_edit.setToolTip(
            self._tr(
                "Добавляются в командную строку как есть — например, "
                "--reasoning off, чтобы отключить «рассуждения» у thinking-моделей."
            )
        )
        self.free_comfy_label.setText(self._tr("Выгружать модели ComfyUI перед запуском:"))
        for index, label in enumerate((
            self._tr("Автоматически (если не хватает VRAM)"),
            self._tr("Всегда"),
            self._tr("Никогда"),
        )):
            self.free_comfy_combo.setItemText(index, label)
        self.free_comfy_combo.setToolTip(
            self._tr(
                "Видеопамять, занятая ComfyUI, заставляет llama-server оставить "
                "часть слоёв модели на CPU — и генерация становится в разы "
                "медленнее. «Автоматически» выгружает модели ComfyUI, только "
                "если по оценке свободной видеопамяти не хватает (нужен драйвер "
                "NVIDIA); следующая генерация картинки загрузит их заново."
            )
        )
        self.image_side_label.setText(self._tr("Макс. сторона изображения (px):"))
        self.image_side_spin.setToolTip(
            self._tr(
                "Прикреплённая картинка уменьшается до этого размера перед "
                "отправкой: чем меньше, тем меньше токенов зрения и тем быстрее "
                "обработка запроса."
            )
        )
        self.open_logs_btn.setText(self._tr("Открыть папку с логами"))
        self.open_logs_btn.setToolTip(
            self._tr(
                "promptgen.log — ход каждой генерации с таймингами, снимками "
                "видеопамяти и предупреждениями; в папке llama-server — полный "
                "вывод llama-server каждого запуска."
            )
        )
        self._refresh_status()
