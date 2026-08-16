#!/usr/bin/env python3
"""
ComfyUI Character/Prompt Builder Config Editor (Qt)
====================================================
GUI-редактор для двух файлов расширения character_search_ui:

  - characters.json              — база персонажей (ключ -> теги [+ LoRA])
  - prompt_builder_config.json   — блочный конструктор промпта
                                    (группы/блоки/варианты, макс. рандом,
                                     пресеты качества/источника, негативы)

Построен на PySide6/Qt (как в присланном референсе PromptVault) —
темы оформления взяты из тех же .qss файлов, что и там (themes/*.qss),
и применяются через ThemeManager (themes/theme_manager аналог).

Запуск:  python main.py
Установка зависимостей:  pip install PySide6
"""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QMainWindow, QMessageBox, QTabWidget,
)

from comfyui_studio.prompt_builder.characters_tab import CharactersTab
from comfyui_studio.prompt_builder.json_store import JsonStoreError, load_json, save_json
from comfyui_studio.prompt_builder.promptbuilder_tab import PromptBuilderTab
from comfyui_studio.prompt_builder.theme_manager import ThemeManager, resource_base
from comfyui_studio.prompt_builder.pb_i18n import LocalizationManager
from comfyui_studio.prompt_builder.lora_combo import get_lora_folder, set_lora_folder

CHARACTERS_FILENAME = "characters.json"
PROMPT_BUILDER_FILENAME = "prompt_builder_config.json"

APP_TITLE = "Character / Prompt Builder Config Editor"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1280, 800)
        self.setMinimumSize(980, 600)

        assets_dir = resource_base() / "assets"
        icon_path = assets_dir / "app_icon.png"
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))

        self.theme_manager = ThemeManager()
        self.loc = LocalizationManager()
        self.dirty = {"characters": False, "prompt_builder": False}
        # Та же группа QSettings, что и у ThemeManager — один файл настроек на всё.
        self._settings = QSettings("PromptConfigEditor", "PromptConfigEditor")

        self._build_body()
        self._build_menu()
        self.loc.language_changed_externally.connect(self._on_language_changed_externally)
        self.statusBar().showMessage(self.loc.tr("Файлы не загружены — откройте папку расширения (Ctrl+O)"))

        app = QApplication.instance()
        self.theme_manager.apply_theme(self.theme_manager.current_theme(), app)
        self.loc.apply_language(self.loc.current_language())

        self._refresh_title()
        self._restore_last_folder()

    # ------------------------------------------------------- last folder
    def _last_folder(self) -> str:
        return self._settings.value("last_folder", "", str)

    def _set_last_folder(self, path: str):
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        if folder:
            self._settings.setValue("last_folder", folder)

    def _dialog_start_dir(self) -> str:
        folder = self._last_folder()
        return folder if folder and os.path.isdir(folder) else ""

    def _restore_last_folder(self):
        """Тихо подхватывает файлы из папки, открытой в прошлый раз —
        без диалогов об ошибке, если папки/файлов уже нет на месте."""
        folder = self._last_folder()
        if not folder or not os.path.isdir(folder):
            return
        chars_path = os.path.join(folder, CHARACTERS_FILENAME)
        pb_path = os.path.join(folder, PROMPT_BUILDER_FILENAME)
        if os.path.isfile(chars_path):
            self._load_characters(chars_path, quiet=True)
        if os.path.isfile(pb_path):
            self._load_prompt_builder(pb_path, quiet=True)
        if self.characters_tab.has_data():
            self.tabs.setCurrentWidget(self.characters_tab)
        self._refresh_title()

    # --------------------------------------------------------------- UI
    def _build_body(self):
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.characters_tab = CharactersTab(on_dirty=lambda: self._set_dirty("characters"), loc=self.loc)
        self.prompt_builder_tab = PromptBuilderTab(on_dirty=lambda: self._set_dirty("prompt_builder"), loc=self.loc)

        self.tabs.addTab(self.characters_tab, self.loc.tr("  Персонажи (characters.json)  "))
        self.tabs.addTab(self.prompt_builder_tab, self.loc.tr("  Конструктор промпта (prompt_builder_config.json)  "))
        self.tabs.currentChanged.connect(lambda _i: self._refresh_title())

    def _build_menu(self):
        menubar = self.menuBar()
        menubar.clear()

        tr = self.loc.tr

        file_menu = menubar.addMenu(tr("Файл"))
        self.act_open_folder = file_menu.addAction(tr("Открыть папку расширения..."), self.open_folder, "Ctrl+O")
        file_menu.addSeparator()
        self.act_open_characters = file_menu.addAction(tr("Открыть characters.json..."), self.open_characters_file)
        self.act_open_pb = file_menu.addAction(tr("Открыть prompt_builder_config.json..."), self.open_prompt_builder_file)
        file_menu.addSeparator()
        self.act_choose_lora_folder = file_menu.addAction(
            tr("Указать папку с файлами LoRA..."), self._choose_lora_folder
        )
        file_menu.addSeparator()
        self.act_save_current = file_menu.addAction(tr("Сохранить текущую вкладку"), self.save_current, "Ctrl+S")
        self.act_save_as = file_menu.addAction(tr("Сохранить как..."), self.save_current_as)
        self.act_save_all = file_menu.addAction(tr("Сохранить всё"), self.save_all)
        file_menu.addSeparator()
        self.act_quit = file_menu.addAction(tr("Выход"), self.close)
        self.file_menu = file_menu

        # Тема оформления и язык интерфейса теперь общие на весь комплект
        # ComfyUI Studio и настраиваются из лаунчера (см. README) — здесь
        # своего переключателя больше нет, только применение (ThemeManager/
        # LocalizationManager следят за общими файлами комплекта).

        help_menu = menubar.addMenu(tr("Справка"))
        self.act_about = help_menu.addAction(tr("О программе"), self._show_about)
        self.help_menu = help_menu

    def retranslate_ui(self):
        """Перевыставляет уже построенные тексты меню и вкладок после
        смены языка — сам факт выбора языка не обновляет текст уже
        созданных виджетов."""
        tr = self.loc.tr

        self.file_menu.setTitle(tr("Файл"))
        self.act_open_folder.setText(tr("Открыть папку расширения..."))
        self.act_open_characters.setText(tr("Открыть characters.json..."))
        self.act_open_pb.setText(tr("Открыть prompt_builder_config.json..."))
        self.act_choose_lora_folder.setText(tr("Указать папку с файлами LoRA..."))
        self.act_save_current.setText(tr("Сохранить текущую вкладку"))
        self.act_save_as.setText(tr("Сохранить как..."))
        self.act_save_all.setText(tr("Сохранить всё"))
        self.act_quit.setText(tr("Выход"))

        self.help_menu.setTitle(tr("Справка"))
        self.act_about.setText(tr("О программе"))

        self.tabs.setTabText(0, tr("  Персонажи (characters.json)  "))
        self.tabs.setTabText(1, tr("  Конструктор промпта (prompt_builder_config.json)  "))
        self.characters_tab.retranslate_ui()
        self.prompt_builder_tab.retranslate_ui()

        self._refresh_title()

    def _on_language_changed_externally(self, _code):
        """Язык поменялся в ComfyUI Launcher или PromptVault, пока это
        приложение уже открыто — applying уже сделан в LocalizationManager,
        здесь только перевыставляем уже показанные тексты."""
        self.retranslate_ui()

    def _show_about(self):
        QMessageBox.information(
            self, APP_TITLE,
            self.loc.tr(
                "Редактор конфигов для расширения ComfyUI character_search_ui.\n\n"
                "Редактирует:\n"
                " • characters.json — база персонажей\n"
                " • prompt_builder_config.json — блочный конструктор промпта\n\n"
                "Перед каждым сохранением создаётся резервная копия (*.bak-...)."
            ),
        )

    # ------------------------------------------------------------ state
    def _current_tab_key(self) -> str:
        return "characters" if self.tabs.currentIndex() == 0 else "prompt_builder"

    def _current_tab(self):
        return self.characters_tab if self._current_tab_key() == "characters" else self.prompt_builder_tab

    def _set_dirty(self, key: str):
        self.dirty[key] = True
        self._refresh_title()

    def _refresh_title(self):
        key = self._current_tab_key()
        tab = self._current_tab()
        name = CHARACTERS_FILENAME if key == "characters" else PROMPT_BUILDER_FILENAME
        star = " *" if self.dirty.get(key) else ""
        path_part = f" — {tab.path}" if getattr(tab, "path", None) else ""
        self.setWindowTitle(f"{APP_TITLE} — {name}{star}{path_part}")
        self._update_status()

    def _update_status(self):
        parts = [
            f"characters.json: {self.characters_tab.path or 'не открыт'}"
            f"{' *' if self.dirty['characters'] else ''}",
            f"prompt_builder_config.json: {self.prompt_builder_tab.path or 'не открыт'}"
            f"{' *' if self.dirty['prompt_builder'] else ''}",
        ]
        self.statusBar().showMessage("   |   ".join(parts))

    # ------------------------------------------------------------ open
    def open_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Выберите папку расширения (с characters.json и prompt_builder_config.json)",
            self._dialog_start_dir())
        if not folder:
            return
        self._set_last_folder(folder)
        chars_path = os.path.join(folder, CHARACTERS_FILENAME)
        pb_path = os.path.join(folder, PROMPT_BUILDER_FILENAME)

        opened_any = False
        if os.path.isfile(chars_path):
            self._load_characters(chars_path)
            opened_any = True
        if os.path.isfile(pb_path):
            self._load_prompt_builder(pb_path)
            opened_any = True

        if not opened_any:
            QMessageBox.warning(
                self, "Файлы не найдены",
                f"В папке не найдено ни {CHARACTERS_FILENAME}, ни {PROMPT_BUILDER_FILENAME}.\n"
                "Откройте файлы по отдельности через меню «Файл».",
            )

    def _choose_lora_folder(self):
        chosen = QFileDialog.getExistingDirectory(
            self, self.loc.tr("Папка с файлами LoRA"), get_lora_folder()
        )
        if chosen:
            set_lora_folder(chosen)

    def open_characters_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Открыть characters.json", self._dialog_start_dir(), "JSON (*.json);;Все файлы (*)")
        if path:
            self._set_last_folder(path)
            self._load_characters(path)

    def open_prompt_builder_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Открыть prompt_builder_config.json", self._dialog_start_dir(), "JSON (*.json);;Все файлы (*)")
        if path:
            self._set_last_folder(path)
            self._load_prompt_builder(path)

    def _load_characters(self, path: str, quiet: bool = False):
        try:
            raw = load_json(path)
        except JsonStoreError as e:
            if quiet:
                self.statusBar().showMessage(f"Не удалось автоматически открыть characters.json: {e}", 6000)
            else:
                QMessageBox.critical(self, "Ошибка загрузки", str(e))
            return
        if not isinstance(raw, dict):
            if not quiet:
                QMessageBox.critical(self, "Ошибка загрузки", "characters.json должен содержать JSON-объект (словарь).")
            return
        self.characters_tab.load(path, raw)
        self.dirty["characters"] = False
        if not quiet:
            self.tabs.setCurrentWidget(self.characters_tab)
        self._refresh_title()

    def _load_prompt_builder(self, path: str, quiet: bool = False):
        try:
            raw = load_json(path)
        except JsonStoreError as e:
            if quiet:
                self.statusBar().showMessage(f"Не удалось автоматически открыть prompt_builder_config.json: {e}", 6000)
            else:
                QMessageBox.critical(self, "Ошибка загрузки", str(e))
            return
        if not isinstance(raw, dict):
            if not quiet:
                QMessageBox.critical(self, "Ошибка загрузки", "prompt_builder_config.json должен содержать JSON-объект.")
            return
        self.prompt_builder_tab.load(path, raw)
        self.dirty["prompt_builder"] = False
        if not quiet:
            self.tabs.setCurrentWidget(self.prompt_builder_tab)
        self._refresh_title()

    # ------------------------------------------------------------ save
    def save_current(self):
        self._save_tab(self._current_tab_key())

    def save_current_as(self):
        key = self._current_tab_key()
        default_name = CHARACTERS_FILENAME if key == "characters" else PROMPT_BUILDER_FILENAME
        start = os.path.join(self._dialog_start_dir(), default_name) if self._dialog_start_dir() else default_name
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить как", start, "JSON (*.json)")
        if path:
            self._set_last_folder(path)
            self._save_tab(key, path)

    def save_all(self):
        for key in ("characters", "prompt_builder"):
            tab = self.characters_tab if key == "characters" else self.prompt_builder_tab
            if tab.has_data():
                self._save_tab(key)

    def _save_tab(self, key: str, path: str | None = None):
        tab = self.characters_tab if key == "characters" else self.prompt_builder_tab
        if not tab.has_data():
            QMessageBox.information(self, "Нечего сохранять", "Сначала откройте файл.")
            return
        target_path = path or tab.path
        try:
            data = tab.to_raw()
            save_json(target_path, data)
        except JsonStoreError as e:
            QMessageBox.critical(self, "Ошибка сохранения", str(e))
            return
        if path:
            tab.path = path
        self.dirty[key] = False
        self._refresh_title()
        self.statusBar().showMessage(f"Сохранено: {target_path}", 3000)

    # ------------------------------------------------------------ close
    def closeEvent(self, event):
        unsaved = [k for k, v in self.dirty.items() if v]
        if unsaved:
            names = ", ".join(CHARACTERS_FILENAME if k == "characters" else PROMPT_BUILDER_FILENAME for k in unsaved)
            reply = QMessageBox.question(
                self, "Есть несохранённые изменения",
                f"Не сохранены изменения в: {names}.\nВыйти без сохранения?",
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
        event.accept()


def create_window() -> MainWindow:
    """Создаёт и возвращает главное окно редактора, не трогая
    QApplication/цикл событий -- используется как при самостоятельном
    запуске (main() ниже), так и из монолитного ComfyUIStudio (см.
    корневой main.py), где QApplication уже создан заранее и общий на
    все три инструмента комплекта."""
    return MainWindow()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("PromptConfigEditor")
    app.setOrganizationName("PromptConfigEditor")
    window = create_window()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
