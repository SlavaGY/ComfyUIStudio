"""Окно настроек — тема, язык, семантический поиск, производительность
(размер страницы ленивой загрузки) и автоочистка (миниатюры/логи).

Раньше тема/язык/переключатель семантического поиска жили прямо в
Toolbar (три отдельные кнопки/меню) — здесь они собраны в одном месте,
открываемом кнопкой "⚙ Settings", чтобы не перегружать тулбар.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from comfyui_studio.promptvault.core.gallery_manager import GalleryManager
from comfyui_studio.promptvault.core.hotkeys import HOTKEY_ACTIONS, HotkeyManager
from comfyui_studio.promptvault.i18n import LocalizationManager
from comfyui_studio.promptvault.settings import AppSettings
from comfyui_studio.promptvault.themes.theme_manager import ThemeManager
from comfyui_studio.promptvault.ui.toolbar import Toolbar


class SettingsWindow(QDialog):
    """Немодальное окно настроек — изменения применяются сразу же, по
    мере переключения (как и раньше в тулбаре), отдельной кнопки
    "Применить"/"OK" нет, только "Close"."""

    # подтверждение (QMessageBox) уже показано этим окном к моменту
    # эмиссии (см. _on_restart_clicked/_on_quit_clicked) — MainWindow
    # выполняет сам перезапуск/выход, т.к. именно оно владеет
    # процедурой аккуратного закрытия (FolderSync.stop/
    # GalleryManager.close — см. MainWindow.closeEvent)
    restartRequested = Signal()
    quitRequested = Signal()

    # эмитится после того, как новая комбинация уже сохранена через
    # HotkeyManager (задача: настраиваемые горячие клавиши) — несёт
    # action_id, а не саму комбинацию, т.к. MainWindow всё равно должна
    # заново прочитать её из HotkeyManager (единственный источник
    # истины), чтобы не рассинхронизироваться при конфликте/сбросе
    hotkeyChanged = Signal(str)

    def __init__(
        self,
        gallery: GalleryManager,
        theme_manager: ThemeManager,
        localization_manager: LocalizationManager,
        toolbar: Toolbar | None = None,
        standalone: bool = True,
        parent: QWidget | None = None,
    ):
        """standalone: False, когда PromptVault открыт ВНУТРИ монолитной
        сборки ComfyUI Studio (общий процесс с лаунчером — см.
        MainWindow(standalone=...) и её docstring). Скрывает блок
        Application (Restart/Quit) целиком в этом случае — self-restart
        через os.execv() в общем процессе убил бы не только PromptVault,
        а лаунчер и управление ComfyUI вместе с ним (см. MainWindow.
        closeEvent), а "Quit" из настроек PromptVault, закрывающий
        только его собственное окно, было бы просто вводящим в
        заблуждение названием кнопки. Studio-wide Restart/Quit — в самой
        ComfyUI Studio, раздел Advanced -> Application (этап 4 дорожной
        карты рефакторинга, доработка по замечанию пользователя).

        toolbar: раньше был обязательным параметром, хотя фактически
        нигде в этом классе не читается (проверено — единственное
        использование было `self.toolbar = toolbar`, ни разу дальше).
        Список хоткеев берётся из отдельного self.hotkey_manager =
        HotkeyManager() ниже, который ничего не знает о toolbar и сам
        читает/пишет назначения через QSettings. Из-за этой (ложной)
        обязательности SettingsWindow нельзя было открыть без уже
        существующего MainWindow PromptVault — теперь можно (см.
        comfyui_studio.promptvault.main.create_settings_window):
        параметр остался в сигнатуре ради обратной совместимости (и на
        случай, если он когда-нибудь понадобится по-настоящему), но
        по умолчанию None и ничем не ограничивает вызывающий код."""

        super().__init__(parent)

        self.gallery = gallery
        self.theme_manager = theme_manager
        self.localization_manager = localization_manager
        self.toolbar = toolbar
        self.standalone = standalone
        self.app_settings = AppSettings()
        self.hotkey_manager = HotkeyManager()

        self.setWindowTitle(self.tr("PromptVault — Settings"))
        self.setMinimumWidth(440)

        self._build_ui()
        # ВАЖНО: подключаемся именно bound-методом (self._on_...), а не
        # lambda-функцией, замыкающей self -- PySide автоматически
        # отключает соединение, когда C++-объект ПОЛУЧАТЕЛЯ уничтожен,
        # но делает это, только распознав получателя как bound-метод
        # QObject'а; у голой lambda такого распознаваемого получателя
        # нет, и соединение остаётся висеть даже после уничтожения self.
        #
        # localization_manager -- общий, более долгоживущий объект (не
        # принадлежит этому окну), поэтому именно эта комбинация опасна:
        # при закрытии PromptVault ВНУТРИ монолитной ComfyUI Studio (см.
        # SettingsPage._open_in_process_window в лаунчере, этап 4
        # дорожной карты -- WA_DeleteOnClose + gc.collect()) C++-объект
        # этого SettingsWindow реально уничтожается, а localization_manager
        # переживает это закрытие. Со старой lambda-связкой следующая же
        # смена языка (например, в едином дереве настроек лаунчера)
        # пыталась вызвать retranslate_ui() на уже удалённом C++-объекте
        # -- `RuntimeError: libshiboken: Internal C++ object (SettingsWindow)
        # already deleted`, всплывавшее не в момент закрытия, а позже, при
        # следующей смене языка, что и затрудняло диагностику. До этапа 4
        # окна никогда не удалялись по-настоящему (см. докстринг
        # _open_in_process_window), поэтому эта связка ни разу не
        # проявлялась как баг раньше.
        self.localization_manager.language_changed_externally.connect(
            self._on_language_changed_externally
        )

    def _on_language_changed_externally(self, _code: str) -> None:
        self.retranslate_ui()

    # --------------------------------------------------

    def _build_ui(self) -> None:

        layout = QVBoxLayout(self)

        layout.addWidget(self._build_hotkeys_group())
        layout.addWidget(self._build_search_group())
        layout.addWidget(self._build_performance_group())
        layout.addWidget(self._build_storage_group())
        if self.standalone:
            layout.addWidget(self._build_application_group())

        layout.addStretch()

        self.close_btn = QPushButton(self.tr("Close"))
        self.close_btn.clicked.connect(self.close)
        layout.addWidget(self.close_btn)

    # --------------------------------------------------
    # Hotkeys: настраиваемые горячие клавиши (задача: настраиваемые
    # горячие клавиши) — сама привязка action_id -> обработчик живёт в
    # MainWindow._register_hotkeys, здесь только редактирование
    # назначенных комбинаций через HotkeyManager (единственный
    # источник истины — то же QSettings-хранилище, что и тема/язык).

    def _build_hotkeys_group(self) -> QGroupBox:

        self.hotkeys_group = QGroupBox(self.tr("Hotkeys"))
        outer = QVBoxLayout(self.hotkeys_group)

        # список действий длинный (почти два десятка) — в отдельной
        # прокручиваемой области, чтобы не растягивать всё окно
        # настроек по высоте на весь экран
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(180)
        scroll.setFrameShape(QScrollArea.NoFrame)

        content = QWidget()
        form = QFormLayout(content)
        scroll.setWidget(content)

        # action_id -> (label, key-sequence editor, кнопка сброса) —
        # нужно для retranslate_ui (только label) и для
        # _on_hotkey_reset/_on_reset_all_hotkeys (editor)
        self._hotkey_rows: dict[str, tuple[QLabel, QKeySequenceEdit, QPushButton]] = {}

        for action_id in HOTKEY_ACTIONS:

            label = QLabel(self._hotkey_label(action_id))

            edit = QKeySequenceEdit(self.hotkey_manager.sequence(action_id))
            edit.editingFinished.connect(
                lambda action_id=action_id: self._on_hotkey_edited(action_id)
            )

            reset_btn = QPushButton(self.tr("↺"))
            reset_btn.setToolTip(self.tr("Reset to default"))
            reset_btn.setFixedWidth(28)
            reset_btn.clicked.connect(
                lambda _checked=False, action_id=action_id: self._on_hotkey_reset(action_id)
            )

            field = QWidget()
            field_layout = QHBoxLayout(field)
            field_layout.setContentsMargins(0, 0, 0, 0)
            field_layout.addWidget(edit, 1)
            field_layout.addWidget(reset_btn)

            form.addRow(label, field)

            self._hotkey_rows[action_id] = (label, edit, reset_btn)

        outer.addWidget(scroll)

        self.hotkey_conflict_hint = QLabel("")
        self.hotkey_conflict_hint.setWordWrap(True)
        self.hotkey_conflict_hint.setStyleSheet("color: #e06c75; font-size: 11px;")
        self.hotkey_conflict_hint.hide()
        outer.addWidget(self.hotkey_conflict_hint)

        self.reset_all_hotkeys_btn = QPushButton(self.tr("Reset all hotkeys to defaults"))
        self.reset_all_hotkeys_btn.clicked.connect(self._on_reset_all_hotkeys)
        outer.addWidget(self.reset_all_hotkeys_btn)

        return self.hotkeys_group

    def _hotkey_label(self, action_id: str) -> str:
        """action_id — ключ из HOTKEY_ACTIONS (см. app/core/hotkeys.py).
        См. _quality_label выше — тот же приём (self.tr() литералом,
        а не переменной, ради pyside6-lupdate) и та же причина."""

        if action_id == "open_folder":
            return self.tr("Open folder")
        if action_id == "focus_search":
            return self.tr("Focus search field")
        if action_id == "toggle_filters":
            return self.tr("Toggle filters popup")
        if action_id == "toggle_sort":
            return self.tr("Toggle sort popup")
        if action_id == "show_statistics":
            return self.tr("Show statistics")
        if action_id == "show_settings":
            return self.tr("Show settings")
        if action_id == "toggle_favorite":
            return self.tr("Toggle favorite")
        if action_id == "edit_metadata":
            return self.tr("Edit metadata")
        if action_id == "add_tags":
            return self.tr("Add tag(s)")
        if action_id == "export_json":
            return self.tr("Export JSON")
        if action_id == "export_zip":
            return self.tr("Export as ZIP")
        if action_id == "open_json_externally":
            return self.tr("Open JSON externally")
        if action_id == "open_in_file_manager":
            return self.tr("Open in file manager")
        if action_id == "delete_from_library":
            return self.tr("Remove from library")
        if action_id == "delete_files":
            return self.tr("Delete files + record")
        if action_id == "toggle_fullscreen":
            return self.tr("Toggle fullscreen")
        if action_id == "reset_image_view":
            return self.tr("Reset image zoom/pan")
        if action_id == "next_image":
            return self.tr("Next image")
        if action_id == "previous_image":
            return self.tr("Previous image")

        return action_id

    def _on_hotkey_edited(self, action_id: str) -> None:
        """editingFinished срабатывает и при простом уходе фокуса без
        изменений — но set_sequence/hotkeyChanged в этом случае
        безвредны (запишется то же самое значение, MainWindow
        выставит тот же QKeySequence на уже существующий QShortcut),
        так что отдельная проверка "а изменилось ли что-то" не нужна."""

        _label, edit, _reset_btn = self._hotkey_rows[action_id]
        new_sequence = edit.keySequence()

        conflict_id = self.hotkey_manager.find_conflict(action_id, new_sequence)

        if conflict_id is not None:
            self.hotkey_conflict_hint.setText(
                self.tr(
                    "\"{}\" is already assigned to \"{}\" — choose a different "
                    "combination or clear that one first."
                ).format(new_sequence.toString(), self._hotkey_label(conflict_id))
            )
            self.hotkey_conflict_hint.show()

            # откатываем поле к тому, что реально сохранено (не
            # применяем конфликтующую комбинацию)
            edit.setKeySequence(self.hotkey_manager.sequence(action_id))
            return

        self.hotkey_conflict_hint.hide()

        self.hotkey_manager.set_sequence(action_id, new_sequence)
        self.hotkeyChanged.emit(action_id)

    def _on_hotkey_reset(self, action_id: str) -> None:

        self.hotkey_manager.reset(action_id)

        _label, edit, _reset_btn = self._hotkey_rows[action_id]
        edit.setKeySequence(self.hotkey_manager.sequence(action_id))

        self.hotkeyChanged.emit(action_id)

    def _on_reset_all_hotkeys(self) -> None:

        for action_id in HOTKEY_ACTIONS:

            self.hotkey_manager.reset(action_id)

            _label, edit, _reset_btn = self._hotkey_rows[action_id]
            edit.setKeySequence(self.hotkey_manager.sequence(action_id))

            self.hotkeyChanged.emit(action_id)

    # --------------------------------------------------
    # Search: семантический поиск (задача: оптимизация памяти)

    def _build_search_group(self) -> QGroupBox:

        self.search_group = QGroupBox(self.tr("Search"))
        layout = QVBoxLayout(self.search_group)

        self.semantic_search_checkbox = QCheckBox(self.tr("Enable semantic search"))
        self.semantic_search_checkbox.setChecked(self.gallery.semantic_search_enabled())
        self.semantic_search_checkbox.toggled.connect(self._on_semantic_search_toggled)
        layout.addWidget(self.semantic_search_checkbox)

        self.semantic_search_hint = QLabel(
            self.tr(
                "Disable if you don't use semantic (meaning-based) search — "
                "the embedding model (~1.3GB) will then never be loaded into "
                "memory. Takes full effect from the next app start if the "
                "model is already loaded in this session."
            )
        )
        self.semantic_search_hint.setWordWrap(True)
        self.semantic_search_hint.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self.semantic_search_hint)

        # -------- выбор модели эмбеддинга (задача: выбор модели) --------
        #
        # Обёрнуты в отдельный QWidget (а не просто QFormLayout, который
        # сам по себе не умеет setVisible()), чтобы прятать их ЦЕЛИКОМ,
        # когда сам переключатель "Enable semantic search" выключен —
        # см. _apply_semantic_search_enabled_visibility() ниже. Раньше
        # они только дизейблились (см. _apply_semantic_search_availability,
        # который остаётся ортогональным механизмом — дизейблит, когда
        # физически недоступна сама библиотека), но оставались видны и
        # занимали место на экране, даже когда семантический поиск
        # выключен и настраивать тут решительно нечего.

        self.embedding_settings_widget = QWidget()
        form = QFormLayout(self.embedding_settings_widget)
        form.setContentsMargins(0, 0, 0, 0)

        self.embedding_model_box = QComboBox()
        self._embedding_model_keys: list[str | None] = []
        self._populate_embedding_model_box()

        self.embedding_model_box.currentIndexChanged.connect(
            self._on_embedding_model_changed
        )

        self.embedding_model_label = QLabel(self.tr("Embedding model"))
        form.addRow(self.embedding_model_label, self.embedding_model_box)

        # "Auto"/"CPU"/"GPU" намеренно не переводятся — как и темы выше,
        # это скорее технические ярлыки (CPU/GPU — общепринятые
        # аббревиатуры в любом языке), а сравнение текста с device_map
        # ниже привязано именно к этим литералам: перевод "Auto" сломал
        # бы это сопоставление, если забыть обновить device_map вместе
        # с ним
        self.embedding_device_box = QComboBox()
        self.embedding_device_box.addItems(["Auto", "CPU", "GPU"])

        device_map = {"auto": "Auto", "cpu": "CPU", "cuda": "GPU"}
        self.embedding_device_box.setCurrentText(
            device_map.get(self.gallery.device_preference(), "Auto")
        )
        self.embedding_device_box.currentTextChanged.connect(
            self._on_embedding_device_changed
        )

        self.embedding_device_label = QLabel(self.tr("Computation device"))
        form.addRow(self.embedding_device_label, self.embedding_device_box)

        layout.addWidget(self.embedding_settings_widget)

        self.gpu_warning_label = QLabel(
            self.tr(
                "GPU requires a CUDA-enabled build of PyTorch — a plain "
                "'pip install torch' often installs a CPU-only build on "
                "Windows. Without it, GPU falls back to CPU automatically."
            )
        )
        self.gpu_warning_label.setWordWrap(True)
        self.gpu_warning_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self.gpu_warning_label)

        self.recompute_btn = QPushButton(self.tr("Recompute all embeddings now"))
        self.recompute_btn.setToolTip(
            self.tr(
                "Recomputes embeddings for every generation using the "
                "currently selected model — needed after switching models, "
                "since different models produce incompatible vectors. Can "
                "take a while for large libraries."
            )
        )
        self.recompute_btn.clicked.connect(self._on_recompute_clicked)
        layout.addWidget(self.recompute_btn)

        # -------- удаление посчитанных векторов (НОВОЕ) --------
        #
        # Не завязана на self.semantic_search_checkbox (видна и активна
        # всегда, в отличие от блока выше) — это операция очистки
        # хранилища, не имеющая отношения к тому, включён ли сейчас
        # переключатель, и не требующая самой библиотеки
        # sentence-transformers/torch (чистый DELETE в БД, см.
        # GenerationRepository.clear_all_embeddings) — можно подчистить
        # оставшиеся векторы, даже уже удалив тяжёлые зависимости.
        self.delete_vectors_btn = QPushButton(self.tr("Delete all vectors"))
        self.delete_vectors_btn.setToolTip(
            self.tr(
                "Deletes the computed embedding vectors for every "
                "generation — frees up space in the database. Semantic "
                "search will fall back to plain text search until vectors "
                "are computed again (enable semantic search and use "
                "'Recompute all embeddings now', or they'll be computed "
                "gradually as generations are added/edited)."
            )
        )
        self.delete_vectors_btn.clicked.connect(self._on_delete_vectors_clicked)
        layout.addWidget(self.delete_vectors_btn)

        self._apply_semantic_search_availability()

        return self.search_group

    def _apply_semantic_search_availability(self) -> None:
        """Дизейблит весь блок семантического поиска и подменяет
        подсказку, если sentence-transformers/torch физически не
        установлены (этап 3 дорожной карты рефакторинга — опциональная
        зависимость `pip install .[promptvault]`).

        Раньше чекбокс включался независимо от того, установлена ли
        библиотека вообще — включить его можно было всегда, просто без
        эффекта (embedding.get_model() тихо проваливался бы в None при
        первой попытке). Теперь недоступность видна пользователю сразу,
        а не только как "почему-то не находит по смыслу" в процессе
        работы.

        Вызывается один раз при построении окна и заново из
        retranslate_ui() (после смены языка), т.к. меняет текст
        подсказки.
        """

        from comfyui_studio.mem_diagnostics import log_memory

        log_memory("SettingsWindow: до semantic_search_available()")
        available = self.gallery.semantic_search_available()
        log_memory("SettingsWindow: после semantic_search_available() (должно быть без скачка)")

        self.semantic_search_checkbox.setEnabled(available)
        self.embedding_settings_widget.setEnabled(available)
        self.recompute_btn.setEnabled(available)

        if available:
            self.semantic_search_hint.setText(
                self.tr(
                    "Disable if you don't use semantic (meaning-based) search "
                    "— the embedding model (~1.3GB) will then never be loaded "
                    "into memory. Takes full effect from the next app start "
                    "if the model is already loaded in this session."
                )
            )
        else:
            # чекбокс мог быть True в QSettings с прошлого запуска (когда
            # зависимость ещё стояла) — визуально снимаем галку, чтобы
            # не выглядело как "включено, но не работает"; сам сохранённый
            # выбор в QSettings НЕ трогаем. blockSignals — иначе
            # setChecked(False) сам вызвал бы _on_semantic_search_toggled
            # и тот бы вызвал self.gallery.set_semantic_search_enabled(False),
            # реально перезаписав сохранённый выбор пользователя, что
            # прямо противоречит написанному выше — если зависимость
            # снова появится, прежний выбор пользователя должен
            # вернуться как есть, а не быть молча забыт из-за того, что
            # окно настроек открывали, пока зависимости не было.
            self.semantic_search_checkbox.blockSignals(True)
            self.semantic_search_checkbox.setChecked(False)
            self.semantic_search_checkbox.blockSignals(False)
            self.semantic_search_hint.setText(
                self.tr(
                    "Semantic search needs optional dependencies that "
                    "aren't installed (sentence-transformers, PyTorch). "
                    "Install them with 'pip install .[promptvault]' (or use "
                    "the full build) to enable it — regular text search "
                    "keeps working either way."
                )
            )

        # blockSignals выше означает, что toggled не сработал сам — эта
        # видимость не обновится сама собой, обновляем явно.
        self._apply_semantic_search_enabled_visibility()

    def _on_semantic_search_toggled(self, checked: bool) -> None:
        self.gallery.set_semantic_search_enabled(checked)
        self._apply_semantic_search_enabled_visibility()

    def _apply_semantic_search_enabled_visibility(self) -> None:
        """Скрывает (а не просто дизейблит) выбор модели эмбеддинга,
        устройства и кнопку "Recompute all embeddings now", пока сам
        переключатель "Enable semantic search" выключен — эти элементы
        ничего не настраивают, пока семантический поиск не включён, и
        не должны занимать место на экране/сбивать с толку.

        Независимо от _apply_semantic_search_availability() выше (та
        дизейблит то же самое, если сама библиотека физически не
        установлена) — то есть виджеты ВИДНЫ, только если переключатель
        включён, и АКТИВНЫ, только если вдобавок доступна зависимость;
        обе проверки самостоятельны и не должны дублировать логику друг
        друга.

        "Delete all vectors" сюда не входит — эта кнопка видна и активна
        всегда, независимо от переключателя (см. её тултип и комментарий
        в _build_search_group)."""

        enabled = self.semantic_search_checkbox.isChecked()
        self.embedding_settings_widget.setVisible(enabled)
        self.gpu_warning_label.setVisible(enabled)
        self.recompute_btn.setVisible(enabled)

    def _populate_embedding_model_box(self) -> None:
        """(Пере)заполняет embedding_model_box переведёнными текстами
        (задача: полный аудит строк UI под self.tr()) — вызывается и
        при первом построении окна, и из retranslate_ui() после смены
        языка, поэтому сохраняет текущий выбор через currentIndex(),
        а не полагается на то, что он переживёт clear() сам.

        quality/speed/recommendation в EMBEDDING_MODELS (см.
        app/config.py) — нейтральные ключи, а не готовый текст: сам
        текст на нужном языке даёт self.tr() в _quality_label/
        _speed_label/_recommendation_label ниже. Раньше здесь был
        готовый русский текст прямо в core-слое (app/config.py) —
        из-за этого селектор модели показывал русский текст даже при
        выбранном английском интерфейсе.
        """

        previous_index = self.embedding_model_box.currentIndex()

        self.embedding_model_box.blockSignals(True)

        try:
            self.embedding_model_box.clear()
            self._embedding_model_keys = []

            for key, info in self.gallery.available_embedding_models().items():
                self.embedding_model_box.addItem(
                    self.tr("{} — {} MB RAM, quality: {}, speed: {} ({})").format(
                        key, info["ram_mb"],
                        self._quality_label(info["quality"]),
                        self._speed_label(info["speed"]),
                        self._recommendation_label(info["recommendation"]),
                    )
                )
                self._embedding_model_keys.append(key)

            self.embedding_model_box.addItem(
                self.tr("No model (disable semantic search)")
            )
            self._embedding_model_keys.append(None)

            if previous_index >= 0:
                self.embedding_model_box.setCurrentIndex(previous_index)
            else:
                current_key = self.gallery.embedding_model_key()
                try:
                    self.embedding_model_box.setCurrentIndex(
                        self._embedding_model_keys.index(current_key)
                    )
                except ValueError:
                    pass

        finally:
            self.embedding_model_box.blockSignals(False)

    def _quality_label(self, key: str) -> str:
        """key — одно из значений EMBEDDING_MODELS[...]["quality"]
        (см. app/config.py). Каждый self.tr() здесь — литерал (не
        переменная) специально: pyside6-lupdate находит строки для
        перевода статическим разбором исходников и не умеет
        отследить self.tr(some_dict[key]) — только self.tr("...")."""

        if key == "excellent":
            return self.tr("Excellent")
        if key == "very_good":
            return self.tr("Very good")
        if key == "good":
            return self.tr("Good")

        return key

    def _speed_label(self, key: str) -> str:
        """См. _quality_label — тот же приём и та же причина."""

        if key == "medium":
            return self.tr("Medium")
        if key == "faster":
            return self.tr("Faster")
        if key == "very_fast":
            return self.tr("Very fast")
        if key == "fast":
            return self.tr("Fast")

        return key

    def _recommendation_label(self, key: str) -> str:
        """См. _quality_label — тот же приём и та же причина."""

        if key == "best_default":
            return self.tr("Best default choice")
        if key == "good_balance":
            return self.tr("Good balance")
        if key == "great_alternative":
            return self.tr("Great alternative")
        if key == "for_weak_machines":
            return self.tr("For weaker machines")
        if key == "tradeoff":
            return self.tr("Trade-off")

        return key

    def _on_embedding_model_changed(self, index: int) -> None:

        if not (0 <= index < len(self._embedding_model_keys)):
            return

        model_key = self._embedding_model_keys[index]

        self.gallery.set_embedding_model(model_key)

        if model_key is None:
            return

        answer = QMessageBox.question(
            self,
            self.tr("Recompute embeddings?"),
            self.tr(
                "The embedding model was changed. Existing embeddings were "
                "computed with the previous model and are now incompatible "
                "— semantic search results will be inaccurate until they "
                "are recomputed.\n\nRecompute all embeddings now? (This can "
                "take a while for large libraries; you can also do this "
                "later using the button below.)"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if answer == QMessageBox.Yes:
            self._recompute_all_embeddings()

    def _on_embedding_device_changed(self, text: str) -> None:

        device_map = {"Auto": "auto", "CPU": "cpu", "GPU": "cuda"}
        preference = device_map.get(text, "auto")

        if preference == "cuda" and not self.gallery.gpu_available():
            QMessageBox.warning(
                self,
                self.tr("GPU not available"),
                self.tr(
                    "No CUDA-capable PyTorch installation was detected — "
                    "the CPU will be used instead until a CUDA build of "
                    "PyTorch is installed."
                ),
            )

        self.gallery.set_device_preference(preference)

    def _on_recompute_clicked(self) -> None:

        answer = QMessageBox.question(
            self,
            self.tr("Recompute embeddings?"),
            self.tr(
                "Recompute embeddings for every generation in the library "
                "using the currently selected model? This can take a while "
                "for large libraries."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if answer == QMessageBox.Yes:
            self._recompute_all_embeddings()

    def _recompute_all_embeddings(self) -> None:

        total = self.gallery.recompute_all_embeddings()

        QMessageBox.information(
            self,
            self.tr("Done"),
            self.tr("Recomputed embeddings for {n} generations.").format(n=total),
        )

    def _on_delete_vectors_clicked(self) -> None:

        answer = QMessageBox.question(
            self,
            self.tr("Delete all vectors?"),
            self.tr(
                "Delete the computed embedding vectors for every generation "
                "in the library? This frees up space in the database. "
                "Semantic search will fall back to plain text search until "
                "vectors are computed again."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if answer == QMessageBox.Yes:
            self._delete_all_vectors()

    def _delete_all_vectors(self) -> None:

        total = self.gallery.clear_all_embeddings()

        QMessageBox.information(
            self,
            self.tr("Done"),
            self.tr("Deleted vectors for {n} generations.").format(n=total),
        )

    # --------------------------------------------------
    # Performance: размер страницы ленивой загрузки (задача 3.3)

    def _build_performance_group(self) -> QGroupBox:

        self.performance_group = QGroupBox(self.tr("Performance"))
        form = QFormLayout(self.performance_group)

        self.page_size_spin = QSpinBox()
        self.page_size_spin.setRange(50, 20000)
        self.page_size_spin.setSingleStep(50)
        self.page_size_spin.setValue(self.gallery.generations_page_size())
        self.page_size_spin.setToolTip(
            self.tr(
                "How many generations to load per page when opening a "
                "folder (lazy loading). Takes effect the next time a "
                "folder is opened."
            )
        )
        self.page_size_spin.valueChanged.connect(
            self.gallery.set_generations_page_size
        )
        self.page_size_label = QLabel(self.tr("Page size (lazy loading)"))
        form.addRow(self.page_size_label, self.page_size_spin)

        return self.performance_group

    # --------------------------------------------------
    # Storage & cleanup: автоочистка миниатюр/логов (задача 3.5)

    def _build_storage_group(self) -> QGroupBox:

        self.storage_group = QGroupBox(self.tr("Storage && cleanup"))
        form = QFormLayout(self.storage_group)

        self.thumbnail_age_spin = QSpinBox()
        self.thumbnail_age_spin.setRange(1, 3650)
        self.thumbnail_age_spin.setValue(self.app_settings.thumbnail_max_age_days())
        self.thumbnail_age_spin.setSuffix(self.tr(" days"))
        self.thumbnail_age_spin.valueChanged.connect(
            self.app_settings.set_thumbnail_max_age_days
        )
        self.thumbnail_age_label = QLabel(self.tr("Delete thumbnails older than"))
        form.addRow(self.thumbnail_age_label, self.thumbnail_age_spin)

        self.thumbnail_size_spin = QSpinBox()
        self.thumbnail_size_spin.setRange(10, 100000)
        self.thumbnail_size_spin.setValue(self.app_settings.thumbnail_cache_max_mb())
        self.thumbnail_size_spin.setSuffix(self.tr(" MB"))
        self.thumbnail_size_spin.valueChanged.connect(
            self.app_settings.set_thumbnail_cache_max_mb
        )
        self.thumbnail_size_label = QLabel(self.tr("Max thumbnail cache size"))
        form.addRow(self.thumbnail_size_label, self.thumbnail_size_spin)

        self.log_age_spin = QSpinBox()
        self.log_age_spin.setRange(1, 3650)
        self.log_age_spin.setValue(self.app_settings.log_max_age_days())
        self.log_age_spin.setSuffix(self.tr(" days"))
        self.log_age_spin.valueChanged.connect(
            self.app_settings.set_log_max_age_days
        )
        self.log_age_label = QLabel(self.tr("Delete logs older than"))
        form.addRow(self.log_age_label, self.log_age_spin)

        self.log_size_spin = QSpinBox()
        self.log_size_spin.setRange(1, 100000)
        self.log_size_spin.setValue(self.app_settings.log_dir_max_mb())
        self.log_size_spin.setSuffix(self.tr(" MB"))
        self.log_size_spin.valueChanged.connect(
            self.app_settings.set_log_dir_max_mb
        )
        self.log_size_label = QLabel(self.tr("Max log folder size"))
        form.addRow(self.log_size_label, self.log_size_spin)

        self.storage_hint = QLabel(
            self.tr("Cleanup runs once on app startup — changes take effect next launch.")
        )
        self.storage_hint.setWordWrap(True)
        self.storage_hint.setStyleSheet("color: gray; font-size: 11px;")
        form.addRow(self.storage_hint)

        return self.storage_group

    # --------------------------------------------------
    # Application: перезапуск / выход (задача)

    def _build_application_group(self) -> QGroupBox:

        self.application_group = QGroupBox(self.tr("Application"))
        layout = QHBoxLayout(self.application_group)

        self.restart_btn = QPushButton(self.tr("🔄 Restart"))
        self.restart_btn.setToolTip(
            self.tr("Restart PromptVault (e.g. after changing a setting that needs it).")
        )
        self.restart_btn.clicked.connect(self._on_restart_clicked)
        layout.addWidget(self.restart_btn)

        self.quit_btn = QPushButton(self.tr("⏻ Quit"))
        self.quit_btn.setToolTip(self.tr("Close PromptVault completely."))
        self.quit_btn.clicked.connect(self._on_quit_clicked)
        layout.addWidget(self.quit_btn)

        return self.application_group

    def _on_restart_clicked(self) -> None:

        answer = QMessageBox.question(
            self,
            self.tr("Restart application"),
            self.tr("Restart PromptVault now?"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if answer == QMessageBox.Yes:
            self.restartRequested.emit()

    def _on_quit_clicked(self) -> None:

        answer = QMessageBox.question(
            self,
            self.tr("Quit application"),
            self.tr("Quit PromptVault now?"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if answer == QMessageBox.Yes:
            self.quitRequested.emit()

    # --------------------------------------------------

    def retranslate_ui(self) -> None:
        """Перевыставляет собственные тексты этого окна после смены
        языка (аналогично Toolbar.retranslate_ui) — установка нового
        QTranslator сама по себе не обновляет текст уже созданных
        виджетов."""

        self.setWindowTitle(self.tr("PromptVault — Settings"))

        self.hotkeys_group.setTitle(self.tr("Hotkeys"))
        self.reset_all_hotkeys_btn.setText(self.tr("Reset all hotkeys to defaults"))
        for action_id, (label, _edit, reset_btn) in self._hotkey_rows.items():
            label.setText(self._hotkey_label(action_id))
            reset_btn.setToolTip(self.tr("Reset to default"))

        self.search_group.setTitle(self.tr("Search"))
        self.semantic_search_checkbox.setText(self.tr("Enable semantic search"))
        self.embedding_model_label.setText(self.tr("Embedding model"))
        self._populate_embedding_model_box()
        self.embedding_device_label.setText(self.tr("Computation device"))
        self.gpu_warning_label.setText(
            self.tr(
                "GPU requires a CUDA-enabled build of PyTorch — a plain "
                "'pip install torch' often installs a CPU-only build on "
                "Windows. Without it, GPU falls back to CPU automatically."
            )
        )
        self.recompute_btn.setText(self.tr("Recompute all embeddings now"))
        self.delete_vectors_btn.setText(self.tr("Delete all vectors"))
        self.delete_vectors_btn.setToolTip(
            self.tr(
                "Deletes the computed embedding vectors for every "
                "generation — frees up space in the database. Semantic "
                "search will fall back to plain text search until vectors "
                "are computed again (enable semantic search and use "
                "'Recompute all embeddings now', or they'll be computed "
                "gradually as generations are added/edited)."
            )
        )
        # переустанавливает semantic_search_hint (и заново применяет
        # disabled-состояние виджетов) — текст подсказки зависит от
        # доступности зависимости, см. её docstring
        self._apply_semantic_search_availability()

        self.performance_group.setTitle(self.tr("Performance"))
        self.page_size_label.setText(self.tr("Page size (lazy loading)"))
        self.page_size_spin.setToolTip(
            self.tr(
                "How many generations to load per page when opening a "
                "folder (lazy loading). Takes effect the next time a "
                "folder is opened."
            )
        )

        self.storage_group.setTitle(self.tr("Storage && cleanup"))
        self.thumbnail_age_label.setText(self.tr("Delete thumbnails older than"))
        self.thumbnail_age_spin.setSuffix(self.tr(" days"))
        self.thumbnail_size_label.setText(self.tr("Max thumbnail cache size"))
        self.thumbnail_size_spin.setSuffix(self.tr(" MB"))
        self.log_age_label.setText(self.tr("Delete logs older than"))
        self.log_age_spin.setSuffix(self.tr(" days"))
        self.log_size_label.setText(self.tr("Max log folder size"))
        self.log_size_spin.setSuffix(self.tr(" MB"))
        self.storage_hint.setText(
            self.tr("Cleanup runs once on app startup — changes take effect next launch.")
        )

        if self.standalone:
            self.application_group.setTitle(self.tr("Application"))
            self.restart_btn.setText(self.tr("🔄 Restart"))
            self.restart_btn.setToolTip(
                self.tr("Restart PromptVault (e.g. after changing a setting that needs it).")
            )
            self.quit_btn.setText(self.tr("⏻ Quit"))
            self.quit_btn.setToolTip(self.tr("Close PromptVault completely."))

        self.close_btn.setText(self.tr("Close"))
