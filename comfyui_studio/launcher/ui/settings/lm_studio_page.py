"""Settings for opening the original LM Studio desktop interface."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
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
        section = cfg.get("lm_studio") or {}
        self.cli_label = QLabel()
        self.cli_edit = QLineEdit(section.get("cli_path", ""))
        self.cli_edit.setPlaceholderText(r"~\.lmstudio\bin\lms.exe")
        cli_row = QHBoxLayout()
        cli_row.addWidget(self.cli_edit)
        self.cli_browse_btn = QPushButton()
        self.cli_browse_btn.clicked.connect(self._browse_cli)
        cli_row.addWidget(self.cli_browse_btn)
        form.addRow(self.cli_label, cli_row)
        self.api_port_label, self.ui_port_label, self.token_label = QLabel(), QLabel(), QLabel()
        self.api_port_spin, self.ui_port_spin = QSpinBox(), QSpinBox()
        for widget, key, default in ((self.api_port_spin, "api_port", 1234), (self.ui_port_spin, "ui_port", 7864)):
            widget.setRange(1, 65535)
            widget.setValue(int(section.get(key, default)))
            widget.valueChanged.connect(self.changed.emit)
        self.token_edit = QLineEdit(section.get("api_token", ""))
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.auto_server_check = QCheckBox()
        self.auto_server_check.setChecked(section.get("auto_start_server", True))
        self.auto_server_check.toggled.connect(self.changed.emit)
        self.cli_edit.textChanged.connect(self.changed.emit)
        self.token_edit.textChanged.connect(self.changed.emit)
        form.addRow(self.api_port_label, self.api_port_spin)
        form.addRow(self.ui_port_label, self.ui_port_spin)
        form.addRow(self.token_label, self.token_edit)
        form.addRow(self.auto_server_check)
        self.app_hint = QLabel()
        self.app_hint.setWordWrap(True)
        form.addRow(self.app_hint)
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
        section.update(cli_path=self.cli_edit.text().strip(), api_port=self.api_port_spin.value(),
                       ui_port=self.ui_port_spin.value(), api_token=self.token_edit.text().strip(),
                       auto_start_server=self.auto_server_check.isChecked())
        return {"lm_studio": section}

    def _browse_cli(self):
        path, _ = QFileDialog.getOpenFileName(self, "lms", self.cli_edit.text(), "Executables (*.exe);;All files (*)")
        if path:
            self.cli_edit.setText(path)

    def set_state(self, state):
        enabled = state not in ("starting", "running", "failed_active")
        self.path_edit.setEnabled(enabled)
        self.browse_btn.setEnabled(enabled)
        for widget in (self.cli_edit, self.cli_browse_btn, self.api_port_spin, self.ui_port_spin, self.token_edit, self.auto_server_check):
            widget.setEnabled(enabled)

    def retranslate_ui(self):
        self.install_box.setTitle(self._tr("Установка"))
        self.intro_label.setText(self._tr(self.INTRO))
        self.path_label.setText(self._tr("Исполняемый файл LM Studio:"))
        self.browse_btn.setText(self._tr("Обзор..."))
        self.launch_hint.setText(self._tr(self.HINT))
        self.cli_label.setText(self._tr("CLI lms (необязательно):"))
        self.cli_browse_btn.setText(self._tr("Обзор..."))
        self.api_port_label.setText(self._tr("Порт сервера LM Studio:"))
        self.ui_port_label.setText(self._tr("Порт интерфейса Studio:"))
        self.token_label.setText(self._tr("API-токен LM Studio (если включён):"))
        self.auto_server_check.setText(self._tr("Автоматически запускать локальный сервер LM Studio"))
        self.app_hint.setText(self._tr("«LM Studio» открывает чат Studio. «LM Studio (оригинал)» сохраняет родной интерфейс. История этих приложений хранится отдельно."))
        self._refresh_path_status()
