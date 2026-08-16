"""Тесты для app/ui/filter_popup.py — трёхстанные фильтры LoRA и
пользовательских тегов (задача: включить/исключить/нейтрально) и
восстановление состояния из сохранённых FilterOptions.

Требуют QT_QPA_PLATFORM=offscreen (см. tests/conftest.py).
"""

import pytest

from comfyui_studio.promptvault.core.generation_filter import FilterOptions
from comfyui_studio.promptvault.ui.filter_popup import FilterPopup


@pytest.fixture
def popup(qapp):

    p = FilterPopup()
    yield p
    p.deleteLater()


class TestLoraTriState:

    def test_starts_with_no_included_or_excluded(self, popup):

        popup.set_loras(["A", "B"])

        assert popup.loras() is None
        assert popup.excluded_loras() is None

    def test_one_click_includes(self, popup):

        popup.set_loras(["A", "B"])
        popup.lora_checkboxes["A"].nextCheckState()

        assert popup.loras() == ["A"]
        assert popup.excluded_loras() is None

    def test_two_clicks_excludes(self, popup):

        popup.set_loras(["A", "B"])
        popup.lora_checkboxes["A"].nextCheckState()
        popup.lora_checkboxes["A"].nextCheckState()

        assert popup.loras() is None
        assert popup.excluded_loras() == ["A"]

    def test_three_clicks_returns_to_neutral(self, popup):

        popup.set_loras(["A", "B"])
        checkbox = popup.lora_checkboxes["A"]

        checkbox.nextCheckState()
        checkbox.nextCheckState()
        checkbox.nextCheckState()

        assert popup.loras() is None
        assert popup.excluded_loras() is None

    def test_reset_clears_include_and_exclude(self, popup):

        popup.set_loras(["A", "B"])
        popup.lora_checkboxes["A"].nextCheckState()
        popup.lora_checkboxes["B"].nextCheckState()
        popup.lora_checkboxes["B"].nextCheckState()

        popup.reset()

        assert popup.loras() is None
        assert popup.excluded_loras() is None

    def test_set_loras_preserves_state_across_rebuild(self, popup):
        """set_loras пересобирает чекбоксы (например, после ре-синхронизации
        папки) — состояние включения/исключения уже отмеченных LoRA не
        должно теряться, если они всё ещё присутствуют в новом списке."""

        popup.set_loras(["A", "B"])
        popup.lora_checkboxes["A"].nextCheckState()
        popup.lora_checkboxes["B"].nextCheckState()
        popup.lora_checkboxes["B"].nextCheckState()

        popup.set_loras(["A", "B", "C"])

        assert popup.loras() == ["A"]
        assert popup.excluded_loras() == ["B"]


class TestCustomTagsTriState:

    def test_one_click_includes(self, popup):

        popup.set_custom_tags(["cat", "dog"])
        popup.tag_checkboxes["cat"].nextCheckState()

        assert popup.custom_tags() == ["cat"]
        assert popup.excluded_custom_tags() is None

    def test_two_clicks_excludes(self, popup):

        popup.set_custom_tags(["cat", "dog"])
        popup.tag_checkboxes["cat"].nextCheckState()
        popup.tag_checkboxes["cat"].nextCheckState()

        assert popup.custom_tags() is None
        assert popup.excluded_custom_tags() == ["cat"]

    def test_reset_clears_include_and_exclude(self, popup):

        popup.set_custom_tags(["cat", "dog"])
        popup.tag_checkboxes["cat"].nextCheckState()
        popup.tag_checkboxes["dog"].nextCheckState()
        popup.tag_checkboxes["dog"].nextCheckState()

        popup.reset()

        assert popup.custom_tags() is None
        assert popup.excluded_custom_tags() is None


class TestApplyOptionsRestoresTriState:

    def test_restores_included_loras(self, popup):

        popup.set_loras(["A", "B"])
        popup.apply_options(FilterOptions(loras=["A"]))

        assert popup.loras() == ["A"]
        assert popup.excluded_loras() is None

    def test_restores_excluded_loras(self, popup):

        popup.set_loras(["A", "B"])
        popup.apply_options(FilterOptions(excluded_loras=["B"]))

        assert popup.loras() is None
        assert popup.excluded_loras() == ["B"]

    def test_restores_included_and_excluded_together(self, popup):

        popup.set_loras(["A", "B", "C"])
        popup.apply_options(FilterOptions(loras=["A"], excluded_loras=["B"]))

        assert popup.loras() == ["A"]
        assert popup.excluded_loras() == ["B"]

    def test_restores_custom_tags(self, popup):

        popup.set_custom_tags(["cat", "dog"])
        popup.apply_options(
            FilterOptions(custom_tags=["cat"], excluded_custom_tags=["dog"])
        )

        assert popup.custom_tags() == ["cat"]
        assert popup.excluded_custom_tags() == ["dog"]
