"""Host LM Studio's original Windows HWND in the Studio page stack."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QWindow
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class LmStudioPage(QWidget):
    settings_requested = Signal()
    stop_requested = Signal()

    def __init__(self, loc=None, parent=None):
        super().__init__(parent)
        self.loc = loc
        self._window = None
        self._container = None
        self._hwnd = None
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        toolbar = QWidget()
        row = QHBoxLayout(toolbar)
        row.addWidget(QLabel("LM Studio"))
        row.addStretch(1)
        self.settings_btn = QPushButton()
        self.stop_btn = QPushButton()
        row.addWidget(self.settings_btn)
        row.addWidget(self.stop_btn)
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        self._layout.addWidget(toolbar)
        self.retranslate_ui()

    def load(self, hwnd):
        if self._hwnd == hwnd:
            return
        self.unload()
        window = QWindow.fromWinId(hwnd)
        if window is None:
            raise RuntimeError("QWindow.fromWinId failed")
        self._window = window
        self._hwnd = hwnd
        self._container = QWidget.createWindowContainer(window, self)
        self._container.setFocusPolicy(Qt.StrongFocus)
        self._layout.addWidget(self._container, 1)

    def unload(self):
        if self._window is not None:
            self._window.hide()
            self._window.setParent(None)
            self._window.deleteLater()
            self._window = None
        if self._container is not None:
            self._layout.removeWidget(self._container)
            self._container.hide()
            self._container.deleteLater()
            self._container = None
        self._hwnd = None

    def retranslate_ui(self):
        tr = self.loc.tr if self.loc is not None else lambda text: text
        self.settings_btn.setText(tr("← Настройки"))
        self.stop_btn.setText(tr("⏹ Остановить"))
