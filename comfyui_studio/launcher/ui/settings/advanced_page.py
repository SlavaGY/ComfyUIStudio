"""Раздел "Advanced" единого дерева настроек: уровень логирования,
диагностика, сброс настроек к дефолту, выход/перезапуск ВСЕЙ ComfyUI
Studio.

Область действия сброса сознательно ограничена настройками ЛАУНЧЕРА
(config.json, см. core/config.py) — у Prompt Builder и PromptVault свои
независимые хранилища настроек (QSettings("PromptVault", "PromptVault")
и т.п.), сбрасывать их отсюда же значило бы либо тихо трогать данные
другого инструмента с экрана "ComfyUI", либо тянуть сюда знание об их
внутреннем устройстве — ни то, ни другое не входит в объём этапа 4.
Кнопка ниже explicitly говорит "только настройки лаунчера".

Application (Restart/Quit) -- добавлено по замечанию пользователя после
первой версии этапа 4: раньше единственные кнопки выхода/перезапуска
во всём дереве настроек были кнопками PromptVault (см. его
ui/settings_window.py, _build_application_group) и относились ТОЛЬКО к
самому PromptVault -- в монолитной сборке (см. MainWindow(standalone=...)
в promptvault/ui/main_window.py) они теперь скрыты как вводящие в
заблуждение (и в случае Restart — попросту опасные, см. комментарий в
launcher_window.py.restart_studio()). Здесь — их полноценный аналог для
всей Studio целиком.

Все строки на этой странице -- исходные на русском (см. пояснение в
general_page.py про TRANSLATIONS/loc.tr()).

Часть этапа 4 дорожной карты рефакторинга ("Единое дерево настроек").
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from comfyui_studio import __version__ as APP_VERSION

from ...core.constants import APP_DIR, APP_LOG_PATH, CONFIG_PATH, DEFAULT_CONFIG
from ...core.logging_setup import AVAILABLE_LOG_LEVELS, set_console_log_level


class AdvancedSettingsPage(QWidget):
    # эмитится после подтверждённого сброса -- AppSettingsDialog
    # перечитывает config.json и заново заполняет ВСЕ страницы дерева
    # (см. AppSettingsDialog._on_reset_confirmed)
    reset_confirmed = Signal()
    changed = Signal()
    # эмитятся после подтверждения в этой же странице -- AppSettingsDialog
    # ретранслирует их наружу как свои собственные quit_studio_requested/
    # restart_studio_requested (см. app_settings_dialog.py)
    quit_requested = Signal()
    restart_requested = Signal()

    def __init__(self, cfg: dict, loc=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.loc = loc

        root = QVBoxLayout(self)

        # -- Logging --------------------------------------------------
        log_box = QGroupBox(self._tr("Логирование"))
        self.log_box = log_box
        log_form = QFormLayout(log_box)

        self.log_level_combo = QComboBox()
        self.log_level_combo.addItems(AVAILABLE_LOG_LEVELS)
        self.log_level_combo.setCurrentText(cfg.get("log_level", "INFO"))
        self.log_level_combo.currentTextChanged.connect(self._on_log_level_changed)
        self.log_level_row_label = QLabel(self._tr("Уровень логирования консоли:"))
        log_form.addRow(self.log_level_row_label, self.log_level_combo)

        self.log_level_hint = QLabel(
            self._tr(
                "Файл лога всегда сохраняет полную детализацию независимо "
                "от этой настройки — она влияет только на то, что "
                "выводится в консоль."
            )
        )
        self.log_level_hint.setWordWrap(True)
        self.log_level_hint.setObjectName("mutedLabel")
        log_form.addRow(self.log_level_hint)

        self.open_log_btn = QPushButton(self._tr("Открыть файл лога"))
        self.open_log_btn.clicked.connect(self._open_log_file)
        log_form.addRow(self.open_log_btn)

        root.addWidget(log_box)

        # -- Diagnostics ------------------------------------------------
        diag_box = QGroupBox(self._tr("Диагностика"))
        self.diag_box = diag_box
        diag_layout = QVBoxLayout(diag_box)

        self.diag_text = QPlainTextEdit()
        self.diag_text.setReadOnly(True)
        self.diag_text.setMaximumHeight(110)
        self.diag_text.setPlainText(self._diagnostics_text())
        diag_layout.addWidget(self.diag_text)

        root.addWidget(diag_box)

        # -- Reset --------------------------------------------------------
        reset_box = QGroupBox(self._tr("Сброс"))
        self.reset_box = reset_box
        reset_layout = QVBoxLayout(reset_box)

        self.reset_hint = QLabel(
            self._tr(
                "Сбрасывает только настройки лаунчера ComfyUI (путь "
                "установки, порт, аргументы запуска, переменные "
                "окружения, тему, язык). Prompt Builder и PromptVault "
                "сохраняют свои настройки — это их не затрагивает."
            )
        )
        self.reset_hint.setWordWrap(True)
        self.reset_hint.setObjectName("mutedLabel")
        reset_layout.addWidget(self.reset_hint)

        reset_row = QHBoxLayout()
        self.reset_btn = QPushButton(
            self._tr("Сбросить настройки лаунчера к значениям по умолчанию")
        )
        self.reset_btn.clicked.connect(self._on_reset_clicked)
        reset_row.addWidget(self.reset_btn)
        reset_row.addStretch(1)
        reset_layout.addLayout(reset_row)

        root.addWidget(reset_box)

        # -- Application: перезапуск / выход ВСЕЙ Studio (НОВОЕ) -----------
        app_box = QGroupBox(self._tr("Приложение"))
        self.app_box = app_box
        app_layout = QVBoxLayout(app_box)

        self.app_hint = QLabel(
            self._tr(
                "Закрывает или перезапускает весь комплект ComfyUI Studio "
                "целиком — лаунчер и открытые окна остальных "
                "инструментов. Если ComfyUI запущен, он будет корректно "
                "остановлен перед выходом/перезапуском."
            )
        )
        self.app_hint.setWordWrap(True)
        self.app_hint.setObjectName("mutedLabel")
        app_layout.addWidget(self.app_hint)

        app_btn_row = QHBoxLayout()
        self.restart_studio_btn = QPushButton(self._tr("🔄 Перезапустить ComfyUI Studio"))
        self.restart_studio_btn.clicked.connect(self._on_restart_studio_clicked)
        app_btn_row.addWidget(self.restart_studio_btn)
        self.quit_studio_btn = QPushButton(self._tr("⏻ Закрыть ComfyUI Studio"))
        self.quit_studio_btn.clicked.connect(self._on_quit_studio_clicked)
        app_btn_row.addWidget(self.quit_studio_btn)
        app_btn_row.addStretch(1)
        app_layout.addLayout(app_btn_row)

        root.addWidget(app_box)
        root.addStretch(1)

    # -- logging ------------------------------------------------------

    def _on_log_level_changed(self, level_name):
        set_console_log_level(level_name)
        self.changed.emit()

    def collect(self) -> dict:
        return {"log_level": self.log_level_combo.currentText()}

    def _open_log_file(self):
        os.makedirs(APP_DIR, exist_ok=True)
        if not os.path.isfile(APP_LOG_PATH):
            open(APP_LOG_PATH, "a", encoding="utf-8").close()
        if sys.platform == "win32":
            os.startfile(APP_LOG_PATH)  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", APP_LOG_PATH])

    # -- diagnostics ------------------------------------------------------

    def _diagnostics_text(self) -> str:
        return (
            f"ComfyUI Studio {APP_VERSION}\n"
            f"Python {platform.python_version()} ({platform.architecture()[0]})\n"
            f"OS: {platform.platform()}\n"
            f"config.json: {CONFIG_PATH}\n"
            f"launcher.log: {APP_LOG_PATH}"
        )

    # -- reset --------------------------------------------------------

    def _on_reset_clicked(self):
        answer = QMessageBox.question(
            self,
            self._tr("Сброс настроек лаунчера"),
            self._tr(
                "Это сбросит папку установки ComfyUI, порт, аргументы "
                "запуска, переменные окружения, тему и язык к значениям "
                "по умолчанию. Действие необратимо. Продолжить?"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        from ...core.config import save_config

        save_config(DEFAULT_CONFIG.copy())
        self.reset_confirmed.emit()

    # -- Application: выход/перезапуск всей Studio ---------------------

    def _on_restart_studio_clicked(self):
        answer = QMessageBox.question(
            self,
            self._tr("Перезапуск ComfyUI Studio"),
            self._tr(
                "Перезапустить ComfyUI Studio целиком сейчас? Если ComfyUI "
                "запущен, он будет остановлен."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.restart_requested.emit()

    def _on_quit_studio_clicked(self):
        answer = QMessageBox.question(
            self,
            self._tr("Закрытие ComfyUI Studio"),
            self._tr(
                "Закрыть ComfyUI Studio целиком сейчас? Если ComfyUI "
                "запущен, он будет остановлен."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.quit_requested.emit()

    # -- прочее -----------------------------------------------------

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def retranslate_ui(self):
        self.log_box.setTitle(self._tr("Логирование"))
        self.log_level_row_label.setText(self._tr("Уровень логирования консоли:"))
        self.log_level_hint.setText(
            self._tr(
                "Файл лога всегда сохраняет полную детализацию независимо "
                "от этой настройки — она влияет только на то, что "
                "выводится в консоль."
            )
        )
        self.open_log_btn.setText(self._tr("Открыть файл лога"))
        self.diag_box.setTitle(self._tr("Диагностика"))
        self.reset_box.setTitle(self._tr("Сброс"))
        self.reset_hint.setText(
            self._tr(
                "Сбрасывает только настройки лаунчера ComfyUI (путь "
                "установки, порт, аргументы запуска, переменные "
                "окружения, тему, язык). Prompt Builder и PromptVault "
                "сохраняют свои настройки — это их не затрагивает."
            )
        )
        self.reset_btn.setText(
            self._tr("Сбросить настройки лаунчера к значениям по умолчанию")
        )
        self.app_box.setTitle(self._tr("Приложение"))
        self.app_hint.setText(
            self._tr(
                "Закрывает или перезапускает весь комплект ComfyUI Studio "
                "целиком — лаунчер и открытые окна остальных "
                "инструментов. Если ComfyUI запущен, он будет корректно "
                "остановлен перед выходом/перезапуском."
            )
        )
        self.restart_studio_btn.setText(self._tr("🔄 Перезапустить ComfyUI Studio"))
        self.quit_studio_btn.setText(self._tr("⏻ Закрыть ComfyUI Studio"))
