from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox


class TriStateFilterCheckBox(QCheckBox):
    """Чекбокс с тремя состояниями для фильтров LoRA/пользовательских
    тегов (задача: включить/исключить/нейтрально):

    - 1-й клик: включить в фильтр (обычное отмеченное состояние,
      Qt.Checked — синяя подсветка, как и раньше);
    - 2-й клик: исключить из фильтра (Qt.PartiallyChecked — красная
      подсветка, см. QCheckBox::indicator:indeterminate в темах
      app/themes/*.qss);
    - 3-й клик: обратно в нейтральное состояние (Qt.Unchecked, не
      участвует в фильтре вообще).

    Стандартный QCheckBox с setTristate(True) при клике пользователя
    цикл состояний не гарантирует — здесь порядок жёстко задан через
    nextCheckState(), т.к. порядок принципиален (обычный клик должен
    сначала ВКЛЮЧАТЬ, а не исключать)."""

    stateCycled = Signal()

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)

        self.setTristate(True)

        # nextCheckState() (см. ниже) отвечает и за programmatic-клики
        # тоже, но для восстановления состояния из сохранённых фильтров
        # используется set_state(), которая не проходит через клик
        self.stateChanged.connect(lambda _state: self.stateCycled.emit())

    def nextCheckState(self) -> None:

        current = self.checkState()

        if current == Qt.CheckState.Unchecked:
            self.setCheckState(Qt.CheckState.Checked)
        elif current == Qt.CheckState.Checked:
            self.setCheckState(Qt.CheckState.PartiallyChecked)
        else:
            self.setCheckState(Qt.CheckState.Unchecked)

    def set_state(self, included: bool, excluded: bool) -> None:
        """Программная установка состояния (не через клик пользователя) —
        используется при восстановлении сохранённых фильтров."""

        if excluded:
            self.setCheckState(Qt.CheckState.PartiallyChecked)
        elif included:
            self.setCheckState(Qt.CheckState.Checked)
        else:
            self.setCheckState(Qt.CheckState.Unchecked)

    def is_included(self) -> bool:

        return self.checkState() == Qt.CheckState.Checked

    def is_excluded(self) -> bool:

        return self.checkState() == Qt.CheckState.PartiallyChecked
