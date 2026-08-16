from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from app.utils import open_file_externally


class ThumbnailItem(QWidget):

    def __init__(self, image_path, seed, click_callback):
        super().__init__()

        self.image_path = Path(image_path)
        self.seed = seed
        self.click_callback = click_callback

        self.setFixedWidth(280)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)


        # -------- IMAGE --------

        self.label = QLabel()

        self.label.setAlignment(
            Qt.AlignCenter
        )

        self.label.setFixedSize(
            260,
            260
        )


        pixmap = QPixmap(
            str(self.image_path)
        )

        if not pixmap.isNull():

            pixmap = pixmap.scaled(
                250,
                250,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )

            self.label.setPixmap(
                pixmap
            )


        layout.addWidget(
            self.label
        )


        # -------- SEED --------

        if seed is not None:

            self.seed_label = QLabel(
                self.tr("Seed: {}").format(seed)
            )

        else:

            self.seed_label = QLabel(
                self.tr("Seed: unknown")
            )


        self.seed_label.setAlignment(
            Qt.AlignCenter
        )


        layout.addWidget(
            self.seed_label
        )


        self.selected = False


    # --------------------------------------------------

    def mousePressEvent(self, event):

        if self.click_callback:

            self.click_callback(
                self.image_path
            )


    # --------------------------------------------------

    def mouseDoubleClickEvent(self, event):

        if self.image_path.exists():

            open_file_externally(
                self.image_path
            )


    # --------------------------------------------------

    def set_selected(self, value: bool):

        self.selected = value

        if value:

            self.label.setStyleSheet(
                """
                QLabel {
                    border: 3px solid #4fc3f7;
                    border-radius: 6px;
                }
                """
            )

        else:

            self.label.setStyleSheet(
                ""
            )


    # --------------------------------------------------

    def enterEvent(self, event):

        if not self.selected:

            self.label.setStyleSheet(
                """
                QLabel {
                    border: 2px solid #777777;
                    border-radius: 6px;
                }
                """
            )


    # --------------------------------------------------

    def leaveEvent(self, event):

        if not self.selected:

            self.label.setStyleSheet(
                ""
            )
