"""Единое дерево настроек ComfyUI Studio -- этап 4 дорожной карты
рефакторинга.

QTreeWidget слева (General / ComfyUI / Prompt Builder / PromptVault /
Prompt Generator / Prompt History / Remote / Advanced) + QStackedWidget справа с соответствующими страницами
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

from PySide6.QtCore import QPoint, QRect, QSize, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QScrollArea,
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
from .prompt_generator_page import PromptGeneratorSettingsPage
from .prompt_history_page import PromptHistorySettingsPage
from .promptvault_page import PromptVaultSettingsPage
from .remote_page import RemoteSettingsPage


# Желаемый размер окна при открытии и доля доступной области экрана, за
# которую он выходить не должен (см. initial_geometry()).
PREFERRED_SIZE = QSize(900, 640)
MAX_SCREEN_FRACTION = 0.85
MIN_DIALOG_SIZE = QSize(480, 320)


def initial_geometry(
    available: QRect, preferred: QSize, anchor_center: QPoint | None = None,
    fraction: float = MAX_SCREEN_FRACTION,
) -> QRect:
    """Размер и позиция окна при ПЕРВОМ показе: желаемый размер, но не
    больше `fraction` доступной области экрана (без панели задач и т. п.);
    окно центрируется на `anchor_center` (центр родительского окна), а
    если его нет -- на экране, и целиком остаётся внутри `available`.

    Чистая функция без Qt-виджетов, чтобы её можно было проверить на
    любых «экранах». Ограничение действует только на стартовый размер:
    максимальный размер окна нигде не задаётся, и пользователь потом
    свободно растягивает его хоть за пределы экрана."""
    width = max(1, min(preferred.width(), int(available.width() * fraction)))
    height = max(1, min(preferred.height(), int(available.height() * fraction)))
    center = anchor_center if anchor_center is not None else available.center()
    left = center.x() - width // 2
    top = center.y() - height // 2
    left = max(available.left(), min(left, available.right() + 1 - width))
    top = max(available.top(), min(top, available.bottom() + 1 - height))
    return QRect(left, top, width, height)


def _has_own_scroll(page: QWidget) -> bool:
    """Прокручивается ли страница уже сама (ComfyUI, Генератор промптов
    строят себя внутри собственного QScrollArea)."""
    layout = page.layout()
    if layout is None:
        return False
    return any(
        isinstance(layout.itemAt(i).widget(), QScrollArea) for i in range(layout.count())
    )


def _scrollable(page: QWidget) -> QWidget:
    """Оборачивает страницу в QScrollArea (так же, как на странице
    ComfyUI): если окно меньше страницы, появляется прокрутка, а не
    обрезанное содержимое. Страницы со своей прокруткой не трогаем --
    двойной скролл не нужен."""
    if _has_own_scroll(page):
        return page
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.NoFrame)
    scroll.setWidget(page)
    return scroll


class AppSettingsDialog(QDialog):

    language_changed = Signal(str)
    # НОВОЕ: Studio-wide выход/перезапуск -- ретранслируются наружу из
    # AdvancedSettingsPage.quit_requested/restart_requested, на них
    # подписывается SettingsPage (см. ../settings_page.py), а оттуда --
    # MainWindow.quit_studio()/restart_studio() (launcher_window.py).
    quit_studio_requested = Signal()
    restart_studio_requested = Signal()
    # НОВОЕ (Remote, этап 1 дорожной карты): этот диалог сам не
    # управляет процессом Remote и не делает HTTP-вызовов (см. докстринг
    # RemoteSettingsPage) -- просто ретранслирует его сигналы наружу,
    # ровно как quit_studio_requested/restart_studio_requested выше;
    # MainWindow (launcher_window.py) — единственный владелец
    # RemoteProcess.
    remote_enable_toggled = Signal(bool)
    remote_pairing_requested = Signal()
    remote_refresh_devices_requested = Signal()
    remote_revoke_requested = Signal(list)

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
        # Стартовый размер подгоняется под экран при первом показе (см.
        # showEvent); здесь только желаемый -- таблице истории нужно
        # больше места, чем формам. Максимум не задаём: пользователь
        # волен растянуть окно как угодно.
        self.resize(PREFERRED_SIZE)
        self.setMinimumSize(MIN_DIALOG_SIZE)
        self._geometry_fitted = False

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
        # Генератор промптов (кнопка в Imagine, llama.cpp): пишет настройки
        # в общий файл %APPDATA%\ComfyUIStudio\prompt_generator.json, а не
        # в cfg -- см. докстринг PromptGeneratorSettingsPage.
        self.prompt_generator_page = PromptGeneratorSettingsPage(loc, parent=self)
        # История запросов к генератору (SQLite, пишет Imagine): страница
        # только читает базу, в cfg/файл настроек ничего не пишет.
        self.prompt_history_page = PromptHistorySettingsPage(loc, parent=self)
        self.advanced_page = AdvancedSettingsPage(cfg, loc, parent=self)
        self.remote_page = RemoteSettingsPage(cfg, loc, parent=self)

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
            (self._tr("Генератор промптов"), self.prompt_generator_page),
            (self._tr("История промптов"), self.prompt_history_page),
            (self._tr("Удалённый доступ"), self.remote_page),
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
        self.remote_page.changed.connect(self._schedule_autosave)
        self.prompt_generator_page.changed.connect(self._schedule_autosave)
        self.remote_page.enable_toggled.connect(self.remote_enable_toggled.emit)
        self.remote_page.pairing_requested.connect(self.remote_pairing_requested.emit)
        self.remote_page.refresh_devices_requested.connect(
            self.remote_refresh_devices_requested.emit
        )
        self.remote_page.revoke_requested.connect(self.remote_revoke_requested.emit)

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
        self.stack.addWidget(_scrollable(page))

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
        cfg.update(self.remote_page.collect())
        self.cfg = cfg
        self.comfyui_page.cfg = cfg
        self.advanced_page.cfg = cfg
        save_config(cfg)
        # Генератор промптов хранит настройки в своём общем файле, не в cfg
        self.prompt_generator_page.save()
        log.debug("Настройки автосохранены (единое дерево настроек)")

    def showEvent(self, event):
        """При первом показе вписывает окно в экран, на котором оно
        открывается. Повторные показы (диалог немодальный и живёт всё время
        работы лаунчера) размер и позицию не трогают -- иначе сбрасывали бы
        то, что пользователь уже подвинул или растянул."""
        if not self._geometry_fitted:
            self._geometry_fitted = True
            self._fit_to_screen()
        super().showEvent(event)

    def _fit_to_screen(self):
        parent = self.parentWidget()
        window = parent.window() if parent is not None else None
        screen = (window.screen() if window is not None else None) or self.screen() \
            or QGuiApplication.primaryScreen()
        if screen is None:
            return
        anchor = None
        if window is not None and window.isVisible():
            anchor = window.frameGeometry().center()
        # запас под рамку и заголовок окна: frameGeometry до показа ещё
        # равна geometry, поэтому берём долю экрана с запасом (см. fraction)
        self.setGeometry(initial_geometry(screen.availableGeometry(), self.size(), anchor))

    def hideEvent(self, event):
        """Не теряем правки, сделанные за последние AUTOSAVE_DEBOUNCE_MS
        перед закрытием окна: без этого путь, вписанный и тут же закрытый
        диалогом, не успевал сохраниться. hideEvent, а не closeEvent --
        Esc у QDialog скрывает окно через reject() без closeEvent."""
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._auto_save()
        super().hideEvent(event)

    # -- Remote (этап 1 дорожной карты): чистая ретрансляция вызовов от
    # launcher_window.py вниз, к RemoteSettingsPage -- сам этот диалог
    # не хранит никакого состояния Remote, см. докстринг класса ---------

    def set_remote_running_state(self, running: bool, error: str | None = None) -> None:
        self.remote_page.set_running_state(running, error)

    def show_lan_url(self, url: str | None) -> None:
        self.remote_page.show_lan_url(url)

    def show_remote_pairing_code(self, code: str, expires_at_text: str, attempts_left: int) -> None:
        self.remote_page.show_pairing_code(code, expires_at_text, attempts_left)

    def show_remote_pairing_error(self, message: str) -> None:
        self.remote_page.show_pairing_error(message)

    def set_remote_devices(self, devices: list) -> None:
        self.remote_page.set_devices(devices)

    def show_remote_devices_error(self, message: str) -> None:
        self.remote_page.show_devices_error(message)

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
            self._tr("Генератор промптов"),
            self._tr("История промптов"),
            self._tr("Удалённый доступ"),
            self._tr("Дополнительно"),
        ]
        for item, title in zip(self._tree_items, titles):
            item.setText(0, title)
        self.general_page.retranslate_ui()
        self.comfyui_page.retranslate_ui()
        self.prompt_builder_page.retranslate_ui()
        self.promptvault_page.retranslate_ui()
        self.prompt_generator_page.retranslate_ui()
        self.prompt_history_page.retranslate_ui()
        self.remote_page.retranslate_ui()
        self.advanced_page.retranslate_ui()
