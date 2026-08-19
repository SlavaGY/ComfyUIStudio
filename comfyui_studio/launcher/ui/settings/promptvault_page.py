"""Раздел "PromptVault" единого дерева настроек: путь к базе данных +
резервная копия (этап 4) и настоящее окно настроек самого PromptVault
(Search/Performance/Storage), открываемое напрямую — без запуска
полного PromptVault.

Раньше (первая версия этапа 4) кнопка ниже открывала весь MainWindow
PromptVault и сразу поверх него — его SettingsWindow, потому что
казалось, что SettingsWindow нельзя собрать без уже открытого
MainWindow (её конструктор требовал `toolbar: Toolbar`, которого вне
MainWindow взять неоткуда). При ревизии по вопросу пользователя
выяснилось, что это было ложной предпосылкой — `toolbar` внутри самого
SettingsWindow фактически нигде не читался (см. её докстринг в
comfyui_studio/promptvault/ui/settings_window.py), а реальные
зависимости (GenerationRepository, GalleryManager, ThemeManager,
LocalizationManager) дёшевы и не требуют открытой папки/полного окна.
См. comfyui_studio.promptvault.main.create_settings_window() — эта
страница теперь использует именно её, открывая окно настроек напрямую,
без сканирования библиотеки, FolderSync и сетки миниатюр.

Все строки на этой странице -- исходные на русском (см. пояснение в
general_page.py про TRANSLATIONS/loc.tr()).

Часть этапа 4 дорожной карты рефакторинга ("Единое дерево настроек").
"""

from __future__ import annotations

import datetime
import gc
import logging
import os
import shutil
import subprocess
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from comfyui_studio.promptvault.config import DB_PATH

log = logging.getLogger("comfyui_launcher")


class PromptVaultSettingsPage(QWidget):
    def __init__(self, loc=None, parent=None):
        super().__init__(parent)
        self.loc = loc
        # окно настроек PromptVault, открытое ЭТОЙ страницей — кэшируется
        # так же, как окна "Других инструментов" на домашнем экране (см.
        # SettingsPage._open_in_process_window): повторный клик поднимает
        # уже открытое окно, а не плодит новые; при закрытии крестиком
        # C++-объект реально уничтожается (WA_DeleteOnClose) и запись
        # обнуляется (см. _on_settings_window_destroyed).
        self._settings_window = None

        root = QVBoxLayout(self)

        # -- Database (этап 4) -------------------------------------
        db_box = QGroupBox(self._tr("База данных"))
        self.db_box = db_box
        db_layout = QVBoxLayout(db_box)

        self.db_path_label = QLabel(self._tr("Расположение:"))
        db_layout.addWidget(self.db_path_label)

        self.db_path_edit = QLineEdit(str(DB_PATH))
        self.db_path_edit.setReadOnly(True)
        self.db_path_edit.setToolTip(
            self._tr(
                "Только для чтения — перенос базы данных пока не "
                "поддерживается (потребовался бы отдельный шаг миграции, "
                "см. дорожную карту рефакторинга). Здесь всегда лежит "
                "единая, общая база PromptVault, независимо от того, "
                "какая папка открыта в PromptVault в данный момент."
            )
        )
        db_layout.addWidget(self.db_path_edit)

        db_btn_row = QHBoxLayout()
        self.open_folder_btn = QPushButton(self._tr("Открыть папку с файлом"))
        self.open_folder_btn.clicked.connect(self._open_containing_folder)
        db_btn_row.addWidget(self.open_folder_btn)
        self.backup_btn = QPushButton(self._tr("Сделать резервную копию"))
        self.backup_btn.clicked.connect(self._backup_now)
        db_btn_row.addWidget(self.backup_btn)
        db_btn_row.addStretch(1)
        db_layout.addLayout(db_btn_row)

        root.addWidget(db_box)

        # -- Search / Performance / Storage (настоящее окно PromptVault) --
        existing_box = QGroupBox(self._tr("Поиск, производительность и хранение"))
        self.existing_box = existing_box
        existing_layout = QVBoxLayout(existing_box)

        self.existing_hint = QLabel(
            self._tr(
                "Открывает собственное окно настроек PromptVault "
                "(семантический поиск, размер страницы ленивой загрузки, "
                "автоочистка миниатюр/логов, горячие клавиши) — без "
                "запуска самого PromptVault целиком."
            )
        )
        self.existing_hint.setWordWrap(True)
        self.existing_hint.setObjectName("mutedLabel")
        existing_layout.addWidget(self.existing_hint)

        self.open_promptvault_settings_btn = QPushButton(
            self._tr("Открыть настройки PromptVault...")
        )
        self.open_promptvault_settings_btn.clicked.connect(self._open_settings_window)
        existing_layout.addWidget(self.open_promptvault_settings_btn)

        root.addWidget(existing_box)
        root.addStretch(1)

    # -- Database ---------------------------------------------------------

    def _open_containing_folder(self):
        folder = DB_PATH.parent
        os.makedirs(folder, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(folder)  # noqa: S606 -- открытие проводника, не выполнение файла
        else:
            subprocess.Popen(["xdg-open", str(folder)])

    def _backup_now(self):
        if not DB_PATH.exists():
            QMessageBox.warning(
                self,
                self._tr("Сделать резервную копию"),
                self._tr("Файл базы данных пока не найден: {path}.").format(path=DB_PATH),
            )
            return
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = DB_PATH.with_name(f"{DB_PATH.stem}_backup_{timestamp}{DB_PATH.suffix}")
        try:
            shutil.copy2(DB_PATH, backup_path)
        except OSError as e:
            QMessageBox.critical(
                self,
                self._tr("Сделать резервную копию"),
                self._tr("Не удалось сделать резервную копию: {error}").format(error=e),
            )
            return
        QMessageBox.information(
            self,
            self._tr("Сделать резервную копию"),
            self._tr("Резервная копия сохранена: {path}").format(path=backup_path),
        )

    # -- окно настроек PromptVault -----------------------------------------

    def _open_settings_window(self):
        if self._settings_window is None:
            try:
                from comfyui_studio.promptvault.main import create_settings_window

                window = create_settings_window()
            except Exception as e:
                log.exception("Не удалось открыть настройки PromptVault")
                QMessageBox.critical(
                    self,
                    self._tr("Открыть настройки PromptVault..."),
                    self._tr("Не удалось открыть настройки PromptVault: {error}").format(
                        error=e
                    ),
                )
                return

            # соединение с БД (window.standalone_repository, см.
            # create_settings_window) держим отдельной переменной, а не
            # читаем его из window внутри _on_settings_window_destroyed --
            # тот вызывается ПОСЛЕ уничтожения C++-объекта window, читать
            # с него что-либо в этот момент уже нельзя (см. похожий баг,
            # найденный и исправленный ранее в PromptVault.SettingsWindow
            # — dangling-соединение на language_changed_externally).
            repository = window.standalone_repository
            window.setAttribute(Qt.WA_DeleteOnClose, True)
            window.destroyed.connect(
                lambda _obj=None, repo=repository: self._on_settings_window_destroyed(repo)
            )
            self._settings_window = window

        self._settings_window.show()
        self._settings_window.raise_()
        self._settings_window.activateWindow()

    def _on_settings_window_destroyed(self, repository):
        self._settings_window = None
        repository.close()
        # см. аналогичный комментарий в SettingsPage._on_child_window_destroyed
        # (launcher/ui/settings_page.py) -- на объекте окна почти
        # наверняка были цикличные ссылки (сигналы/слоты), обычный
        # refcounting сам по себе не всегда убирает их сразу же.
        gc.collect()
        log.info("Окно настроек PromptVault закрыто, соединение с БД освобождено")

    # -- прочее -----------------------------------------------------

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def retranslate_ui(self):
        self.db_box.setTitle(self._tr("База данных"))
        self.db_path_label.setText(self._tr("Расположение:"))
        self.open_folder_btn.setText(self._tr("Открыть папку с файлом"))
        self.backup_btn.setText(self._tr("Сделать резервную копию"))
        self.existing_box.setTitle(self._tr("Поиск, производительность и хранение"))
        self.existing_hint.setText(
            self._tr(
                "Открывает собственное окно настроек PromptVault "
                "(семантический поиск, размер страницы ленивой загрузки, "
                "автоочистка миниатюр/логов, горячие клавиши) — без "
                "запуска самого PromptVault целиком."
            )
        )
        self.open_promptvault_settings_btn.setText(
            self._tr("Открыть настройки PromptVault...")
        )
