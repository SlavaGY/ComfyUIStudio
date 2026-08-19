"""Единое дерево настроек ComfyUI Studio -- этап 4 дорожной карты
рефакторинга.

QTreeWidget слева (General / ComfyUI / Prompt Builder / PromptVault /
Advanced) + QStackedWidget справа с соответствующими страницами
(см. соседние *_page.py в этом же пакете). Раньше всё это было одним
плоским QFormLayout прямо на главном экране лаунчера (SettingsPage,
см. ../settings_page.py) -- теперь SettingsPage остаётся "домашним"
экраном лаунчера (запуск/лог/статус/другие инструменты) и просто
открывает этот диалог кнопкой "Настройки...".

НЕмодальный диалог (show()/raise(), не exec() -- см. SettingsPage.
_open_settings_dialog): изначально был модальным, но это оказалось
багом -- окно PromptVault, открываемое кнопкой "Открыть настройки
PromptVault..." (см. promptvault_page.py) прямо из этого диалога,
оказывалось заблокировано и визуально пряталось ЗА модальным диалогом
настроек лаунчера, взаимодействовать с ним можно было только закрыв
диалог настроек лаунчера. Немодальный диалог ведёт себя так же, как и
собственное окно настроек PromptVault (тоже show()/raise()) -- оба
могут быть открыты одновременно.

Автосохранение: тот же debounce-паттерн, что был в SettingsPage
(AUTOSAVE_DEBOUNCE_MS) -- изменения на страницах ComfyUI/Advanced не
пишутся в config.json на каждое нажатие клавиши, а откладываются на
короткую паузу. Тема/язык (General) применяются и сохраняются
немедленно самими ThemeManager/LocalizationManager, как и раньше --
это НЕ часть cfg/config.json и не проходит через этот debounce.

Все строки на этой странице -- исходные на русском (см. пояснение в
general_page.py про TRANSLATIONS/loc.tr()).
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from comfyui_studio.themes.theme_manager import ThemeManager

from ...core.config import save_config
from ...core.logging_setup import log
from .advanced_page import AdvancedSettingsPage
from .comfyui_page import ComfyUISettingsPage
from .general_page import GeneralSettingsPage
from .prompt_builder_page import PromptBuilderSettingsPage
from .promptvault_page import PromptVaultSettingsPage


class AppSettingsDialog(QDialog):

    language_changed = Signal(str)
    # НОВОЕ: Studio-wide выход/перезапуск -- ретранслируются наружу из
    # AdvancedSettingsPage.quit_requested/restart_requested, на них
    # подписывается SettingsPage (см. ../settings_page.py), а оттуда --
    # MainWindow.quit_studio()/restart_studio() (launcher_window.py).
    quit_studio_requested = Signal()
    restart_studio_requested = Signal()

    AUTOSAVE_DEBOUNCE_MS = 400

    def __init__(
        self,
        cfg: dict,
        theme_manager: ThemeManager,
        loc=None,
        parent=None,
    ):
        super().__init__(parent)
        self.cfg = cfg
        self.loc = loc
        self.setWindowTitle(self._tr("Настройки ComfyUI Studio"))
        self.resize(760, 560)

        outer = QVBoxLayout(self)
        body = QHBoxLayout()
        outer.addLayout(body, 1)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setFixedWidth(200)
        body.addWidget(self.tree)

        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)

        self.general_page = GeneralSettingsPage(cfg, theme_manager, loc, parent=self)
        self.comfyui_page = ComfyUISettingsPage(cfg, loc, parent=self)
        self.prompt_builder_page = PromptBuilderSettingsPage(loc, parent=self)
        # PromptVaultSettingsPage сама открывает свою настоящую SettingsWindow
        # напрямую (см. comfyui_studio.promptvault.main.create_settings_window)
        # -- моста через полноценный MainWindow PromptVault больше не нужно,
        # см. её докстринг.
        self.promptvault_page = PromptVaultSettingsPage(loc, parent=self)
        self.advanced_page = AdvancedSettingsPage(cfg, loc, parent=self)

        # (заголовок дерева, страница) -- см. _add_section ниже; заголовки
        # "ComfyUI"/"Prompt Builder"/"PromptVault" не переводятся -- это
        # названия конкретных инструментов комплекта, одинаковые в любом
        # языке интерфейса (как и везде в этом приложении, см. например
        # ярлыки EXTERNAL_APPS).
        self._sections = [
            (self._tr("Общие"), self.general_page),
            ("ComfyUI", self.comfyui_page),
            ("Prompt Builder", self.prompt_builder_page),
            ("PromptVault", self.promptvault_page),
            (self._tr("Дополнительно"), self.advanced_page),
        ]
        self._tree_items: list[QTreeWidgetItem] = []
        for title, page in self._sections:
            self._add_section(title, page)

        self.tree.currentItemChanged.connect(self._on_tree_selection_changed)
        self.tree.setCurrentItem(self._tree_items[0])

        self.general_page.language_changed.connect(self._on_language_changed)
        self.comfyui_page.changed.connect(self._schedule_autosave)
        self.advanced_page.changed.connect(self._schedule_autosave)
        self.advanced_page.reset_confirmed.connect(self._on_reset_confirmed)
        self.advanced_page.quit_requested.connect(self._on_quit_requested)
        self.advanced_page.restart_requested.connect(self._on_restart_requested)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(self.AUTOSAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self._auto_save)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        self.close_btn = QPushButton(self._tr("Закрыть"))
        self.close_btn.clicked.connect(self.close)
        close_row.addWidget(self.close_btn)
        outer.addLayout(close_row)

    def _add_section(self, title, page: QWidget):
        item = QTreeWidgetItem([title])
        self.tree.addTopLevelItem(item)
        self._tree_items.append(item)
        self.stack.addWidget(page)

    def _on_tree_selection_changed(self, current, _previous):
        index = self._tree_items.index(current)
        self.stack.setCurrentIndex(index)

    # -- запуск/остановка сервера: часть полей ComfyUI нельзя менять,
    # пока сервер уже работает (перенесено из старого
    # SettingsPage.set_server_running) ------------------------------

    def set_running_state(self, running: bool) -> None:
        self.comfyui_page.set_editable(not running)

    # -- автосохранение (ComfyUI/Advanced страницы) -----------------------

    def _schedule_autosave(self, *_args):
        self._save_timer.start()

    def _auto_save(self):
        cfg = dict(self.cfg)
        cfg.update(self.comfyui_page.collect())
        cfg.update(self.advanced_page.collect())
        self.cfg = cfg
        self.comfyui_page.cfg = cfg
        self.advanced_page.cfg = cfg
        save_config(cfg)
        log.debug("Настройки автосохранены (единое дерево настроек)")

    # -- язык -------------------------------------------------------------

    def _on_language_changed(self, code):
        self.retranslate_ui()
        self.language_changed.emit(code)

    # -- сброс к дефолту (Advanced) ----------------------------------------

    def _on_reset_confirmed(self):
        """config.json уже перезаписан значениями по умолчанию (см.
        AdvancedSettingsPage._on_reset_clicked) -- здесь только просим
        пользователя перезапустить приложение, а не пытаемся откатить
        уже построенные виджеты каждой страницы вживую (риск
        рассинхронизации заметно выше пользы: путь/скрипт/аргументы/
        env-переменные/тема/язык -- у каждого свой набор виджетов и
        сигналов, надёжнее просто перечитать всё заново при следующем
        старте)."""

        QMessageBox.information(
            self,
            self._tr("Сброс настроек лаунчера"),
            self._tr(
                "Настройки лаунчера сброшены. Перезапустите ComfyUI Studio, "
                "чтобы изменения вступили в силу полностью."
            ),
        )

    # -- выход/перезапуск всей Studio (Advanced -> Application) -----------

    def _on_quit_requested(self):
        # закрываем сам диалог настроек первым, а не оставляем его висеть
        # поверх исчезающего главного окна на время анимации/задержки
        # закрытия (см. MainWindow.closeEvent)
        self.close()
        self.quit_studio_requested.emit()

    def _on_restart_requested(self):
        self.close()
        self.restart_studio_requested.emit()

    # -- прочее -----------------------------------------------------

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def retranslate_ui(self):
        self.setWindowTitle(self._tr("Настройки ComfyUI Studio"))
        self.close_btn.setText(self._tr("Закрыть"))
        titles = [
            self._tr("Общие"),
            "ComfyUI",
            "Prompt Builder",
            "PromptVault",
            self._tr("Дополнительно"),
        ]
        for item, title in zip(self._tree_items, titles):
            item.setText(0, title)
        self.general_page.retranslate_ui()
        self.comfyui_page.retranslate_ui()
        self.prompt_builder_page.retranslate_ui()
        self.promptvault_page.retranslate_ui()
        self.advanced_page.retranslate_ui()
