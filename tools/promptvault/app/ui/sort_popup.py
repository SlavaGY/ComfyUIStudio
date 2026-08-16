from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QRadioButton,
    QVBoxLayout,
)

from app.core.sort_options import SortMode


class SortPopup(QFrame):

    changed = Signal()

    def __init__(self):
        super().__init__(None, Qt.Popup)

        self.setObjectName("sortPopup")

        layout = QVBoxLayout(self)

        self.buttons = {}

        self.add(layout, self.tr("Newest first"), SortMode.NEWEST, True)
        self.add(layout, self.tr("Oldest first"), SortMode.OLDEST)
        self.add(layout, self.tr("Model"), SortMode.MODEL)
        self.add(layout, self.tr("CFG"), SortMode.CFG)
        self.add(layout, self.tr("Steps"), SortMode.STEPS)
        self.add(layout, self.tr("Generation time"), SortMode.GENERATION_TIME)
        self.add(layout, self.tr("Rating"), SortMode.RATING)

    def add(self, layout, text, mode, checked=False):

        btn = QRadioButton(text)

        btn.setChecked(checked)

        btn.toggled.connect(
            lambda checked: checked and self.changed.emit()
        )

        layout.addWidget(btn)

        self.buttons[mode] = btn

    def current_mode(self):

        for mode, btn in self.buttons.items():

            if btn.isChecked():
                return mode

        return SortMode.NEWEST
