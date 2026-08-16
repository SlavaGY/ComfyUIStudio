"""Окно статистики/дашборда (задача 3.2).

Открывается из Toolbar (кнопка "📊 Stats") и содержит две вкладки:

- "Current view" — статистика ровно того, что сейчас видно в самой
  галерее: текущая открытая папка с уже применёнными фильтрами (см.
  GalleryManager.get_statistics / GalleryManager.filtered_generations);
- "Whole library" — статистика по всей библиотеке (всем папкам,
  когда-либо просканированным в БД), независимо от того, какая папка
  сейчас открыта и какие фильтры включены (см.
  GalleryManager.get_library_statistics /
  GenerationRepository.get_statistics).
"""

from __future__ import annotations

from PySide6.QtCharts import (
    QBarCategoryAxis,
    QBarSeries,
    QBarSet,
    QChart,
    QChartView,
    QValueAxis,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor, QPainter
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from comfyui_studio.promptvault.core.gallery_manager import GalleryManager
from comfyui_studio.promptvault.core.statistics import Statistics


class StatisticsWindow(QMainWindow):
    """Окно со сводной статистикой и диаграммами — вкладка "текущий
    вид" (папка + фильтры) и вкладка "вся библиотека"."""

    def __init__(self, gallery: GalleryManager, parent: QWidget | None = None):
        super().__init__(parent)

        self.gallery = gallery

        self.setWindowTitle(self.tr("PromptVault — Statistics"))
        self.resize(1100, 800)

        self.stats: Statistics = gallery.get_statistics()
        self.library_stats: Statistics = gallery.get_library_statistics()

        self._build_ui()

    # --------------------------------------------------

    def _build_ui(self) -> None:

        root = QWidget()
        self.setCentralWidget(root)

        outer_layout = QVBoxLayout(root)

        header = QHBoxLayout()
        # self.tr(...) ВНУТРИ f-строки не находится pyside6-lupdate —
        # он ищет literal-аргумент прямо в вызове self.tr(...), а не
        # разбирает f-строки на составляющие; поэтому переведённый
        # текст собирается заранее отдельной переменной (см. также
        # CONTRIBUTING.md, раздел "Локализация")
        title_text = self.tr("Statistics")
        header.addWidget(QLabel(f"<h2>{title_text}</h2>"))
        header.addStretch()

        refresh_btn = QPushButton(self.tr("⟳ Refresh"))
        refresh_btn.clicked.connect(self.refresh)
        header.addWidget(refresh_btn)

        outer_layout.addLayout(header)

        tabs = QTabWidget()
        tabs.addTab(
            self._build_tab(self.stats, scope_label=self._build_scope_label()),
            self.tr("Current view"),
        )
        tabs.addTab(
            self._build_tab(self.library_stats, scope_label=None),
            self.tr("Whole library"),
        )

        outer_layout.addWidget(tabs, 1)

    def _build_tab(self, stats: Statistics, scope_label: QLabel | None) -> QWidget:
        """Одна вкладка (сводка + диаграммы) для заданного набора
        Statistics — используется и для "текущего вида", и для "всей
        библиотеки", отличаются только переданными данными и наличием
        подписи scope_label."""

        tab = QWidget()
        layout = QVBoxLayout(tab)

        if scope_label is not None:
            layout.addWidget(scope_label)

        layout.addWidget(self._build_summary_row(stats))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        charts_container = QWidget()
        grid = QGridLayout(charts_container)

        grid.addWidget(
            self._build_bar_chart(self.tr("Top models"), stats.top_models), 0, 0
        )
        grid.addWidget(
            self._build_bar_chart(self.tr("Top samplers"), stats.top_samplers), 0, 1
        )
        grid.addWidget(
            self._build_bar_chart(self.tr("Top LoRA"), stats.top_loras), 1, 0
        )
        grid.addWidget(
            self._build_bar_chart(
                self.tr("Rating distribution"),
                [(f"{rating}★", count) for rating, count in stats.rating_distribution],
            ),
            1, 1
        )
        grid.addWidget(
            self._build_histogram_chart(self.tr("CFG distribution"), stats.cfg_histogram),
            2, 0
        )
        grid.addWidget(
            self._build_histogram_chart(self.tr("Steps distribution"), stats.steps_histogram),
            2, 1
        )

        scroll.setWidget(charts_container)
        layout.addWidget(scroll, 1)

        return tab

    # --------------------------------------------------

    def _build_scope_label(self) -> QLabel:
        """Подпись над сводкой вкладки "Current view", поясняющая, что
        числа на ней относятся именно к текущей открытой папке с учётом
        активных фильтров, а не ко всей библиотеке (та показана на
        отдельной вкладке "Whole library")."""

        folder = self.gallery.current_folder

        if folder is None:
            text = self.tr("No folder open")
        else:
            text = self.tr("Folder: {folder} · {count} generations shown").format(
                folder=folder, count=self.stats.total_generations
            )

        label = QLabel(text)
        label.setStyleSheet("color: gray;")

        return label

    # --------------------------------------------------

    def _build_summary_row(self, stats: Statistics) -> QWidget:

        row = QWidget()
        layout = QHBoxLayout(row)

        layout.addWidget(self._summary_card(self.tr("Total generations"), str(stats.total_generations)))
        layout.addWidget(self._summary_card(self.tr("Favorites"), str(stats.total_favorites)))
        layout.addWidget(self._summary_card(self.tr("Average rating"), f"{stats.average_rating:.2f}"))

        return row

    @staticmethod
    def _summary_card(title: str, value: str) -> QWidget:

        card = QWidget()
        card.setObjectName("statSummaryCard")

        layout = QVBoxLayout(card)

        value_label = QLabel(value)
        value_label.setStyleSheet("font-size: 22px; font-weight: bold;")
        value_label.setAlignment(Qt.AlignCenter)

        title_label = QLabel(title)
        title_label.setAlignment(Qt.AlignCenter)

        layout.addWidget(value_label)
        layout.addWidget(title_label)

        return card

    # --------------------------------------------------

    def _build_bar_chart(self, title: str, data: list[tuple[str, int]]) -> QChartView:
        """Столбчатая диаграмма "категория -> количество" (топ-N
        моделей/сэмплеров/LoRA, распределение рейтингов)."""

        chart = QChart()
        chart.setTitle(title)

        if not data:
            return self._empty_chart_view(chart)

        bar_set = QBarSet(title)
        bar_set.append([count for _label, count in data])

        series = QBarSeries()
        series.append(bar_set)
        chart.addSeries(series)

        axis_x = QBarCategoryAxis()
        axis_x.append([str(label) for label, _count in data])
        chart.addAxis(axis_x, Qt.AlignBottom)
        series.attachAxis(axis_x)

        axis_y = QValueAxis()
        axis_y.setLabelFormat("%d")
        max_count = max((count for _label, count in data), default=1)
        axis_y.setRange(0, max(1, max_count))
        chart.addAxis(axis_y, Qt.AlignLeft)
        series.attachAxis(axis_y)

        chart.legend().hide()

        # подписи категорий (особенно длинные имена моделей/LoRA) на
        # QBarCategoryAxis часто обрезаются, если не помещаются —
        # показываем полное название + количество во всплывающей
        # подсказке при наведении на столбец
        self._attach_hover_tooltip(
            series, [f"{label}: {count}" for label, count in data]
        )

        return self._chart_view(chart)

    def _build_histogram_chart(self, title: str, buckets) -> QChartView:
        """Гистограмма (CFG/Steps) — те же корзины, что вернул
        GenerationRepository._sql_histogram / compute_statistics,
        просто отрисованные."""

        chart = QChart()
        chart.setTitle(title)

        if not buckets:
            return self._empty_chart_view(chart)

        bar_set = QBarSet(title)
        bar_set.append([bucket.count for bucket in buckets])

        series = QBarSeries()
        series.append(bar_set)
        chart.addSeries(series)

        axis_x = QBarCategoryAxis()
        axis_x.append([bucket.label for bucket in buckets])
        chart.addAxis(axis_x, Qt.AlignBottom)
        series.attachAxis(axis_x)

        axis_y = QValueAxis()
        axis_y.setLabelFormat("%d")
        max_count = max((bucket.count for bucket in buckets), default=1)
        axis_y.setRange(0, max(1, max_count))
        chart.addAxis(axis_y, Qt.AlignLeft)
        series.attachAxis(axis_y)

        chart.legend().hide()

        self._attach_hover_tooltip(
            series, [f"{bucket.label}: {bucket.count}" for bucket in buckets]
        )

        return self._chart_view(chart)

    @staticmethod
    def _attach_hover_tooltip(series: QBarSeries, full_labels: list[str]) -> None:
        """Показывает всплывающую подсказку с полным (не обрезанным)
        текстом категории при наведении на столбец диаграммы.

        QBarCategoryAxis обрезает длинные подписи (например, полные
        имена файлов моделей/LoRA), если они не помещаются под осью —
        подсказка остаётся единственным способом увидеть название
        целиком, не увеличивая окно."""

        def handle_hover(status: bool, index: int, _bar_set) -> None:

            if status and 0 <= index < len(full_labels):
                QToolTip.showText(QCursor.pos(), full_labels[index])
            else:
                QToolTip.hideText()

        series.hovered.connect(handle_hover)

    @staticmethod
    def _chart_view(chart: QChart) -> QChartView:

        view = QChartView(chart)
        view.setRenderHint(QPainter.Antialiasing)
        view.setMinimumHeight(280)

        return view

    def _empty_chart_view(self, chart: QChart) -> QChartView:
        """Диаграмма без данных — показываем пустой график вместо
        падения на пустом QBarCategoryAxis/QBarSet."""

        # см. комментарий у title_text в _build_ui — self.tr(...)
        # внутри f-строки не находится pyside6-lupdate
        no_data_text = self.tr("(no data)")
        chart.setTitle(f"{chart.title()} {no_data_text}")

        return self._chart_view(chart)

    # --------------------------------------------------

    def refresh(self) -> None:
        """Пересчитывает статистику обеих вкладок и перестраивает окно
        с нуля — проще и надёжнее, чем точечно обновлять уже
        построенные QChart-объекты."""

        self.stats = self.gallery.get_statistics()
        self.library_stats = self.gallery.get_library_statistics()
        self._build_ui()
