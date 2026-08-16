from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from comfyui_studio.promptvault.ui.thumbnail_item import ThumbnailItem


class ThumbnailPanel(QWidget):

    # индекс выбранного изображения
    imageSelected = Signal(int)

    def __init__(self):
        super().__init__()

        self.current_index = -1
        self.thumb_items = []
        self.image_paths = []

        # ---------- Scroll ----------
        self.container = QWidget()

        self.grid = QGridLayout(self.container)
        self.grid.setSpacing(10)
        self.grid.setContentsMargins(5, 5, 5, 5)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.container)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.scroll)

    # ---------------------------------------------------------

    def clear(self):

        while self.grid.count():
            item = self.grid.takeAt(0)

            if item.widget():
                item.widget().deleteLater()

        self.thumb_items.clear()
        self.image_paths.clear()
        self.current_index = -1

    # ---------------------------------------------------------

    def set_generation(self, generation):

        self.clear()

        if generation is None:
            return

        self.image_paths = [
            generation.directory / img.file
            for img in generation.images
        ]

        for row, path in enumerate(self.image_paths):

            thumb = ThumbnailItem(
                path,
                generation.images[row].seed,
                lambda p=path, i=row: self.select(i)
            )

            self.thumb_items.append(thumb)

            self.grid.addWidget(
                thumb,
                row,
                0
            )

        if self.image_paths:
            self.select(0)

    # ---------------------------------------------------------

    def select(self, index):

        if index < 0:
            return

        if index >= len(self.thumb_items):
            return

        self.current_index = index

        for i, thumb in enumerate(self.thumb_items):
            thumb.set_selected(i == index)

        self.imageSelected.emit(index)

    # ---------------------------------------------------------

    def next(self):

        if self.current_index + 1 < len(self.thumb_items):
            self.select(self.current_index + 1)

    # ---------------------------------------------------------

    def previous(self):

        if self.current_index > 0:
            self.select(self.current_index - 1)

    # ---------------------------------------------------------

    def wheelEvent(self, event):

        if event.modifiers() == Qt.ControlModifier:
            super().wheelEvent(event)
            return

        if event.angleDelta().y() < 0:
            self.next()
        else:
            self.previous()
