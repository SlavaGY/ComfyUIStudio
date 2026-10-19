"""Settings for opening the original LM Studio desktop interface."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.lm_studio import lm_studio_executable, validate_lm_studio_executable


class LmStudioSettingsPage(QWidget):
    changed = Signal()

    INTRO = (
        "Оригинальное окно LM Studio открывается внутри Studio. "
        "Для завершения работы используйте кнопку «Остановить». "
        "При выходе из Studio LM Studio также останавливается."
    )
    HINT = (
        "Для запуска выберите «LM Studio (оригинал)» в списке рядом с кнопкой "
        "«Запустить» на главном экране."
    )

    def __init__(self, cfg, loc=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.loc = loc
        root = QVBoxLayout(self)
        self.install_box = QGroupBox()
        form = QFormLayout(self.install_box)
        self.intro_label = QLabel()
        self.intro_label.setWordWrap(True)
        form.addRow(self.intro_label)
        row = QHBoxLayout()
        self.path_edit = QLineEdit(lm_studio_executable(cfg))
        self.path_edit.setPlaceholderText(r"C:\...\LM Studio\LM Studio.exe")
        self.browse_btn = QPushButton()
        row.addWidget(self.path_edit)
        row.addWidget(self.browse_btn)
        self.path_label = QLabel()
        form.addRow(self.path_label, row)
        self.path_status = QLabel()
        self.path_status.setWordWrap(True)
        self.path_status.setObjectName("mutedLabel")
        form.addRow(self.path_status)
        root.addWidget(self.install_box)
        self.launch_hint = QLabel()
        self.launch_hint.setWordWrap(True)
        self.launch_hint.setObjectName("mutedLabel")
        root.addWidget(self.launch_hint)
        root.addStretch(1)
        self.path_edit.textChanged.connect(self._on_path_changed)
        self.browse_btn.clicked.connect(self._browse)
        self.retranslate_ui()

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def _on_path_changed(self):
        self._refresh_path_status()
        self.changed.emit()

    def _refresh_path_status(self):
        path = self.path_edit.text().strip()
        valid, message = validate_lm_studio_executable(path)
        self.path_status.setText(self._tr("Установка найдена.") if valid else self._tr(message))

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, self._tr("Исполняемый файл LM Studio"),
            self.path_edit.text().strip(), "Executables (*.exe);;All files (*)",
        )
        if path:
            self.path_edit.setText(path)

    def collect(self):
        current = self.cfg.get("lm_studio")
        section = dict(current) if isinstance(current, dict) else {}
        section["executable_path"] = self.path_edit.text().strip()
        return {"lm_studio": section}

    def set_state(self, state):
        enabled = state not in ("starting", "running", "failed_active")
        self.path_edit.setEnabled(enabled)
        self.browse_btn.setEnabled(enabled)

    def retranslate_ui(self):
        self.install_box.setTitle(self._tr("Установка"))
        self.intro_label.setText(self._tr(self.INTRO))
        self.path_label.setText(self._tr("Исполняемый файл LM Studio:"))
        self.browse_btn.setText(self._tr("Обзор..."))
        self.launch_hint.setText(self._tr(self.HINT))
        self._refresh_path_status()
