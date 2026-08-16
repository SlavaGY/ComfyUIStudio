"""Тесты для app/core/hotkeys.py (HotkeyManager) — настраиваемые
горячие клавиши (задача: настраиваемые горячие клавиши).
"""

import pytest
from PySide6.QtGui import QKeySequence

from app.core.hotkeys import DEFAULT_HOTKEYS, HOTKEY_ACTIONS, HotkeyManager


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    """См. tests/test_app_settings.py — тот же паттерн изоляции
    QSettings("PromptVault", "PromptVault") между тестовыми файлами."""

    from PySide6.QtCore import QSettings

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))

    QSettings("PromptVault", "PromptVault").clear()

    yield

    QSettings("PromptVault", "PromptVault").clear()


class TestDefaults:

    def test_every_action_has_a_default(self):

        for action_id in HOTKEY_ACTIONS:
            assert action_id in DEFAULT_HOTKEYS

    def test_defaults_have_no_internal_conflicts(self):
        """Сами значения по умолчанию не должны конфликтовать друг с
        другом — иначе UI открывался бы уже с предупреждением о
        конфликте на пустом месте."""

        seen: dict[str, str] = {}

        for action_id, sequence_text in DEFAULT_HOTKEYS.items():
            assert sequence_text not in seen, (
                f"{action_id} и {seen.get(sequence_text)} по умолчанию "
                f"используют одну и ту же комбинацию {sequence_text!r}"
            )
            seen[sequence_text] = action_id

    def test_fresh_manager_returns_defaults(self):

        manager = HotkeyManager()

        for action_id, default_text in DEFAULT_HOTKEYS.items():
            assert manager.sequence_text(action_id) == default_text
            assert manager.sequence(action_id) == QKeySequence(default_text)
            assert manager.is_default(action_id)


class TestSetAndReset:

    def test_set_sequence_persists(self):

        manager = HotkeyManager()

        manager.set_sequence("open_folder", QKeySequence("Ctrl+Shift+O"))

        assert manager.sequence_text("open_folder") == "Ctrl+Shift+O"
        assert not manager.is_default("open_folder")

        # новый экземпляр должен увидеть то же самое — это то же самое
        # QSettings-хранилище, что и у ThemeManager/AppSettings
        assert HotkeyManager().sequence_text("open_folder") == "Ctrl+Shift+O"

    def test_reset_restores_default(self):

        manager = HotkeyManager()

        manager.set_sequence("open_folder", QKeySequence("Ctrl+Shift+O"))
        manager.reset("open_folder")

        assert manager.is_default("open_folder")
        assert manager.sequence_text("open_folder") == DEFAULT_HOTKEYS["open_folder"]

    def test_reset_all_restores_every_action(self):

        manager = HotkeyManager()

        for action_id in HOTKEY_ACTIONS:
            manager.set_sequence(action_id, QKeySequence("Ctrl+Alt+Z"))

        manager.reset_all()

        for action_id in HOTKEY_ACTIONS:
            assert manager.is_default(action_id)

    def test_clearing_to_empty_sequence_is_allowed(self):
        """Задача явно требует возможность "снять" хоткей (пустая
        комбинация) — не только переназначить."""

        manager = HotkeyManager()

        manager.set_sequence("reset_image_view", QKeySequence())

        assert manager.sequence_text("reset_image_view") == ""
        assert manager.sequence("reset_image_view").isEmpty()


class TestConflicts:

    def test_no_conflict_against_defaults(self):

        manager = HotkeyManager()

        # ни одно действие по умолчанию не должно конфликтовать со
        # своей же (не изменённой) комбинацией при повторном
        # редактировании тем же значением
        for action_id in HOTKEY_ACTIONS:
            sequence = manager.sequence(action_id)
            assert manager.find_conflict(action_id, sequence) is None

    def test_detects_conflict_with_another_action(self):

        manager = HotkeyManager()

        taken = manager.sequence("open_folder")

        conflict = manager.find_conflict("focus_search", taken)

        assert conflict == "open_folder"

    def test_empty_sequence_never_conflicts(self):

        manager = HotkeyManager()

        manager.set_sequence("open_folder", QKeySequence())
        manager.set_sequence("focus_search", QKeySequence())

        assert manager.find_conflict("show_statistics", QKeySequence()) is None

    def test_no_self_conflict(self):
        """Проверка конфликта для действия против его же текущей
        комбинации не должна засчитывать конфликт с самим собой."""

        manager = HotkeyManager()

        current = manager.sequence("toggle_favorite")

        assert manager.find_conflict("toggle_favorite", current) is None
