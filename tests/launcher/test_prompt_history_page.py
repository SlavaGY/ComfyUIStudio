"""Тесты вкладки «История промптов» (launcher/ui/settings/prompt_history_page.py)
и её подключения к AppSettingsDialog. Qt -- без экрана; база и общая папка
перенаправлены во временный каталог."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from comfyui_studio import promptgen_history as ph  # noqa: E402
from comfyui_studio import shared_promptgen as sp  # noqa: E402
from comfyui_studio.i18n import TRANSLATIONS  # noqa: E402
from comfyui_studio.launcher.ui.settings import prompt_history_page as page_mod  # noqa: E402
from comfyui_studio.launcher.ui.settings.prompt_history_page import (  # noqa: E402
    PromptHistorySettingsPage,
)


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def shared(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "SHARED_DIR", str(tmp_path / "shared"))
    monkeypatch.setattr(sp, "SHARED_PROMPTGEN_PATH", str(tmp_path / "shared" / "prompt_generator.json"))


def add(**kw):
    base = {
        "started_at": 1_700_000_000, "state": "done", "user_text": "cat", "full_prompt": "P: cat",
        "output_text": "A cat", "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
        "tokens_source": "usage", "total_s": 10.0, "load_s": 6.0, "ttft_s": 1.0, "gen_s": 3.0,
        "tok_per_s": 20.0, "model_name": "m.gguf",
    }
    base.update(kw)
    return ph.add_record(base)


def column(page, col):
    return [page.table.item(r, col).text() for r in range(page.table.rowCount())]


def click_header(page, col):
    page.table.horizontalHeader().sectionClicked.emit(col)


def test_lists_records_newest_first(qapp, shared):
    add(started_at=100, user_text="old")
    add(started_at=200, user_text="new")
    page = PromptHistorySettingsPage()
    assert column(page, 2) == ["new", "old"]
    assert "2" in page.page_label.text()


def test_search_filters_with_cyrillic_and_resets_page(qapp, shared):
    add(user_text="Кот на крыше")
    add(user_text="Собака")
    page = PromptHistorySettingsPage()
    page.search_edit.setText("КОТ")
    page._on_search_applied()  # без ожидания debounce-таймера
    assert column(page, 2) == ["Кот на крыше"]
    assert page.stats_label.text().startswith("Найдено")


def test_header_click_sorts_and_toggles_direction(qapp, shared):
    add(user_text="b", total_tokens=50)
    add(user_text="a", total_tokens=10)
    add(user_text="c", total_tokens=30)
    page = PromptHistorySettingsPage()
    click_header(page, 4)  # токены: сначала больше
    assert column(page, 2) == ["b", "c", "a"]
    click_header(page, 4)
    assert column(page, 2) == ["a", "c", "b"]
    click_header(page, 2)  # текст: по алфавиту
    assert column(page, 2) == ["a", "b", "c"]
    header = page.table.horizontalHeader()
    assert header.sortIndicatorSection() == 2
    assert header.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder


def test_selecting_a_row_shows_details(qapp, shared):
    add(user_text="кот", full_prompt="FULL PROMPT", output_text="OUT", image_name="a.png", has_image=1)
    page = PromptHistorySettingsPage()
    page.table.selectRow(0)
    info = page.info_view.toPlainText()
    assert "a.png" in info and "15" in info and "m.gguf" in info
    assert "+  10.0" in info                      # хронология: конец задачи
    assert page.prompt_view.toPlainText() == "FULL PROMPT"
    assert page.output_view.toPlainText() == "OUT"
    assert page.copy_btn.isEnabled() and page.delete_btn.isEnabled()


def test_error_record_shows_error_in_output_tab(qapp, shared):
    add(state="error", error="llama-server упал", output_text="", total_tokens=None,
        prompt_tokens=None, completion_tokens=None, tok_per_s=None)
    page = PromptHistorySettingsPage()
    page.table.selectRow(0)
    assert "llama-server упал" in page.output_view.toPlainText()
    assert column(page, 1) == ["Ошибка"]


def test_delete_selected_asks_for_confirmation(qapp, shared, monkeypatch):
    add(user_text="keep")
    add(user_text="drop", started_at=1_800_000_000)
    page = PromptHistorySettingsPage()
    page.table.selectRow(0)  # самая новая -- "drop"
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    page._delete_selected()
    assert ph.count() == 2
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    page._delete_selected()
    assert column(page, 2) == ["keep"]


def test_clear_all(qapp, shared, monkeypatch):
    add()
    add()
    page = PromptHistorySettingsPage()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    page._clear_all()
    assert page.table.rowCount() == 0 and not page.clear_btn.isEnabled()


def test_paging(qapp, shared, monkeypatch):
    monkeypatch.setattr(page_mod, "PAGE_SIZE", 3)
    for i in range(7):
        add(started_at=1000 + i, user_text=str(i))
    page = PromptHistorySettingsPage()
    assert column(page, 2) == ["6", "5", "4"] and not page.prev_btn.isEnabled()
    page.next_btn.click()
    page.next_btn.click()
    assert column(page, 2) == ["0"] and not page.next_btn.isEnabled()
    page.prev_btn.click()
    assert column(page, 2) == ["3", "2", "1"]


def test_poll_picks_up_records_written_by_another_process(qapp, shared):
    page = PromptHistorySettingsPage()
    assert page.table.rowCount() == 0
    add(user_text="from imagine")
    page._poll()
    assert column(page, 2) == ["from imagine"]


def test_selection_survives_refresh(qapp, shared):
    add(started_at=1, user_text="a")
    add(started_at=2, user_text="b")
    page = PromptHistorySettingsPage()
    page.table.selectRow(1)  # "a"
    add(started_at=3, user_text="c")
    page._poll()
    assert [page.table.item(i.row(), 2).text() for i in page.table.selectedItems()][:1] == ["a"]


def test_broken_database_is_reported_not_raised(qapp, shared, monkeypatch):
    page = PromptHistorySettingsPage()
    monkeypatch.setattr(ph, "fingerprint", lambda *a, **k: (_ for _ in ()).throw(OSError("locked")))
    page._poll()
    assert "locked" in page.stats_label.text()


class EnLoc:
    def tr(self, text):
        return TRANSLATIONS["en"].get(text, text)


def test_english_translation_covers_all_visible_strings(qapp, shared):
    add(state="cancelled")
    page = PromptHistorySettingsPage(EnLoc())
    page.table.selectRow(0)
    texts = [
        page.intro.text(), page.search_edit.placeholderText(), page.refresh_btn.text(),
        page.copy_btn.text(), page.delete_btn.text(), page.clear_btn.text(),
        *[page.tabs.tabText(i) for i in range(3)],
        *[page.table.horizontalHeaderItem(i).text() for i in range(page.table.columnCount())],
        page.page_label.text(), page.stats_label.text(), page.info_view.toPlainText(),
    ]
    joined = "\n".join(texts)
    import re
    assert not re.search(r"[А-Яа-яЁё]", joined), joined
