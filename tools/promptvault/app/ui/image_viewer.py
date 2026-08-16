from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView


class ImageViewer(QGraphicsView):

    def __init__(self):
        super().__init__()

        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)

        # Один единственный объект изображения
        self.pixmap_item = QGraphicsPixmapItem()
        # без этого Qt масштабирует пиксельную карту "ближайшим соседом"
        # при любом fitInView/zoom — картинка выглядит зашакаленной
        # (жёсткие пиксельные края) вместо плавного даунскейла/апскейла
        self.pixmap_item.setTransformationMode(Qt.SmoothTransformation)
        self.scene.addItem(self.pixmap_item)

        # сглаживание при отрисовке сцены (влияет на сам QGraphicsView,
        # а не на исходные данные картинки — оригинальный файл
        # по-прежнему грузится в полном разрешении, см. set_image)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setRenderHint(QPainter.SmoothPixmapTransform, True)

        self.zoom_level = 0

        self.setDragMode(QGraphicsView.ScrollHandDrag)

    # --------------------------

    def set_image(self, path):

        # без аргументов QPixmap(path) уже грузит исходный файл как
        # есть, без даунскейла — "шакалистость" была не в загрузке, а
        # в отрисовке без сглаживания (см. __init__)
        pixmap = QPixmap(str(path))

        self.pixmap_item.setPixmap(pixmap)

        self.scene.setSceneRect(
            self.pixmap_item.boundingRect()
        )

        self.reset_view()

    # --------------------------

    def reset_view(self):

        self.resetTransform()

        self.fitInView(
            self.pixmap_item,
            Qt.KeepAspectRatio
        )

        self.zoom_level = 0

    # --------------------------

    def wheelEvent(self, event):

        if event.modifiers() == Qt.ControlModifier:

            factor = 1.15

            if event.angleDelta().y() > 0:

                self.scale(factor, factor)

                self.zoom_level += 1

            else:

                self.scale(1 / factor, 1 / factor)

                self.zoom_level -= 1

            return

        super().wheelEvent(event)

    # --------------------------

    def resizeEvent(self, event):

        super().resizeEvent(event)

        if (
            self.zoom_level == 0
            and not self.pixmap_item.pixmap().isNull()
        ):
            self.fitInView(
                self.pixmap_item,
                Qt.KeepAspectRatio
            )
