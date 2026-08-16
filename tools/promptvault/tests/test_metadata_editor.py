"""Тесты для app/ui/metadata_editor.py.

Требуют QT_QPA_PLATFORM=offscreen (см. tests/conftest.py).

Запуск: QT_QPA_PLATFORM=offscreen pytest tests/test_metadata_editor.py -v
"""

from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog

from comfyui_studio.promptvault.core.generation import Generation
from comfyui_studio.promptvault.ui.metadata_editor import MetadataEditor


def _make_gen(custom_tags=None) -> Generation:

    return Generation(
        id=42,
        path=Path("/tmp/gen_1.json"),
        timestamp="t00001",
        generation_time=1.0,
        model="modelA",
        cfg=7.0,
        steps=20,
        sampler="Euler",
        positive="a cat",
        negative="blurry",
        custom_tags=custom_tags or [],
    )


@pytest.fixture
def editor(qapp):

    ed = MetadataEditor(_make_gen())
    yield ed
    ed.deleteLater()


class TestSaveDoesNotCloseDialog:
    """Регрессия: раньше accept() эмитил saved() и безусловно закрывал
    диалог сразу после — даже если сохранение (обрабатываемое снаружи,
    через сигнал) заканчивалось неудачей. Теперь клик Save только
    эмитит saved(); закрытие — ответственность вызывающего кода."""

    def test_clicking_save_emits_saved_with_correct_payload(self, editor):

        captured = {}
        editor.saved.connect(lambda gid, d: captured.update(id=gid, data=d))

        editor.model_box.setCurrentText("modelB")
        editor.cfg_box.setValue(9.5)

        editor._on_save_clicked()

        assert captured["id"] == 42
        assert captured["data"]["model"] == "modelB"
        assert captured["data"]["cfg"] == 9.5

    def test_clicking_save_does_not_close_the_dialog(self, editor):

        editor.saved.connect(lambda *a: None)

        editor._on_save_clicked()

        assert editor.result() != QDialog.Accepted

    def test_dialog_stays_open_even_if_save_handler_reports_failure(self, editor):
        """Симулирует то, что раньше ломалось: подключённый обработчик
        (аналог GalleryManager.update_generation_metadata) сигнализирует
        о неудаче, ничего не закрывая — диалог должен остаться открытым,
        чтобы пользователь не терял введённые изменения."""

        results = []

        def fake_save_handler(gid, update_dict):
            # имитация неудачного сохранения — просто ничего не делает,
            # в реальности здесь GalleryManager эмитил бы error_occurred
            results.append(False)

        editor.saved.connect(fake_save_handler)

        editor.positive_edit.setPlainText("a very good cat")
        editor._on_save_clicked()

        assert results == [False]
        assert editor.result() != QDialog.Accepted
        # данные пользователя всё ещё в полях, ничего не потеряно
        assert editor.positive_edit.toPlainText() == "a very good cat"

    def test_external_code_can_close_dialog_on_confirmed_success(self, editor):
        """Проверяет ожидаемый паттерн использования (см.
        MainWindow._on_edit_requested): внешний код сам вызывает
        accept() после успеха."""

        def on_saved(gid, update_dict):
            editor.accept()

        editor.saved.connect(on_saved)

        editor._on_save_clicked()

        assert editor.result() == QDialog.Accepted

    def test_clicking_cancel_rejects_without_emitting_saved(self, editor):

        captured = []
        editor.saved.connect(captured.append)

        editor.reject()

        assert captured == []
        assert editor.result() == QDialog.Rejected


class TestFieldPopulation:

    def test_fields_prefilled_from_generation(self, editor):

        assert editor.model_box.currentText() == "modelA"
        assert editor.sampler_box.currentText() == "Euler"
        assert editor.cfg_box.value() == 7.0
        assert editor.steps_box.value() == 20
        assert editor.positive_edit.toPlainText() == "a cat"
        assert editor.negative_edit.toPlainText() == "blurry"

    def test_known_models_populate_dropdown(self, qapp):

        ed = MetadataEditor(
            _make_gen(),
            known_models={"modelA", "modelB", "modelC"},
        )

        items = {ed.model_box.itemText(i) for i in range(ed.model_box.count())}
        assert {"modelA", "modelB", "modelC"} <= items

        ed.deleteLater()

    def test_identity_fields_are_not_editable_through_the_dialog(self, editor):
        """timestamp/generation_time сознательно не выведены в форму —
        это ключ идентичности записи в БД (см. docstring класса)."""

        assert not hasattr(editor, "timestamp_edit")
        assert not hasattr(editor, "generation_time_edit")


class TestMetadataHistoryButton:
    """Задача: история изменений метаданных — кнопка "History..." в
    MetadataEditor, показывающая переданный извне (см.
    GalleryManager.get_metadata_history) список записей."""

    def test_history_button_disabled_when_no_history(self, editor):

        assert editor.history_btn.isEnabled() is False

    def test_history_button_enabled_when_history_present(self, qapp):

        ed = MetadataEditor(
            _make_gen(),
            history=[{
                "changed_at": 1700000000.0, "field": "model",
                "old_value": "modelA", "new_value": "modelB",
            }],
        )

        assert ed.history_btn.isEnabled() is True
        assert "1" in ed.history_btn.text()

        ed.deleteLater()

    def test_clicking_history_button_opens_dialog_with_entries(self, qapp, monkeypatch):

        from comfyui_studio.promptvault.ui import metadata_editor as metadata_editor_module

        ed = MetadataEditor(
            _make_gen(),
            history=[{
                "changed_at": 1700000000.0, "field": "cfg",
                "old_value": "7.0", "new_value": "9.0",
            }],
        )

        opened = {}

        class FakeDialog:
            def __init__(self, history, parent=None):
                opened["history"] = history

            def exec(self):
                opened["exec_called"] = True

        monkeypatch.setattr(metadata_editor_module, "MetadataHistoryDialog", FakeDialog)

        ed._on_history_clicked()

        assert opened["exec_called"] is True
        assert opened["history"][0]["field"] == "cfg"

        ed.deleteLater()


class TestMetadataHistoryDialog:

    def test_lists_entries_as_readable_text(self, qapp):

        from comfyui_studio.promptvault.ui.metadata_editor import MetadataHistoryDialog

        dialog = MetadataHistoryDialog([
            {
                "changed_at": 1700000000.0, "field": "model",
                "old_value": "modelA", "new_value": "modelB",
            },
        ])

        assert dialog.list_widget.count() == 1
        text = dialog.list_widget.item(0).text()
        assert "Model" in text
        assert "modelA" in text
        assert "modelB" in text

        dialog.deleteLater()

    def test_empty_history_shows_empty_list(self, qapp):

        from comfyui_studio.promptvault.ui.metadata_editor import MetadataHistoryDialog

        dialog = MetadataHistoryDialog([])

        assert dialog.list_widget.count() == 0

        dialog.deleteLater()
    """Задача: пользовательские теги — виджет QLineEdit + Add + список
    в MetadataEditor."""

    def test_tags_prefilled_from_generation(self, qapp):

        ed = MetadataEditor(_make_gen(custom_tags=["cat", "outdoors"]))

        items = {ed.tags_list.item(i).text() for i in range(ed.tags_list.count())}
        assert items == {"cat", "outdoors"}

        ed.deleteLater()

    def test_add_tag_button_adds_to_list(self, editor):

        editor.tag_input.setText("cat")
        editor._on_add_tag_clicked()

        assert editor._current_tags() == ["cat"]
        assert editor.tag_input.text() == ""

    def test_add_tag_ignores_blank_input(self, editor):

        editor.tag_input.setText("   ")
        editor._on_add_tag_clicked()

        assert editor._current_tags() == []

    def test_add_tag_prevents_case_insensitive_duplicates(self, editor):

        editor.tag_input.setText("Cat")
        editor._on_add_tag_clicked()

        editor.tag_input.setText("cat")
        editor._on_add_tag_clicked()

        assert editor._current_tags() == ["Cat"]

    def test_remove_tag_button_removes_selected(self, qapp):

        ed = MetadataEditor(_make_gen(custom_tags=["cat", "outdoors"]))

        ed.tags_list.setCurrentRow(0)
        ed._on_remove_tag_clicked()

        assert len(ed._current_tags()) == 1

        ed.deleteLater()

    def test_save_emits_tags_changed_with_current_tags(self, editor):

        captured = {}
        editor.tagsChanged.connect(lambda gid, tags: captured.update(id=gid, tags=tags))

        editor.tag_input.setText("cat")
        editor._on_add_tag_clicked()

        editor._on_save_clicked()

        assert captured["id"] == 42
        assert captured["tags"] == ["cat"]
