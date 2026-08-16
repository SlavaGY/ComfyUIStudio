"""
Иконка в системном трее.

Вынесено из comfyui_launcher.py (этап 1 дорожной карты).
"""

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..core.constants import APP_NAME
from ..core.system_monitor import format_stats_tooltip


class TrayIcon(QSystemTrayIcon):
    show_window_requested = Signal()
    stop_requested = Signal()
    quit_requested = Signal()

    def __init__(self, icon: QIcon, loc=None, parent=None):
        super().__init__(icon, parent)
        self.loc = loc
        self.setToolTip(APP_NAME)

        self.menu = QMenu()
        self.show_action = QAction(self._tr("Показать окно"), self.menu)
        self.show_action.triggered.connect(self.show_window_requested.emit)
        self.menu.addAction(self.show_action)

        self.stop_action = QAction(self._tr("Остановить ComfyUI"), self.menu)
        self.stop_action.triggered.connect(self.stop_requested.emit)
        self.menu.addAction(self.stop_action)

        self.menu.addSeparator()

        self.quit_action = QAction(self._tr("Выход"), self.menu)
        self.quit_action.triggered.connect(self.quit_requested.emit)
        self.menu.addAction(self.quit_action)

        self.setContextMenu(self.menu)
        self.activated.connect(self._on_activated)

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def retranslate_ui(self):
        self.show_action.setText(self._tr("Показать окно"))
        self.stop_action.setText(self._tr("Остановить ComfyUI"))
        self.quit_action.setText(self._tr("Выход"))

    def _on_activated(self, reason):
        if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger):
            self.show_window_requested.emit()

    def update_stats(self, stats: dict):
        self.setToolTip(format_stats_tooltip(stats, tr=self._tr))


# --------------------------------------------------------------------------
# Главное окно
# --------------------------------------------------------------------------


