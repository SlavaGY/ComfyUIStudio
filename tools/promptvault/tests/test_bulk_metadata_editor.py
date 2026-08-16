"""Тесты для app/ui/bulk_metadata_editor.py.

Требуют QT_QPA_PLATFORM=offscreen (см. tests/conftest.py).

Запуск: QT_QPA_PLATFORM=offscreen pytest tests/test_bulk_metadata_editor.py -v
"""

import pytest

from app.ui.bulk_metadata_editor import BulkMetadataEditor


@pytest.fixture
def editor(qapp):

    ed = BulkMetadataEditor([1, 2, 3])
    yield ed
    ed.deleteLater()


class TestFieldsStartDisabled:
    """По умолчанию ни одно поле не отмечено — пользователь должен
    явно выбрать, что именно перезаписывать у всех выделенных
    генераций, чтобы не затереть значения случайно."""

    def test_all_checkboxes_start_unchecked(self, editor):

        assert editor.model_enabled.isChecked() is False
        assert editor.sampler_enabled.isChecked() is False
        assert editor.cfg_enabled.isChecked() is False
        assert editor.steps_enabled.isChecked() is False

    def test_all_fields_start_disabled(self, editor):

        assert editor.model_box.isEnabled() is False
        assert editor.sampler_box.isEnabled() is False
        assert editor.cfg_box.isEnabled() is False
        assert editor.steps_box.isEnabled() is False

    def test_checking_checkbox_enables_its_field(self, editor):

        editor.cfg_enabled.setChecked(True)

        assert editor.cfg_box.isEnabled() is True
        assert editor.model_box.isEnabled() is False


class TestSaveOnlyIncludesCheckedFields:

    def test_no_checkbox_checked_emits_nothing(self, editor):

        captured = []
        editor.saved.connect(lambda ids, d: captured.append((ids, d)))

        editor._on_save_clicked()

        assert captured == []

    def test_single_checked_field_only_includes_that_field(self, editor):

        captured = {}
        editor.saved.connect(lambda ids, d: captured.update(ids=ids, data=d))

        editor.cfg_enabled.setChecked(True)
        editor.cfg_box.setValue(9.5)

        editor._on_save_clicked()

        assert captured["ids"] == [1, 2, 3]
        assert captured["data"] == {"cfg": 9.5}

    def test_multiple_checked_fields_all_included(self, editor):

        captured = {}
        editor.saved.connect(lambda ids, d: captured.update(ids=ids, data=d))

        editor.model_enabled.setChecked(True)
        editor.model_box.setCurrentText("modelZ")

        editor.steps_enabled.setChecked(True)
        editor.steps_box.setValue(30)

        editor._on_save_clicked()

        assert captured["data"] == {"model": "modelZ", "steps": 30}

    def test_save_does_not_close_dialog(self, editor):

        from PySide6.QtWidgets import QDialog

        editor.saved.connect(lambda *a: None)
        editor.cfg_enabled.setChecked(True)

        editor._on_save_clicked()

        assert editor.result() != QDialog.Accepted


class TestKnownValuesPopulateDropdowns:

    def test_known_models_and_samplers_populate_dropdowns(self, qapp):

        ed = BulkMetadataEditor(
            [1, 2],
            known_models={"modelA", "modelB"},
            known_samplers={"Euler", "DPM++"},
        )

        model_items = {ed.model_box.itemText(i) for i in range(ed.model_box.count())}
        sampler_items = {ed.sampler_box.itemText(i) for i in range(ed.sampler_box.count())}

        assert {"modelA", "modelB"} <= model_items
        assert {"Euler", "DPM++"} <= sampler_items

        ed.deleteLater()
