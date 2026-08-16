"""Тесты для app/ui/tri_state_checkbox.py — трёхстанный чекбокс для
фильтров LoRA/пользовательских тегов (задача: включить/исключить/
нейтрально).

Требуют QT_QPA_PLATFORM=offscreen (см. tests/conftest.py).
"""

from PySide6.QtCore import Qt

from comfyui_studio.promptvault.ui.tri_state_checkbox import TriStateFilterCheckBox


class TestClickCycle:

    def test_starts_unchecked(self, qapp):

        box = TriStateFilterCheckBox("tag")
        assert box.checkState() == Qt.Unchecked
        assert not box.is_included()
        assert not box.is_excluded()

    def test_first_click_includes(self, qapp):

        box = TriStateFilterCheckBox("tag")

        box.nextCheckState()

        assert box.is_included()
        assert not box.is_excluded()

    def test_second_click_excludes(self, qapp):

        box = TriStateFilterCheckBox("tag")

        box.nextCheckState()
        box.nextCheckState()

        assert box.is_excluded()
        assert not box.is_included()

    def test_third_click_returns_to_neutral(self, qapp):

        box = TriStateFilterCheckBox("tag")

        box.nextCheckState()
        box.nextCheckState()
        box.nextCheckState()

        assert not box.is_included()
        assert not box.is_excluded()
        assert box.checkState() == Qt.Unchecked


class TestSetState:

    def test_set_state_included(self, qapp):

        box = TriStateFilterCheckBox("tag")
        box.set_state(included=True, excluded=False)

        assert box.is_included()
        assert not box.is_excluded()

    def test_set_state_excluded(self, qapp):

        box = TriStateFilterCheckBox("tag")
        box.set_state(included=False, excluded=True)

        assert box.is_excluded()
        assert not box.is_included()

    def test_set_state_neutral(self, qapp):

        box = TriStateFilterCheckBox("tag")
        box.set_state(included=True, excluded=False)
        box.set_state(included=False, excluded=False)

        assert not box.is_included()
        assert not box.is_excluded()

    def test_excluded_takes_priority_if_both_true(self, qapp):
        """Не должно происходить на практике (см. FilterPopup — один
        и тот же элемент не может быть одновременно и в loras, и в
        excluded_loras), но на всякий случай exclude должен побеждать,
        а не молча образовывать нейтральное/непредсказуемое состояние."""

        box = TriStateFilterCheckBox("tag")
        box.set_state(included=True, excluded=True)

        assert box.is_excluded()
