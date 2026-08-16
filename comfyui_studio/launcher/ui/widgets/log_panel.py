"""
Панель лога последнего запуска ComfyUI (переиспользуется на странице
настроек).

Вынесено из comfyui_launcher.py (этап 1 дорожной карты).
"""

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QPlainTextEdit, QPushButton, QVBoxLayout

from ...core.constants import APP_DIR

MAX_LOG_PANEL_LINES = 2000


class LogPanel(QGroupBox):
    def __init__(self, loc=None, title="Лог последнего запуска ComfyUI", parent=None):
        self.loc = loc
        self._title_ru = title
        super().__init__(self._tr(title), parent)
        layout = QVBoxLayout(self)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(MAX_LOG_PANEL_LINES)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(9)
        self.text.setFont(mono)
        # Цвета берём из применённой темы (QWidget-правило в *.qss), а не
        # захардкоженный тёмный терминал — иначе на светлой теме лог
        # оставался тёмным пятном посреди светлого интерфейса.
        layout.addWidget(self.text)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.open_folder_btn = QPushButton(self._tr("Открыть папку с логами"))
        self.open_folder_btn.clicked.connect(self._open_log_folder)
        btn_row.addWidget(self.open_folder_btn)
        self.clear_btn = QPushButton(self._tr("Очистить"))
        self.clear_btn.clicked.connect(self.text.clear)
        btn_row.addWidget(self.clear_btn)
        layout.addLayout(btn_row)

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def retranslate_ui(self):
        self.setTitle(self._tr(self._title_ru))
        self.open_folder_btn.setText(self._tr("Открыть папку с логами"))
        self.clear_btn.setText(self._tr("Очистить"))

    def append_line(self, line):
        self.text.appendPlainText(line)

    def _open_log_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(APP_DIR))


# --------------------------------------------------------------------------
# Диалог "Аргументы запуска ComfyUI" — раньше скрипт запуска/порт/чекбокс
# браузера жили прямо на экране настроек; теперь это отдельное окно,
# чтобы не загромождать главный экран, и в нём же живут доп. флаги
# командной строки ComfyUI (см. LAUNCH_ARG_DEFS выше) — каждый как
# чекбокс с описанием, что он делает, и полем для значения там, где оно
# нужно.
# --------------------------------------------------------------------------


