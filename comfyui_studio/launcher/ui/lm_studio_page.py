"""Host LM Studio's original Windows HWND in the Studio page stack."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QWindow
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..core.native_window import hide_window_frame


class LmStudioViewport(QWidget):
    # LM Studio 0.4.x draws a 30 CSS-pixel draggable header in its renderer,
    # independently of the Windows caption. Qt coordinates use the same logical
    # pixels, so the native window and this offset scale together with DPI.
    TITLE_BAR_HEIGHT = 30

    def __init__(self, parent=None):
        super().__init__(parent)
        # A native parent clips the foreign HWND, including its mouse input.
        # A painted overlay would leave the renderer's Close button reachable.
        self.setAttribute(Qt.WA_NativeWindow)
        self._container = None

    def set_container(self, container):
        self._container = container
        self._position_container()

    def _position_container(self):
        if self._container is not None:
            height = self.TITLE_BAR_HEIGHT
            self._container.setGeometry(0, -height, self.width(), self.height() + height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_container()


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
        self._viewport = LmStudioViewport(self)
        self._layout.addWidget(self._viewport, 1)
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
        window.setFlag(Qt.FramelessWindowHint, True)
        self._container = QWidget.createWindowContainer(window, self._viewport)
        # Otherwise Qt optimizes the foreign HWND's parent to the top-level
        # Studio window, bypassing the viewport's native clipping boundary.
        self._container.setAttribute(Qt.WA_NativeWindow)
        self._container.setFocusPolicy(Qt.StrongFocus)
        self._viewport.set_container(self._container)
        hide_window_frame(hwnd)
        self._container.show()

    def unload(self):
        self._viewport.set_container(None)
        if self._window is not None:
            self._window.hide()
            self._window.setParent(None)
            self._window.deleteLater()
            self._window = None
        if self._container is not None:
            self._container.hide()
            self._container.deleteLater()
            self._container = None
        self._hwnd = None

    def retranslate_ui(self):
        tr = self.loc.tr if self.loc is not None else lambda text: text
        self.settings_btn.setText(tr("← Настройки"))
        self.stop_btn.setText(tr("⏹ Остановить"))
