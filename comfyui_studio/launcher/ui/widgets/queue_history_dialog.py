"""
Диалог "Очередь и история" -- этап 8 дорожной карты ("Новая
функциональность"), пункт "очередь задач, история генераций". Прямое
расширение ComfyAPIClient.get_queue_items()/get_history() (плюс
delete_queue_item/clear_queue/interrupt/delete_history_item/
clear_history, все добавлены сюда же в core/comfy_api.py) -- этот
пункт не затронут ограничением clientId (см. подраздел "Окончательный
ответ" в этапе 7 и "По факту реализации" в начале этапа 8): очередь и
история читаются по обычному HTTP-опросу из этапа 6, WebSocket здесь
не участвует.

НЕмодальный диалог (show()/raise(), тот же паттерн, что и
AppSettingsDialog/собственное окно настроек PromptVault) -- открывается
кнопкой в верхней панели BrowserPage, пока ComfyUI запущен. Опрашивает
/queue и /history по таймеру, ТОЛЬКО пока сам диалог виден (см.
showEvent/hideEvent) -- нет смысла дёргать сеть, пока окно закрыто.
"""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.comfy_api import (
    ComfyAPIClient,
    count_history_outputs,
    count_steps_in_prompt,
    format_history_status,
    history_entry_graph,
)
from ...core.logging_setup import log

POLL_INTERVAL_MS = 2000

_STATUS_LABELS_RU = {
    "running": "Выполняется",
    "pending": "В очереди",
}


class QueueHistoryDialog(QDialog):
    def __init__(self, get_port_fn, loc=None, parent=None):
        super().__init__(parent)
        self.loc = loc
        self._get_port = get_port_fn
        self._api = ComfyAPIClient()

        self.setWindowTitle(self._tr("Очередь и история ComfyUI"))
        self.resize(720, 480)

        root = QVBoxLayout(self)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        # -- вкладка "Очередь" --------------------------------------------
        queue_tab = QWidget()
        queue_layout = QVBoxLayout(queue_tab)

        self.queue_table = QTableWidget(0, 3)
        self.queue_table.setHorizontalHeaderLabels(
            [self._tr("Статус"), self._tr("Prompt ID"), self._tr("Шагов")]
        )
        self.queue_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.queue_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.queue_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        queue_layout.addWidget(self.queue_table, 1)

        queue_btn_row = QHBoxLayout()
        self.queue_refresh_btn = QPushButton(self._tr("Обновить"))
        self.queue_refresh_btn.clicked.connect(self._refresh_queue)
        queue_btn_row.addWidget(self.queue_refresh_btn)
        self.queue_cancel_btn = QPushButton(self._tr("Снять выбранное"))
        self.queue_cancel_btn.clicked.connect(self._cancel_selected_queue_item)
        queue_btn_row.addWidget(self.queue_cancel_btn)
        self.queue_interrupt_btn = QPushButton(self._tr("Прервать текущее"))
        self.queue_interrupt_btn.clicked.connect(self._interrupt_running)
        queue_btn_row.addWidget(self.queue_interrupt_btn)
        self.queue_clear_btn = QPushButton(self._tr("Очистить очередь"))
        self.queue_clear_btn.clicked.connect(self._clear_queue)
        queue_btn_row.addWidget(self.queue_clear_btn)
        queue_btn_row.addStretch(1)
        queue_layout.addLayout(queue_btn_row)

        self.tabs.addTab(queue_tab, self._tr("Очередь"))

        # -- вкладка "История" ----------------------------------------------
        history_tab = QWidget()
        history_layout = QVBoxLayout(history_tab)

        self.history_table = QTableWidget(0, 4)
        self.history_table.setHorizontalHeaderLabels(
            [
                self._tr("Prompt ID"),
                self._tr("Статус"),
                self._tr("Изображений"),
                self._tr("Шагов"),
            ]
        )
        self.history_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        history_layout.addWidget(self.history_table, 1)

        history_btn_row = QHBoxLayout()
        self.history_refresh_btn = QPushButton(self._tr("Обновить"))
        self.history_refresh_btn.clicked.connect(self._refresh_history)
        history_btn_row.addWidget(self.history_refresh_btn)
        self.history_delete_btn = QPushButton(self._tr("Удалить выбранное"))
        self.history_delete_btn.clicked.connect(self._delete_selected_history_item)
        history_btn_row.addWidget(self.history_delete_btn)
        self.history_clear_btn = QPushButton(self._tr("Очистить историю"))
        self.history_clear_btn.clicked.connect(self._clear_history)
        history_btn_row.addWidget(self.history_clear_btn)
        history_btn_row.addStretch(1)
        history_layout.addLayout(history_btn_row)

        self.tabs.addTab(history_tab, self._tr("История"))

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._refresh_all)

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def retranslate_ui(self):
        self.setWindowTitle(self._tr("Очередь и история ComfyUI"))
        self.queue_table.setHorizontalHeaderLabels(
            [self._tr("Статус"), self._tr("Prompt ID"), self._tr("Шагов")]
        )
        self.queue_refresh_btn.setText(self._tr("Обновить"))
        self.queue_cancel_btn.setText(self._tr("Снять выбранное"))
        self.queue_interrupt_btn.setText(self._tr("Прервать текущее"))
        self.queue_clear_btn.setText(self._tr("Очистить очередь"))
        self.tabs.setTabText(0, self._tr("Очередь"))
        self.history_table.setHorizontalHeaderLabels(
            [
                self._tr("Prompt ID"),
                self._tr("Статус"),
                self._tr("Изображений"),
                self._tr("Шагов"),
            ]
        )
        self.history_refresh_btn.setText(self._tr("Обновить"))
        self.history_delete_btn.setText(self._tr("Удалить выбранное"))
        self.history_clear_btn.setText(self._tr("Очистить историю"))
        self.tabs.setTabText(1, self._tr("История"))

    # -- открытие/закрытие: опрос идёт только пока диалог виден -----------

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_all()
        self._timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    def open_and_raise(self):
        self.show()
        self.raise_()
        self.activateWindow()

    # -- опрос ------------------------------------------------------------

    def _current_port(self):
        return self._get_port() if self._get_port else None

    def _refresh_all(self):
        self._refresh_queue()
        self._refresh_history()

    def _refresh_queue(self):
        port = self._current_port()
        if port is None:
            self.queue_table.setRowCount(0)
            self.status_label.setText(self._tr("ComfyUI не запущен."))
            return
        items = self._api.get_queue_items(port=port)
        if items is None:
            self.status_label.setText(
                self._tr("Не удалось получить очередь -- ComfyUI не отвечает.")
            )
            return
        self.status_label.setText("")
        self.queue_table.setRowCount(len(items))
        for row, item in enumerate(items):
            status_key = item.get("status")
            status_text = _STATUS_LABELS_RU.get(status_key, status_key or "?")
            self.queue_table.setItem(row, 0, QTableWidgetItem(self._tr(status_text)))
            self.queue_table.setItem(row, 1, QTableWidgetItem(str(item.get("prompt_id") or "")))
            self.queue_table.setItem(row, 2, QTableWidgetItem(str(item.get("steps") or 0)))

    def _refresh_history(self):
        port = self._current_port()
        if port is None:
            self.history_table.setRowCount(0)
            return
        entries = self._api.get_history(limit=200, port=port)
        if entries is None:
            return
        # Самые новые -- сверху (см. get_history: порядок обычно
        # хронологический вставки, но не гарантирован ComfyUI -- при
        # отображении это не критично, просто удобнее видеть последнее
        # первым).
        entries = list(reversed(entries))
        self.history_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            self.history_table.setItem(row, 0, QTableWidgetItem(str(entry.get("id") or "")))
            self.history_table.setItem(
                row, 1, QTableWidgetItem(self._tr(format_history_status(entry)))
            )
            self.history_table.setItem(row, 2, QTableWidgetItem(str(count_history_outputs(entry))))
            graph = history_entry_graph(entry)
            steps = count_steps_in_prompt(graph) if graph else 0
            self.history_table.setItem(row, 3, QTableWidgetItem(str(steps)))

    # -- действия -----------------------------------------------------------

    def _selected_prompt_id(self, table: QTableWidget, column: int):
        rows = table.selectionModel().selectedRows() if table.selectionModel() else []
        if not rows:
            return None
        item = table.item(rows[0].row(), column)
        return item.text() if item is not None else None

    def _cancel_selected_queue_item(self):
        prompt_id = self._selected_prompt_id(self.queue_table, 1)
        if not prompt_id:
            return
        port = self._current_port()
        if port is None:
            return
        if not self._api.delete_queue_item(prompt_id, port=port):
            log.warning("Не удалось снять задание %s из очереди ComfyUI", prompt_id)
        self._refresh_queue()

    def _interrupt_running(self):
        port = self._current_port()
        if port is None:
            return
        if not self._api.interrupt(port=port):
            log.warning("Не удалось прервать текущее задание ComfyUI")
        self._refresh_queue()

    def _clear_queue(self):
        port = self._current_port()
        if port is None:
            return
        reply = QMessageBox.question(
            self,
            self._tr("Очистить очередь"),
            self._tr(
                "Снять все ожидающие задания из очереди? "
                "Задание, которое уже выполняется, не будет прервано."
            ),
        )
        if reply != QMessageBox.Yes:
            return
        if not self._api.clear_queue(port=port):
            log.warning("Не удалось очистить очередь ComfyUI")
        self._refresh_queue()

    def _delete_selected_history_item(self):
        prompt_id = self._selected_prompt_id(self.history_table, 0)
        if not prompt_id:
            return
        port = self._current_port()
        if port is None:
            return
        if not self._api.delete_history_item(prompt_id, port=port):
            log.warning("Не удалось удалить запись %s из истории ComfyUI", prompt_id)
        self._refresh_history()

    def _clear_history(self):
        port = self._current_port()
        if port is None:
            return
        reply = QMessageBox.question(
            self,
            self._tr("Очистить историю"),
            self._tr("Полностью очистить историю генераций ComfyUI?"),
        )
        if reply != QMessageBox.Yes:
            return
        if not self._api.clear_history(port=port):
            log.warning("Не удалось очистить историю ComfyUI")
        self._refresh_history()
