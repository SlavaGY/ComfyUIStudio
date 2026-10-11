"""Раздел «Ultimate RVC» единого дерева настроек (этап RVC-1): папка
установки, порт, доп. аргументы запуска, автозапуск и кнопки управления
(Запустить / Остановить / Открыть).

Здесь только ПАРАМЕТРЫ. Запуск/остановка/«Открыть» -- на главном экране
(список режимов рядом с «Запустить» и полоса «запущен»); страница лишь
показывает состояние, которое окно шлёт из RvcController через set_state().

Поля пишутся в cfg["rvc"] (см. collect(); AppSettingsDialog подмешивает
результат в общий cfg при автосохранении -- тот же debounce-паттерн, что у
ComfyUISettingsPage). Пока RVC запущен или стартует, путь/порт/аргументы
заблокированы: они применяются только при следующем запуске.
"""

from __future__ import annotations

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

from ...core.rvc_process import port_conflict, rvc_settings, validate_rvc_root
from ..rvc_controller import FAILED, RUNNING, STARTING, STOPPED


class RvcSettingsPage(QWidget):
    changed = Signal()

    LAUNCH_HINT = (
        "Запускать и останавливать Ultimate RVC нужно на главном экране: "
        "выберите «Ultimate RVC» в списке рядом с кнопкой «Запустить»."
    )

    def __init__(self, cfg: dict, loc=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.loc = loc
        self._state = STOPPED
        s = rvc_settings(cfg)

        root = QVBoxLayout(self)

        # -- Установка --------------------------------------------------
        self.install_box = QGroupBox(self._tr("Установка"))
        form = QFormLayout(self.install_box)

        self.intro_label = QLabel(
            self._tr(
                "Ultimate RVC запускается как отдельная программа из своей "
                "папки (со своим окружением Python) и открывается во "
                "встроенном браузере Studio. От ComfyUI он не зависит."
            )
        )
        self.intro_label.setWordWrap(True)
        self.intro_label.setObjectName("mutedLabel")
        form.addRow(self.intro_label)

        path_row = QHBoxLayout()
        self.path_edit = QLineEdit(s["root_path"])
        self.path_edit.textChanged.connect(self._on_path_changed)
        self.browse_btn = QPushButton(self._tr("Обзор..."))
        self.browse_btn.clicked.connect(self._browse)
        path_row.addWidget(self.path_edit)
        path_row.addWidget(self.browse_btn)
        self.path_row_label = QLabel(self._tr("Папка Ultimate RVC:"))
        form.addRow(self.path_row_label, path_row)

        self.path_status = QLabel("")
        self.path_status.setWordWrap(True)
        self.path_status.setObjectName("mutedLabel")
        form.addRow(self.path_status)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(s["port"])
        self.port_spin.valueChanged.connect(self._on_port_changed)
        self.port_row_label = QLabel(self._tr("Порт:"))
        form.addRow(self.port_row_label, self.port_spin)

        self.port_status = QLabel("")
        self.port_status.setWordWrap(True)
        self.port_status.setObjectName("mutedLabel")
        form.addRow(self.port_status)

        self.args_edit = QLineEdit(s["launch_args"])
        self.args_edit.setPlaceholderText("--foo bar")
        self.args_edit.textChanged.connect(self._on_field_changed)
        self.args_row_label = QLabel(self._tr("Доп. аргументы urvc-web:"))
        form.addRow(self.args_row_label, self.args_edit)

        self.args_hint = QLabel(
            self._tr(
                "Необязательно. Передаются urvc-web как есть (пути с пробелами — в "
                "кавычках). Порт задаётся полем выше; если укажете свой флаг порта "
                "здесь, он будет иметь приоритет."
            )
        )
        self.args_hint.setWordWrap(True)
        self.args_hint.setObjectName("mutedLabel")
        form.addRow(self.args_hint)

        self.auto_start_check = QCheckBox(self._tr("Запускать Ultimate RVC вместе со Studio"))
        self.auto_start_check.setChecked(s["auto_start"])
        self.auto_start_check.stateChanged.connect(self._on_field_changed)
        form.addRow(self.auto_start_check)

        root.addWidget(self.install_box)

        # -- Состояние (только показ) -----------------------------------
        # Запуск/остановка/«Открыть» -- на главном экране (список режимов и
        # полоса «запущен»); здесь кнопок нет, чтобы не было двух мест
        # управления одним процессом.
        self.state_label = QLabel("")
        self.state_label.setWordWrap(True)
        root.addWidget(self.state_label)

        self.launch_hint = QLabel(self._tr(self.LAUNCH_HINT))
        self.launch_hint.setWordWrap(True)
        self.launch_hint.setObjectName("mutedLabel")
        root.addWidget(self.launch_hint)
        root.addStretch(1)

        self._refresh_path_status()
        self._refresh_port_status()
        self.set_state(STOPPED, "")

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    # -- поля -----------------------------------------------------------

    def _on_field_changed(self, *_args):
        self.changed.emit()

    def _on_path_changed(self, *_args):
        self._refresh_path_status()
        self.changed.emit()

    def _on_port_changed(self, *_args):
        self._refresh_port_status()
        self.changed.emit()

    def _browse(self):
        path = QFileDialog.getExistingDirectory(
            self, self._tr("Папка Ultimate RVC"), self.path_edit.text().strip()
        )
        if path:
            self.path_edit.setText(path)

    def _refresh_path_status(self):
        path = self.path_edit.text().strip()
        if not path:
            self.path_status.setText("")
            return
        ok, message = validate_rvc_root(path)
        self.path_status.setText(
            self._tr("Установка найдена.") if ok else self._tr(message)
        )

    def _refresh_port_status(self):
        conflict = port_conflict(self.cfg, self.port_spin.value())
        self.port_status.setText(
            self._tr("Этот порт уже назначен для {} — выберите другой.").format(conflict)
            if conflict
            else ""
        )

    def collect(self) -> dict:
        """Что записать в cfg: секция «rvc» целиком (с сохранением чужих
        ключей, которых на странице нет)."""
        section = dict(self.cfg.get("rvc") or {})
        section.update(
            {
                "root_path": self.path_edit.text().strip(),
                "port": self.port_spin.value(),
                "launch_args": self.args_edit.text().strip(),
                "auto_start": self.auto_start_check.isChecked(),
            }
        )
        return {"rvc": section}

    # -- состояние (из RvcController через окно) ---------------------------

    def set_state(self, state, message=""):
        self._state = state
        busy = state in (STARTING, RUNNING)
        for widget in (
            self.path_edit, self.browse_btn, self.port_spin, self.args_edit,
        ):
            widget.setEnabled(not busy)

        if message:
            text = message
        elif state == STOPPED:
            text = self._tr("Ultimate RVC не запущен.")
        else:
            text = ""
        # Ошибку выделяем значком, а не цветом: цвета задают QSS-темы
        # (их шесть, меняются на лету), и правило под отдельную метку
        # пришлось бы дублировать во всех.
        if state == FAILED and text:
            text = "\u26A0 " + text
        self.state_label.setText(text)

    def retranslate_ui(self):
        self.install_box.setTitle(self._tr("Установка"))
        self.intro_label.setText(
            self._tr(
                "Ultimate RVC запускается как отдельная программа из своей "
                "папки (со своим окружением Python) и открывается во "
                "встроенном браузере Studio. От ComfyUI он не зависит."
            )
        )
        self.path_row_label.setText(self._tr("Папка Ultimate RVC:"))
        self.browse_btn.setText(self._tr("Обзор..."))
        self.port_row_label.setText(self._tr("Порт:"))
        self.args_row_label.setText(self._tr("Доп. аргументы urvc-web:"))
        self.args_hint.setText(
            self._tr(
                "Необязательно. Передаются urvc-web как есть (пути с пробелами — в "
                "кавычках). Порт задаётся полем выше; если укажете свой флаг порта "
                "здесь, он будет иметь приоритет."
            )
        )
        self.auto_start_check.setText(self._tr("Запускать Ultimate RVC вместе со Studio"))
        self.launch_hint.setText(self._tr(self.LAUNCH_HINT))
        self._refresh_path_status()
        self._refresh_port_status()
        if self._state == STOPPED:
            self.state_label.setText(self._tr("Ultimate RVC не запущен."))
