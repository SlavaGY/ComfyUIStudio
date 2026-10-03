"""Раздел "История промптов" единого дерева настроек: все запросы к
Генератору промптов (кнопка в Imagine) с поиском и сортировкой.

Данные лежат в SQLite-файле %APPDATA%\\ComfyUIStudio\\promptgen_history.db
(см. comfyui_studio/promptgen_history.py): пишет их Imagine -- по одной
записи на каждую задачу, а эта страница только читает (и по просьбе
пользователя удаляет). Поэтому:

  * поиск и сортировка выполняются в самой БД (по всей истории, а не по
    загруженной странице), результат отдаётся страницами по PAGE_SIZE;
  * пока страница видна, раз в REFRESH_MS проверяется «отпечаток» таблицы
    (число записей + максимальный id): Imagine -- другой процесс, и новая
    генерация должна появиться без нажатия «Обновить». Выбранная запись
    при обновлении сохраняется;
  * ошибки БД показываются строкой статуса и не роняют окно настроек.

Сортировка -- щелчком по заголовку столбца (повторный щелчок меняет
направление). Заголовки сами порядок не меняют: мы слушаем sectionClicked
и пересобираем выборку запросом с ORDER BY.

Низ страницы -- подробности выбранной записи во вкладках: сводка (время,
токены, тайминги, модель), полный запрос, ответ.

Все строки на этой странице -- исходные на русском (см. пояснение в
general_page.py про TRANSLATIONS/loc.tr()).
"""

from __future__ import annotations

import datetime
import re

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from comfyui_studio import promptgen_history as history

PAGE_SIZE = 100
REFRESH_MS = 3000
SEARCH_DEBOUNCE_MS = 250
PREVIEW_CHARS = 200

# (ключ сортировки в promptgen_history.SORT_COLUMNS, выравнивание вправо?)
_COLUMNS = [
    ("started_at", False),
    ("state", False),
    ("user_text", False),
    ("image_name", False),
    ("total_tokens", True),
    ("total_s", True),
    ("tok_per_s", True),
    ("output_text", False),
]
_COL_TEXT = 2
_COL_OUTPUT = 7

_WS_RE = re.compile(r"\s+")


def _fmt_time(ts) -> str:
    if ts is None:
        return ""
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _preview(text: str, limit: int = PREVIEW_CHARS) -> str:
    text = _WS_RE.sub(" ", text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _num(value, fmt: str) -> str:
    return "—" if value is None else format(value, fmt)


class PromptHistorySettingsPage(QWidget):

    def __init__(self, loc=None, parent=None):
        super().__init__(parent)
        self.loc = loc
        self._sort_key = "started_at"
        self._descending = True
        self._page = 0
        self._total = 0
        self._ids: list[int] = []
        self._fingerprint = None
        self._error = ""

        root = QVBoxLayout(self)

        self.intro = QLabel()
        self.intro.setWordWrap(True)
        self.intro.setObjectName("mutedLabel")
        root.addWidget(self.intro)

        # -- поиск -------------------------------------------------------
        search_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setClearButtonEnabled(True)
        search_row.addWidget(self.search_edit, 1)
        self.refresh_btn = QPushButton()
        self.refresh_btn.clicked.connect(self.reload)
        search_row.addWidget(self.refresh_btn)
        root.addLayout(search_row)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._on_search_applied)
        self.search_edit.textChanged.connect(lambda _t: self._search_timer.start())

        # -- таблица + подробности (разделитель) -----------------------------
        splitter = QSplitter(Qt.Orientation.Vertical)
        # в маленьком окне страница прокручивается (см. _scrollable в
        # app_settings_dialog.py), а не сжимает таблицу до нечитаемого вида
        splitter.setMinimumHeight(380)
        root.addWidget(splitter, 1)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setStretchLastSection(False)
        for col in range(len(_COLUMNS)):
            mode = (
                QHeaderView.ResizeMode.Stretch
                if col in (_COL_TEXT, _COL_OUTPUT)
                else QHeaderView.ResizeMode.ResizeToContents
            )
            header.setSectionResizeMode(col, mode)
        header.sectionClicked.connect(self._on_header_clicked)
        self._update_sort_indicator()
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        splitter.addWidget(self.table)

        self.tabs = QTabWidget()
        self.info_view = self._readonly_view()
        self.prompt_view = self._readonly_view()
        self.output_view = self._readonly_view()
        self.tabs.addTab(self.info_view, "")
        self.tabs.addTab(self.prompt_view, "")
        self.tabs.addTab(self.output_view, "")
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        # -- низ: страницы, сводка, действия ---------------------------------
        nav = QHBoxLayout()
        self.prev_btn = QPushButton("‹")
        self.prev_btn.setFixedWidth(36)
        self.prev_btn.clicked.connect(lambda: self._goto_page(self._page - 1))
        self.next_btn = QPushButton("›")
        self.next_btn.setFixedWidth(36)
        self.next_btn.clicked.connect(lambda: self._goto_page(self._page + 1))
        self.page_label = QLabel()
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.page_label)
        nav.addWidget(self.next_btn)
        nav.addStretch(1)
        self.copy_btn = QPushButton()
        self.copy_btn.clicked.connect(self._copy_current_tab)
        self.delete_btn = QPushButton()
        self.delete_btn.clicked.connect(self._delete_selected)
        self.clear_btn = QPushButton()
        self.clear_btn.clicked.connect(self._clear_all)
        nav.addWidget(self.copy_btn)
        nav.addWidget(self.delete_btn)
        nav.addWidget(self.clear_btn)
        root.addLayout(nav)

        self.stats_label = QLabel()
        self.stats_label.setObjectName("mutedLabel")
        self.stats_label.setWordWrap(True)
        root.addWidget(self.stats_label)

        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._poll)

        self.retranslate_ui()
        self.reload()

    # -- построение --------------------------------------------------------

    @staticmethod
    def _readonly_view() -> QPlainTextEdit:
        view = QPlainTextEdit()
        view.setReadOnly(True)
        return view

    # -- видимость: авто-обновление только пока страница на экране ------------

    def showEvent(self, event):
        super().showEvent(event)
        self._poll()
        self._timer.start()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    # -- загрузка данных -----------------------------------------------------

    def _poll(self):
        try:
            current = history.fingerprint()
        except Exception as exc:
            self._set_error(exc)
            return
        if current != self._fingerprint:
            self.reload()

    def reload(self):
        """Перечитывает текущую страницу с учётом поиска и сортировки;
        выбранные записи остаются выбранными, если они ещё есть."""
        keep = set(self._selected_ids())
        search = self.search_edit.text()
        try:
            self._fingerprint = history.fingerprint()
            self._total = history.count(search)
            last_page = max(0, (self._total - 1) // PAGE_SIZE)
            self._page = min(self._page, last_page)
            rows = history.query(
                search,
                self._sort_key,
                self._descending,
                limit=PAGE_SIZE,
                offset=self._page * PAGE_SIZE,
            )
            stats = history.stats(search)
        except Exception as exc:
            self._set_error(exc)
            return
        self._error = ""
        self._fill_table(rows, keep)
        self._update_footer(stats, bool(search.strip()))

    def _set_error(self, exc: Exception):
        self._error = f"{exc}"
        self.stats_label.setText(self._tr("Не удалось прочитать историю: {}").format(exc))

    def _fill_table(self, rows: list[dict], keep: set[int]):
        table = self.table
        table.blockSignals(True)
        table.setRowCount(len(rows))
        self._ids = [r["id"] for r in rows]
        for row_index, rec in enumerate(rows):
            for col, text, tip in self._cells(rec):
                item = QTableWidgetItem(text)
                if tip:
                    item.setToolTip(tip)
                if _COLUMNS[col][1]:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, rec["id"])
                table.setItem(row_index, col, item)
            if rec["id"] in keep:
                table.selectRow(row_index)
        table.blockSignals(False)
        self._on_selection_changed()

    def _cells(self, rec: dict):
        """(колонка, текст, подсказка) для строки таблицы."""
        if rec["total_tokens"] is not None:
            tokens = f"{rec['total_tokens']} ({rec['prompt_tokens']} + {rec['completion_tokens']})"
        elif rec["completion_tokens"] is not None:
            tokens = f"? + {rec['completion_tokens']}"
        else:
            tokens = "—"
        image = rec["image_name"] or (self._tr("да") if rec["has_image"] else "")
        user_tip = (rec["user_text"] or "")[:1500]
        out_tip = (rec["output_text"] or rec["error"] or "")[:1500]
        yield 0, _fmt_time(rec["started_at"]), ""
        yield 1, self._state_label(rec["state"]), rec["error"][:1500]
        yield 2, _preview(rec["user_text"]), user_tip
        yield 3, image, rec["image_name"]
        yield 4, tokens, self._tr("всего (запрос + ответ)")
        yield 5, _num(rec["total_s"], ".1f"), ""
        yield 6, _num(rec["tok_per_s"], ".1f"), ""
        yield 7, _preview(rec["output_text"] or rec["error"]), out_tip

    def _state_label(self, state: str) -> str:
        return {
            "done": self._tr("Готово"),
            "error": self._tr("Ошибка"),
            "cancelled": self._tr("Отмена"),
        }.get(state, state)

    def _update_footer(self, stats: dict, filtered: bool):
        pages = max(1, -(-self._total // PAGE_SIZE))
        self.page_label.setText(
            self._tr("Стр. {} из {} · записей: {}").format(self._page + 1, pages, self._total)
        )
        self.prev_btn.setEnabled(self._page > 0)
        self.next_btn.setEnabled(self._page + 1 < pages)
        self.clear_btn.setEnabled(self._fingerprint is not None and self._fingerprint[0] > 0)
        avg_t, avg_r = stats.get("avg_total_s"), stats.get("avg_tok_per_s")
        text = self._tr("{}: {} · токенов всего: {}").format(
            self._tr("Найдено") if filtered else self._tr("Всего"), stats["n"], stats["tokens"]
        )
        if avg_t is not None:
            text += self._tr(" · среднее время: {:.1f} с").format(avg_t)
        if avg_r is not None:
            text += self._tr(" · средняя скорость: {:.1f} ток/с").format(avg_r)
        self.stats_label.setText(text)

    # -- поиск, сортировка, страницы -----------------------------------------

    def _on_search_applied(self):
        self._page = 0
        self.reload()

    def _on_header_clicked(self, col: int):
        key = _COLUMNS[col][0]
        if key == self._sort_key:
            self._descending = not self._descending
        else:
            self._sort_key = key
            # даты и числа удобнее сначала «больше», текст -- по алфавиту
            self._descending = key not in ("state", "user_text", "image_name", "output_text")
        self._update_sort_indicator()
        self._page = 0
        self.reload()

    def _update_sort_indicator(self):
        col = next(i for i, (key, _r) in enumerate(_COLUMNS) if key == self._sort_key)
        order = Qt.SortOrder.DescendingOrder if self._descending else Qt.SortOrder.AscendingOrder
        self.table.horizontalHeader().setSortIndicator(col, order)

    def _goto_page(self, page: int):
        self._page = max(0, page)
        self.reload()

    # -- выбор и подробности --------------------------------------------------

    def _selected_ids(self) -> list[int]:
        rows = sorted({i.row() for i in self.table.selectedItems()})
        return [self._ids[r] for r in rows if r < len(self._ids)]

    def _on_selection_changed(self):
        ids = self._selected_ids()
        self.delete_btn.setEnabled(bool(ids))
        self.copy_btn.setEnabled(len(ids) == 1)
        if len(ids) != 1:
            for view in (self.info_view, self.prompt_view, self.output_view):
                view.setPlainText("")
            return
        try:
            rec = history.get(ids[0])
        except Exception as exc:
            self._set_error(exc)
            return
        if rec is None:
            return
        self.info_view.setPlainText(self._describe(rec))
        self.prompt_view.setPlainText(rec["full_prompt"])
        output = rec["output_text"]
        if rec["error"]:
            output = (output + "\n\n" if output else "") + self._tr("Ошибка:") + " " + rec["error"]
        if rec["raw_output"]:
            output += "\n\n— " + self._tr("Сырой ответ модели (с рассуждениями)") + " —\n" + rec["raw_output"]
        self.output_view.setPlainText(output)

    def _describe(self, rec: dict) -> str:
        tr = self._tr
        started, finished = rec["started_at"], rec["finished_at"]
        lines = [
            f"{tr('Начало')}: {_fmt_time(started)}",
            f"{tr('Конец')}: {_fmt_time(finished)}",
            f"{tr('Статус')}: {self._state_label(rec['state'])}",
        ]
        if rec["error"]:
            lines.append(f"{tr('Ошибка')}: {rec['error']}")
        if rec["has_image"]:
            lines.append(f"{tr('Изображение')}: {rec['image_name'] or tr('да (имя неизвестно)')}")
        else:
            lines.append(f"{tr('Изображение')}: {tr('нет')}")

        source = {
            "usage": tr("точно, от llama-server"),
            "timings": tr("по данным llama-server"),
            "chunks": tr("приблизительно: число принятых частей ответа"),
        }.get(rec["tokens_source"], "")
        lines.append(
            f"{tr('Токены')}: {tr('запрос')} {_num(rec['prompt_tokens'], 'd')}, "
            f"{tr('ответ')} {_num(rec['completion_tokens'], 'd')}, "
            f"{tr('всего')} {_num(rec['total_tokens'], 'd')}" + (f" ({source})" if source else "")
        )
        if rec["truncated"]:
            lines.append(tr("⚠ Ответ обрезан лимитом max_tokens"))

        lines += ["", tr("Хронология (секунды от начала задачи):")]
        t = 0.0
        lines.append(f"  +{t:6.1f}  {tr('старт задачи')}")
        if rec["load_s"] is not None:
            t += rec["load_s"]
            lines.append(f"  +{t:6.1f}  {tr('модель загружена')} ({tr('загрузка')} {rec['load_s']:.1f} {tr('с')})")
        if rec["ttft_s"] is not None:
            t += rec["ttft_s"]
            lines.append(f"  +{t:6.1f}  {tr('первый токен')} ({tr('обработка запроса')} {rec['ttft_s']:.1f} {tr('с')})")
        if rec["gen_s"] is not None:
            t += rec["gen_s"]
            lines.append(f"  +{t:6.1f}  {tr('ответ получен')} ({tr('генерация')} {rec['gen_s']:.1f} {tr('с')})")
        if rec["total_s"] is not None:
            lines.append(f"  +{rec['total_s']:6.1f}  {tr('конец задачи')}")
        lines.append(f"{tr('Скорость генерации')}: {_num(rec['tok_per_s'], '.1f')} {tr('ток/с')}")

        lines += [
            "",
            f"{tr('Модель')}: {rec['model_name'] or '—'}",
            f"mmproj: {rec['mmproj_name'] or '—'}",
            f"{tr('Контекст')}: {rec['ctx_size'] if rec['ctx_size'] else '—'}",
            f"ID: {rec['id']} / {rec['job_id']}",
        ]
        return "\n".join(lines)

    # -- действия -----------------------------------------------------------

    def _copy_current_tab(self):
        view = self.tabs.currentWidget()
        if isinstance(view, QPlainTextEdit):
            QGuiApplication.clipboard().setText(view.toPlainText())

    def _delete_selected(self):
        ids = self._selected_ids()
        if not ids:
            return
        answer = QMessageBox.question(
            self,
            self._tr("Удаление записей"),
            self._tr("Удалить выбранные записи из истории: {}?").format(len(ids)),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            history.delete(ids)
        except Exception as exc:
            self._set_error(exc)
            return
        self.reload()

    def _clear_all(self):
        answer = QMessageBox.question(
            self,
            self._tr("Очистка истории"),
            self._tr("Удалить ВСЮ историю запросов? Это нельзя отменить."),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            history.clear()
        except Exception as exc:
            self._set_error(exc)
            return
        self._page = 0
        self.reload()

    # -- прочее ---------------------------------------------------------

    def _tr(self, text):
        return self.loc.tr(text) if self.loc is not None else text

    def retranslate_ui(self):
        self.intro.setText(
            self._tr(
                "Все запросы, отправленные Генератору промптов из Imagine: полный текст запроса, "
                "ответ модели, токены и время. Щёлкните по заголовку столбца, чтобы отсортировать; "
                "в поиске можно указать несколько слов — найдутся записи, где есть все."
            )
        )
        self.search_edit.setPlaceholderText(self._tr("Поиск по запросу, ответу, имени изображения…"))
        self.refresh_btn.setText(self._tr("Обновить"))
        self.copy_btn.setText(self._tr("Копировать вкладку"))
        self.delete_btn.setText(self._tr("Удалить выбранные"))
        self.clear_btn.setText(self._tr("Очистить всё…"))
        self.tabs.setTabText(0, self._tr("Сводка"))
        self.tabs.setTabText(1, self._tr("Полный запрос"))
        self.tabs.setTabText(2, self._tr("Ответ"))
        self.table.setHorizontalHeaderLabels([
            self._tr("Дата и время"),
            self._tr("Статус"),
            self._tr("Запрос"),
            self._tr("Изображение"),
            self._tr("Токены"),
            self._tr("Время, с"),
            self._tr("Ток/с"),
            self._tr("Ответ"),
        ])
        if not self._error:
            self.reload()
