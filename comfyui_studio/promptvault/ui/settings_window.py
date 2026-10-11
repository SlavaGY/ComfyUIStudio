"""Окно настроек — тема, язык, производительность (размер страницы
ленивой загрузки) и автоочистка (миниатюры/логи).

Раньше тема/язык жили прямо в Toolbar (отдельные кнопки/меню) — здесь
они собраны в одном месте, открываемом кнопкой "⚙ Settings", чтобы не
перегружать тулбар.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
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
