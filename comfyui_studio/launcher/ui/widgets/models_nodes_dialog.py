"""
Диалог "Модели и VRAM" / "Custom nodes" -- этап 8 дорожной карты
("Новая функциональность"), пункты "какие модели используются,
загрузка/выгрузка моделей" и "состояние custom nodes". Оба -- через
уже существующие ComfyAPIClient.get_system_stats()/get_object_info()
(этап 6), не затронуты ограничением clientId (тот же принцип, что и у
QueueHistoryDialog -- обычный HTTP-опрос, WebSocket здесь не
участвует).

ЧЕСТНАЯ ОГОВОРКА (важно понимать при чтении этого файла и при
использовании диалога): у ComfyUI НЕТ публичного эндпоинта "какие
модели прямо сейчас загружены в VRAM" -- ни в /system_stats, ни где-
либо ещё в стандартном REST API. /system_stats даёт только суммарную
VRAM per-device (использовано/свободно), не разбивку по конкретным
моделям. Поэтому вкладка "Модели и VRAM" показывает СУММАРНУЮ VRAM
устройства как прокси-сигнал загрузки, а не список конкретных
загруженных чекпоинтов/LoRA -- в логе ComfyUI такая по-модельная
разбивка иногда мелькает (см. debug-строки сторонних плагинов вроде
comfy-aimdo про VBAR/Actual Resident VRAM), но это внутренняя
диагностика конкретного стороннего плагина управления памятью, а не
что-то, что можно спросить у сервера через HTTP.

Аналогично "custom nodes state": /object_info не сообщает, из какого
custom_nodes-пакета пришла конкретная нода -- только class_type,
category, входы/выходы. Поэтому вкладка "Custom nodes" -- это
поисковый список ВСЕХ зарегистрированных нод (core + custom вместе,
неразличимо), а не отчёт "что установлено по пакетам".

НЕмодальный диалог (show()/raise(), тот же паттерн, что и
QueueHistoryDialog/AppSettingsDialog). /system_stats дешёвый --
опрашивается по таймеру, пока диалог виден (как в QueueHistoryDialog).
/object_info ТЯЖЁЛЫЙ (см. предупреждение в docstring
ComfyAPIClient.get_object_info) -- запрашивается только вручную
(кнопка "Обновить"/при первом открытии вкладки "Custom nodes"), НЕ по
таймеру.
"""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.comfy_api import ComfyAPIClient, device_vram_usage, summarize_object_info
from ...core.logging_setup import log

SYSTEM_STATS_POLL_INTERVAL_MS = 3000


def _format_gib(num_bytes):
    if not isinstance(num_bytes, (int, float)) or isinstance(num_bytes, bool):
        return "?"
    return f"{num_bytes / (1024 ** 3):.2f} ГБ"


class ModelsNodesDialog(QDialog):
    def __init__(self, get_port_fn, loc=None, parent=None):
        super().__init__(parent)
        self.loc = loc
        self._get_port = get_port_fn
        self._api = ComfyAPIClient()
        # Кэш /object_info (этап 8 -- намеренно НЕ опрашивается по
        # таймеру, см. docstring модуля выше): None -- ещё не
        # запрашивали в этой сессии диалога.
        self._object_info_cache = None

        self.setWindowTitle(self._tr("Модели, VRAM и ноды ComfyUI"))
        self.resize(760, 520)

        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        # -- вкладка "Модели и VRAM" --------------------------------------
        vram_tab = QWidget()
        vram_layout = QVBoxLayout(vram_tab)

        self.system_info_label = QLabel("")
        self.system_info_label.setWordWrap(True)
        vram_layout.addWidget(self.system_info_label)

        self.vram_note_label = QLabel(
            self._tr(
                "ComfyUI не сообщает, какие именно модели сейчас в VRAM -- "
                "только суммарную загрузку по устройству. Значения ниже -- "
                "прокси-сигнал, а не список конкретных моделей."
            )
        )
        self.vram_note_label.setWordWrap(True)
        vram_layout.addWidget(self.vram_note_label)

        self.devices_table = QTableWidget(0, 4)
        self.devices_table.setHorizontalHeaderLabels(
            [
                self._tr("Устройство"),
                self._tr("Тип"),
                self._tr("VRAM занято / всего"),
                self._tr("%"),
            ]
        )
        self.devices_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.devices_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.devices_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        vram_layout.addWidget(self.devices_table, 1)

        vram_btn_row = QHBoxLayout()
        self.vram_refresh_btn = QPushButton(self._tr("Обновить"))
        self.vram_refresh_btn.clicked.connect(self._refresh_system_stats)
        vram_btn_row.addWidget(self.vram_refresh_btn)
        vram_btn_row.addStretch(1)
        vram_layout.addLayout(vram_btn_row)

        self.tabs.addTab(vram_tab, self._tr("Модели и VRAM"))

        # -- вкладка "Custom nodes" ------------------------------------------
        nodes_tab = QWidget()
        nodes_layout = QVBoxLayout(nodes_tab)

        self.nodes_note_label = QLabel(
            self._tr(
                "Список всех зарегистрированных нод (встроенных и custom "
                "вместе) -- ComfyUI не сообщает, из какого пакета пришла "
                "конкретная нода."
            )
        )
        self.nodes_note_label.setWordWrap(True)
        nodes_layout.addWidget(self.nodes_note_label)

        search_row = QHBoxLayout()
        self.nodes_search_label = QLabel(self._tr("Поиск:"))
        search_row.addWidget(self.nodes_search_label)
        self.nodes_search_edit = QLineEdit()
        self.nodes_search_edit.textChanged.connect(self._apply_node_filter)
        search_row.addWidget(self.nodes_search_edit, 1)
        nodes_layout.addLayout(search_row)

        self.nodes_count_label = QLabel("")
        nodes_layout.addWidget(self.nodes_count_label)

        self.nodes_table = QTableWidget(0, 3)
        self.nodes_table.setHorizontalHeaderLabels(
            [self._tr("Class type"), self._tr("Название"), self._tr("Категория")]
        )
        self.nodes_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.nodes_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.nodes_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        nodes_layout.addWidget(self.nodes_table, 1)

        nodes_btn_row = QHBoxLayout()
        self.nodes_refresh_btn = QPushButton(self._tr("Обновить (может занять время)"))
        self.nodes_refresh_btn.clicked.connect(self._refresh_object_info)
        nodes_btn_row.addWidget(self.nodes_refresh_btn)
        nodes_btn_row.addStretch(1)
        nodes_layout.addLayout(nodes_btn_row)

        self.tabs.addTab(nodes_tab, self._tr("Custom nodes"))

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self._timer = QTimer(self)
        self._timer.setInterval(SYSTEM_STATS_POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._refresh_system_stats)

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def retranslate_ui(self):
        self.setWindowTitle(self._tr("Модели, VRAM и ноды ComfyUI"))
        self.vram_note_label.setText(
            self._tr(
                "ComfyUI не сообщает, какие именно модели сейчас в VRAM -- "
                "только суммарную загрузку по устройству. Значения ниже -- "
                "прокси-сигнал, а не список конкретных моделей."
            )
        )
        self.devices_table.setHorizontalHeaderLabels(
            [
                self._tr("Устройство"),
                self._tr("Тип"),
                self._tr("VRAM занято / всего"),
                self._tr("%"),
            ]
        )
        self.vram_refresh_btn.setText(self._tr("Обновить"))
        self.tabs.setTabText(0, self._tr("Модели и VRAM"))
        self.nodes_note_label.setText(
            self._tr(
                "Список всех зарегистрированных нод (встроенных и custom "
                "вместе) -- ComfyUI не сообщает, из какого пакета пришла "
                "конкретная нода."
            )
        )
        self.nodes_search_label.setText(self._tr("Поиск:"))
        self.nodes_table.setHorizontalHeaderLabels(
            [self._tr("Class type"), self._tr("Название"), self._tr("Категория")]
        )
        self.nodes_refresh_btn.setText(self._tr("Обновить (может занять время)"))
        self.tabs.setTabText(1, self._tr("Custom nodes"))
        self._update_nodes_count_label()

    # -- открытие/закрытие -------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_system_stats()
        self._timer.start()
        if self._object_info_cache is None:
            self._refresh_object_info()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    def open_and_raise(self):
        self.show()
        self.raise_()
        self.activateWindow()

    # -- вкладка "Модели и VRAM" --------------------------------------------

    def _current_port(self):
        return self._get_port() if self._get_port else None

    def _refresh_system_stats(self):
        port = self._current_port()
        if port is None:
            self.devices_table.setRowCount(0)
            self.system_info_label.setText("")
            self.status_label.setText(self._tr("ComfyUI не запущен."))
            return
        stats = self._api.get_system_stats(port=port)
        if stats is None:
            self.status_label.setText(
                self._tr("Не удалось получить статистику -- ComfyUI не отвечает.")
            )
            return
        self.status_label.setText("")

        info_parts = []
        if stats.os:
            info_parts.append(f"OS: {stats.os}")
        if stats.comfyui_version:
            info_parts.append(f"ComfyUI: {stats.comfyui_version}")
        if stats.python_version:
            info_parts.append(f"Python: {stats.python_version}")
        if stats.pytorch_version:
            info_parts.append(f"PyTorch: {stats.pytorch_version}")
        if isinstance(stats.ram_total, (int, float)) and isinstance(
            stats.ram_free, (int, float)
        ):
            ram_used = max(stats.ram_total - stats.ram_free, 0)
            info_parts.append(
                f"RAM: {_format_gib(ram_used)} / {_format_gib(stats.ram_total)}"
            )
        self.system_info_label.setText("  |  ".join(info_parts))

        devices = stats.devices or []
        self.devices_table.setRowCount(len(devices))
        for row, device in enumerate(devices):
            name = device.get("name", "?") if isinstance(device, dict) else "?"
            dtype = device.get("type", "?") if isinstance(device, dict) else "?"
            self.devices_table.setItem(row, 0, QTableWidgetItem(str(name)))
            self.devices_table.setItem(row, 1, QTableWidgetItem(str(dtype)))
            usage = device_vram_usage(device)
            if usage is None:
                self.devices_table.setItem(row, 2, QTableWidgetItem("?"))
                self.devices_table.setItem(row, 3, QTableWidgetItem("?"))
            else:
                used, total, pct = usage
                self.devices_table.setItem(
                    row, 2, QTableWidgetItem(f"{_format_gib(used)} / {_format_gib(total)}")
                )
                self.devices_table.setItem(row, 3, QTableWidgetItem(f"{pct:.0f}%"))

    # -- вкладка "Custom nodes" ----------------------------------------------

    def _refresh_object_info(self):
        port = self._current_port()
        if port is None:
            self.status_label.setText(self._tr("ComfyUI не запущен."))
            return
        self.status_label.setText(self._tr("Загружаю список нод..."))
        info = self._api.get_object_info(port=port)
        if info is None:
            self.status_label.setText(
                self._tr("Не удалось получить список нод -- ComfyUI не отвечает.")
            )
            return
        self.status_label.setText("")
        self._object_info_cache = summarize_object_info(info)
        self._apply_node_filter()

    def _apply_node_filter(self):
        self._update_nodes_count_label()
        rows = self._object_info_cache or []
        query = self.nodes_search_edit.text().strip().lower()
        if query:
            rows = [
                r
                for r in rows
                if query in r["class_type"].lower()
                or query in r["display_name"].lower()
                or query in r["category"].lower()
            ]
        self.nodes_table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            self.nodes_table.setItem(row, 0, QTableWidgetItem(item["class_type"]))
            self.nodes_table.setItem(row, 1, QTableWidgetItem(item["display_name"]))
            self.nodes_table.setItem(row, 2, QTableWidgetItem(item["category"]))

    def _update_nodes_count_label(self):
        total = len(self._object_info_cache) if self._object_info_cache else 0
        self.nodes_count_label.setText(
            self._tr("Всего зарегистрировано нод: {n}").format(n=total)
        )
