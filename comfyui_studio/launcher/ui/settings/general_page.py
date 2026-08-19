"""Раздел "General" единого дерева настроек: язык, тема, автозапуск,
проверка обновлений.

Язык и тема физически те же самые ThemeManager/LocalizationManager,
что были в плоском SettingsPage (см. ui/settings_page.py до этапа 4) --
здесь только перенесённые сюда виджеты, поведение не изменилось.
Startup/Updates -- новые, см. их докстринги ниже.

Все строки интерфейса на этой странице -- ИСХОДНЫЕ на русском (как и
везде в comfyui_studio.i18n/comfyui_studio.launcher, см. TRANSLATIONS
в i18n.py: перевод на английский — это словарь ru -> en, применяемый
вручную через loc.tr(), а не Qt-механизм tr()/.ts/.qm). Первая версия
этого файла (до правки) ошибочно передавала в tr() уже английский
текст — из-за этого при русском языке интерфейса (TRANSLATIONS не
содержит обратного en -> ru словаря) вся эта страница показывалась бы
по-английски независимо от выбранного языка.

Часть этапа 4 дорожной карты рефакторинга ("Единое дерево настроек").
"""

from __future__ import annotations

import webbrowser

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from comfyui_studio import __version__ as APP_VERSION
from comfyui_studio.themes.theme_manager import ThemeManager

from ...core import autostart
from ...core.logging_setup import log


class GeneralSettingsPage(QWidget):
    # эмитится после того, как язык реально применён (LocalizationManager.
    # apply_language уже вызван) -- AppSettingsDialog ретранслирует его
    # наружу как свой собственный language_changed, на который подписан
    # MainWindow (ровно так же, как раньше был подписан на
    # SettingsPage.language_changed).
    language_changed = Signal(str)

    def __init__(self, cfg: dict, theme_manager: ThemeManager, loc=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.theme_manager = theme_manager
        self.loc = loc

        root = QVBoxLayout(self)

        # -- Тема и язык (перенесено из старого плоского SettingsPage;
        # "Тема оформления:"/"Язык интерфейса:" -- те же самые исходные
        # строки, что были там, перевод в TRANSLATIONS уже есть) --------
        appearance_box = QGroupBox(self._tr("Оформление и язык"))
        self.appearance_box = appearance_box
        form = QFormLayout(appearance_box)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(theme_manager.available_themes())
        self.theme_combo.setCurrentText(theme_manager.current_theme())
        self.theme_combo.currentTextChanged.connect(self._on_theme_changed)
        theme_manager.theme_changed_externally.connect(self._on_theme_changed_externally)
        self.theme_row_label = QLabel(self._tr("Тема оформления:"))
        form.addRow(self.theme_row_label, self.theme_combo)

        self.language_combo = QComboBox()
        if self.loc is not None:
            self.language_combo.addItems(self.loc.available_languages())
            self._sync_language_combo_display()
            self.language_combo.currentTextChanged.connect(self._on_language_changed)
            self.loc.language_changed_externally.connect(self._on_language_changed_externally)
        self.language_row_label = QLabel(self._tr("Язык интерфейса:"))
        form.addRow(self.language_row_label, self.language_combo)

        root.addWidget(appearance_box)

        # -- Startup (НОВОЕ, этап 4) -----------------------------------
        self.startup_box = QGroupBox(self._tr("Автозапуск"))
        startup_layout = QVBoxLayout(self.startup_box)

        self.autostart_check = QCheckBox(
            self._tr("Запускать ComfyUI Studio при старте Windows")
        )
        if autostart.is_supported():
            self.autostart_check.setChecked(autostart.is_enabled())
            self.autostart_check.toggled.connect(self._on_autostart_toggled)
        else:
            self.autostart_check.setEnabled(False)
        startup_layout.addWidget(self.autostart_check)

        self.autostart_hint = QLabel(
            self._tr(
                "Добавляет ComfyUI Studio в автозагрузку текущего "
                "пользователя Windows (права администратора не нужны). "
                "Сам ComfyUI при этом автоматически не запускается — "
                "только открывается приложение, как при обычном запуске "
                "вручную."
            )
            if autostart.is_supported()
            else self._tr("Автозапуск доступен только в Windows.")
        )
        self.autostart_hint.setWordWrap(True)
        self.autostart_hint.setObjectName("mutedLabel")
        startup_layout.addWidget(self.autostart_hint)

        root.addWidget(self.startup_box)

        # -- Updates (НОВОЕ, этап 4; сознательно — заготовка, не
        # приоритет, см. дорожную карту, этап 4: настоящей проверки
        # версии/автообновления здесь нет — только версия текущей
        # сборки и ссылка на страницу релизов, где пользователь может
        # сравнить сам) ------------------------------------------------
        self.updates_box = QGroupBox(self._tr("Обновления"))
        updates_layout = QVBoxLayout(self.updates_box)

        self.version_label = QLabel(
            self._tr("Текущая версия: {version}").format(version=APP_VERSION)
        )
        updates_layout.addWidget(self.version_label)

        self.check_updates_btn = QPushButton(self._tr("Открыть страницу релизов..."))
        self.check_updates_btn.setToolTip(
            self._tr(
                "Открывает страницу релизов на GitHub в браузере — "
                "автоматической проверки обновлений пока нет, это "
                "заготовка на будущее (см. дорожную карту рефакторинга, "
                "этап 4)."
            )
        )
        self.check_updates_btn.clicked.connect(self._open_releases_page)
        updates_layout.addWidget(self.check_updates_btn)

        root.addWidget(self.updates_box)
        root.addStretch(1)

    # -- тема -------------------------------------------------------

    def _on_theme_changed(self, name):
        self.theme_manager.apply_theme(name)
        log.info("Тема оформления изменена на: %s", name)

    def _on_theme_changed_externally(self, name):
        self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentText(name)
        self.theme_combo.blockSignals(False)

    # -- язык -------------------------------------------------------

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
        self._sync_language_combo_display()
        self.retranslate_ui()

    # -- автозапуск ---------------------------------------------------

    def _on_autostart_toggled(self, checked):
        ok, error = autostart.set_enabled(checked)
        if not ok:
            self.autostart_hint.setText(error)
            # откатываем визуальное состояние чекбокса, раз в реестр
            # записать не удалось — иначе UI врал бы о реальном состоянии
            self.autostart_check.blockSignals(True)
            self.autostart_check.setChecked(not checked)
            self.autostart_check.blockSignals(False)

    # -- updates --------------------------------------------------------

    def _open_releases_page(self):
        webbrowser.open("https://github.com/SlavaGY/ComfyUIStudio/releases")

    # -- прочее -----------------------------------------------------

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def retranslate_ui(self):
        self.appearance_box.setTitle(self._tr("Оформление и язык"))
        self.theme_row_label.setText(self._tr("Тема оформления:"))
        self.language_row_label.setText(self._tr("Язык интерфейса:"))
        self.startup_box.setTitle(self._tr("Автозапуск"))
        self.autostart_check.setText(
            self._tr("Запускать ComfyUI Studio при старте Windows")
        )
        self.autostart_hint.setText(
            self._tr(
                "Добавляет ComfyUI Studio в автозагрузку текущего "
                "пользователя Windows (права администратора не нужны). "
                "Сам ComfyUI при этом автоматически не запускается — "
                "только открывается приложение, как при обычном запуске "
                "вручную."
            )
            if autostart.is_supported()
            else self._tr("Автозапуск доступен только в Windows.")
        )
        self.updates_box.setTitle(self._tr("Обновления"))
        self.version_label.setText(
            self._tr("Текущая версия: {version}").format(version=APP_VERSION)
        )
        self.check_updates_btn.setText(self._tr("Открыть страницу релизов..."))
