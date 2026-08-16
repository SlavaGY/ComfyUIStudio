from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QWidget,
)


class Toolbar(QWidget):
    """Панель инструментов главного окна.

    Тема, язык интерфейса и переключатель семантического поиска отсюда
    перенесены в отдельное окно настроек (см. app/ui/settings_window.py,
    открывается кнопкой "⚙" ниже) — раньше они жили прямо в тулбаре и
    делали его слишком тесным. Toolbar по-прежнему хранит и применяет
    свои собственные переводимые тексты (см. retranslate_ui) — просто
    вызывается это теперь из SettingsWindow при смене языка, а не
    изнутри самого Toolbar.
    """

    openFolder = Signal()
    searchRequested = Signal()
    filtersRequested = Signal()
    sortRequested = Signal()
    statisticsRequested = Signal()
    importRatingsRequested = Signal()
    settingsRequested = Signal()

    def __init__(self):
        super().__init__()

        layout = QHBoxLayout(self)

        layout.setContentsMargins(6, 6, 6, 6)

        self.open_folder_btn = QPushButton(self.tr("📂 Folder"))
        self.open_folder_btn.setObjectName("primaryButton")

        self.search = QLineEdit()
        self.search.setPlaceholderText(self.tr("Search..."))

        self.search_btn = QPushButton(self.tr("Search"))

        self.filters_btn = QPushButton(self.tr("Filters ▼"))

        self.sort_btn = QPushButton(self.tr("Sort ▼"))

        self.stats_btn = QPushButton(self.tr("📊 Stats"))

        self.import_ratings_btn = QPushButton(self.tr("⬇ Import ratings"))

        self.settings_btn = QPushButton(self.tr("⚙ Settings"))

        layout.addWidget(self.open_folder_btn)

        layout.addSpacing(10)

        layout.addWidget(self.search, 1)

        layout.addWidget(self.search_btn)
        layout.addWidget(self.filters_btn)
        layout.addWidget(self.sort_btn)
        layout.addWidget(self.stats_btn)
        layout.addWidget(self.import_ratings_btn)

        layout.addSpacing(10)

        layout.addWidget(self.settings_btn)

        self.open_folder_btn.clicked.connect(
            self.openFolder.emit
        )

        self.search_btn.clicked.connect(
            self.searchRequested.emit
        )

        self.search.returnPressed.connect(
            self.searchRequested.emit
        )

        self.filters_btn.clicked.connect(
            self.filtersRequested.emit
        )
        self.sort_btn.clicked.connect(
            self.sortRequested.emit
        )
        self.stats_btn.clicked.connect(
            self.statisticsRequested.emit
        )
        self.import_ratings_btn.clicked.connect(
            self.importRatingsRequested.emit
        )
        self.settings_btn.clicked.connect(
            self.settingsRequested.emit
        )

    def retranslate_ui(self) -> None:
        """Перевыставляет тексты всех кнопок тулбара после смены языка
        (см. LocalizationManager.apply_language в app/ui/settings_window.py
        — установка QTranslator сама по себе не обновляет текст уже
        созданных виджетов)."""

        self.open_folder_btn.setText(self.tr("📂 Folder"))
        self.search.setPlaceholderText(self.tr("Search..."))
        self.search_btn.setText(self.tr("Search"))
        self.filters_btn.setText(self.tr("Filters ▼"))
        self.sort_btn.setText(self.tr("Sort ▼"))
        self.stats_btn.setText(self.tr("📊 Stats"))
        self.import_ratings_btn.setText(self.tr("⬇ Import ratings"))
        self.settings_btn.setText(self.tr("⚙ Settings"))

    def search_text(self):

        return self.search.text()

    def set_search(self, text):

        self.search.setText(text)

    def filters_button(self):

        return self.filters_btn

    def sort_button(self):

        return self.sort_btn
