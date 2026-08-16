from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from comfyui_studio.promptvault.config import MAX_RATING, MIN_RATING


class _StarLabel(QLabel):
    """Одна кликабельная звезда."""

    clicked = Signal()

    def mousePressEvent(self, event):

        if event.button() == Qt.LeftButton:

            self.clicked.emit()

            # QLabel по умолчанию не принимает mousePressEvent, из-за
            # чего он всплывает вверх по дереву виджетов до
            # QListWidget — тот интерпретирует это как клик по строке
            # и меняет текущее выделение списка. explicit accept()
            # останавливает всплытие, не давая клику по звезде на
            # чужой (невыделенной) карточке "телепортировать" на неё.
            event.accept()
            return

        super().mousePressEvent(event)


class StarRatingWidget(QWidget):
    """Ряд из 5 кликабельных звёзд для рейтинга от 0 (без рейтинга) до 5.

    Повторный клик по звезде, совпадающей с текущим рейтингом,
    сбрасывает рейтинг обратно в 0 — так рейтинг можно снять.
    """

    ratingChanged = Signal(int)

    def __init__(self, rating: int = 0, parent: QWidget | None = None):
        super().__init__(parent)

        self._rating = self._clamp(rating)
        self._stars: list[_StarLabel] = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        for value in range(MIN_RATING + 1, MAX_RATING + 1):

            star = _StarLabel()
            star.setObjectName("ratingStar")
            star.setCursor(Qt.PointingHandCursor)

            star.clicked.connect(
                lambda v=value: self._on_star_clicked(v)
            )

            layout.addWidget(star)
            self._stars.append(star)

        self._refresh()

    # --------------------------------------------------

    def _on_star_clicked(self, value: int) -> None:

        new_rating = MIN_RATING if value == self._rating else value

        self.set_rating(new_rating)
        self.ratingChanged.emit(new_rating)

    # --------------------------------------------------

    def set_rating(self, rating: int) -> None:
        """Программно выставляет рейтинг (без эмита ratingChanged)."""

        self._rating = self._clamp(rating)
        self._refresh()

    def rating(self) -> int:

        return self._rating

    # --------------------------------------------------

    @staticmethod
    def _clamp(rating: int) -> int:

        return max(MIN_RATING, min(MAX_RATING, rating))

    def _refresh(self) -> None:

        for i, star in enumerate(self._stars, start=1):
            filled = i <= self._rating

            star.setText("★" if filled else "☆")

            # динамическое свойство для раздельной стилизации закрашенных
            # и пустых звёзд через QSS (см. темы в app/themes/*.qss)
            star.setProperty("filled", filled)
            star.style().unpolish(star)
            star.style().polish(star)
