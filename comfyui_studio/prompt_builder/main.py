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

from PySide6.QtGui import QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QMainWindow, QMessageBox, QTabWidget, QToolBar,
)

from comfyui_studio.prompt_builder.characters_tab import CharactersTab
from comfyui_studio.prompt_builder.json_store import JsonStoreError, load_json, save_json
from comfyui_studio.prompt_builder.promptbuilder_tab import PromptBuilderTab
from comfyui_studio.prompt_builder.theme_manager import ThemeManager, resource_base
from comfyui_studio.prompt_builder.pb_i18n import LocalizationManager
from comfyui_studio.prompt_builder.pb_settings import get_extension_folder

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

        self._build_body()
        self._build_toolbar()
        self.loc.language_changed_externally.connect(self._on_language_changed_externally)
        self.statusBar().showMessage(
            self.loc.tr(
                "Файлы не загружены — укажите папку расширения в "
                "настройках ComfyUI Studio, либо откройте файл (Ctrl+O)"
            )
        )

        app = QApplication.instance()
        self.theme_manager.apply_theme(self.theme_manager.current_theme(), app)
        self.loc.apply_language(self.loc.current_language())

        self._refresh_title()
        self._load_from_extension_folder()

    # ------------------------------------------------------ extension folder
    def _dialog_start_dir(self) -> str:
        folder = get_extension_folder()
        return folder if folder and os.path.isdir(folder) else ""

    def _load_from_extension_folder(self):
        """Тихо подхватывает файлы из папки расширения, настроенной в
        едином дереве настроек ComfyUI Studio (см. pb_settings.
        get_extension_folder(), ui/settings/prompt_builder_page.py в
        лаунчере) — без диалогов об ошибке, если папки/файлов уже нет
        на месте. Раньше эта папка запоминалась изнутри самого редактора
        (меню "Файл -> Открыть папку расширения...") — теперь это чистое
        чтение уже готовой настройки, сам редактор её не меняет (см.
        докстринг pb_settings.py)."""
        folder = get_extension_folder()
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

    def _build_toolbar(self):
        """Тулбар из двух кнопок вместо прежнего меню "Файл"/"Справка"
        (см. докстринг pb_settings.py) — папки (расширения, LoRA) и
        число бэкапов теперь настраиваются извне, из единого дерева
        настроек ComfyUI Studio, а не изнутри самого редактора; "Открыть
        characters.json.../prompt_builder_config.json..." (два разных
        диалога) заменены одной кнопкой "Открыть файл..." (см.
        open_existing_file() ниже — сама определяет, в какую вкладку
        грузить, по имени файла). "Сохранить текущую вкладку"/"Сохранить
        как..."/"О программе"/"Выход" убраны как избыточные: "Сохранить
        всё" покрывает основной сценарий (в редакторе всего два
        файла-цели с фиксированными именами), а "Выход" ничем не
        отличался от обычного закрытия окна (closeEvent ниже и так
        спрашивает про несохранённые изменения)."""

        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        self.addToolBar(toolbar)

        tr = self.loc.tr

        self.act_save_all = toolbar.addAction(tr("💾 Сохранить всё"), self.save_all)
        self.act_save_all.setShortcut(QKeySequence.Save)  # Ctrl+S
        self.act_open_existing = toolbar.addAction(
            tr("📂 Открыть файл..."), self.open_existing_file
        )
        self.act_open_existing.setShortcut(QKeySequence.Open)  # Ctrl+O

        self.toolbar = toolbar

    def retranslate_ui(self):
        """Перевыставляет уже построенные тексты тулбара и вкладок после
        смены языка — сам факт выбора языка не обновляет текст уже
        созданных виджетов."""
        tr = self.loc.tr

        self.act_save_all.setText(tr("💾 Сохранить всё"))
        self.act_open_existing.setText(tr("📂 Открыть файл..."))

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
    def open_existing_file(self):
        """Заменяет прежние три отдельных диалога ("Открыть папку
        расширения...", "Открыть characters.json...", "Открыть
        prompt_builder_config.json...", все были в меню "Файл", см.
        докстринг _build_toolbar) одним. Папка расширения теперь только
        настраивается (см. pb_settings.get_extension_folder()) — этот
        диалог её не меняет и не запоминает, даже если выбранный файл
        лежит в другой папке: начальная папка диалога подсказывается из
        неё же, но открытие произвольного файла не должно неявно
        менять настройку, которую пользователь задал через настройки
        ComfyUI Studio."""
        path, _ = QFileDialog.getOpenFileName(
            self, self.loc.tr("Открыть файл"), self._dialog_start_dir(),
            "JSON (*.json);;Все файлы (*)",
        )
        if not path:
            return

        name = os.path.basename(path).lower()
        if name == CHARACTERS_FILENAME.lower():
            self._load_characters(path)
            return
        if name == PROMPT_BUILDER_FILENAME.lower():
            self._load_prompt_builder(path)
            return

        # Имя файла не совпадает ни с одним из двух ожидаемых — не
        # угадываем по содержимому, просто спрашиваем прямо.
        reply = QMessageBox.question(
            self,
            self.loc.tr("В какую вкладку загрузить?"),
            self.loc.tr(
                'Файл "{name}" не похож по имени ни на {characters}, ни '
                "на {prompt_builder}.\n\nЗагрузить его как вкладку "
                '"Персонажи"? ("Нет" — загрузить как "Конструктор промпта")'
            ).format(
                name=os.path.basename(path),
                characters=CHARACTERS_FILENAME,
                prompt_builder=PROMPT_BUILDER_FILENAME,
            ),
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if reply == QMessageBox.Yes:
            self._load_characters(path)
        elif reply == QMessageBox.No:
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
