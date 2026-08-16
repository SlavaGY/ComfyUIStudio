"""Тесты для горячих клавиш в MainWindow (задача: настраиваемые
горячие клавиши, см. app/core/hotkeys.py) — регистрация QShortcut на
каждое действие, живое обновление при смене комбинации через
SettingsWindow, и обработчики, действующие на текущее выделение в
GenerationList.
"""

import pytest
from PySide6.QtGui import QKeySequence

from comfyui_studio.promptvault.core.hotkeys import HOTKEY_ACTIONS
from comfyui_studio.promptvault.ui.main_window import MainWindow


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    """См. tests/test_app_settings.py — та же изоляция QSettings.
    Актуальна и здесь: HotkeyManager и (с задачи "сохранение пути к
    папке между сессиями") MainWindow._restore_last_folder оба читают/
    пишут QSettings("PromptVault", "PromptVault") при создании
    MainWindow()."""

    from PySide6.QtCore import QSettings

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))

    QSettings("PromptVault", "PromptVault").clear()

    yield

    QSettings("PromptVault", "PromptVault").clear()


class _FakeSelection:
    """Подменяет GenerationList.selected_ids — тестам хоткеев,
    завязанных на выделение, не нужен настоящий список карточек."""

    def __init__(self, ids):
        self.ids = ids

    def __call__(self):
        return self.ids


class TestHotkeyRegistration:

    def test_every_action_has_a_shortcut(self, qapp):

        w = MainWindow()

        try:
            assert set(w._shortcuts.keys()) == set(HOTKEY_ACTIONS)
        finally:
            w.close()

    def test_shortcut_key_matches_hotkey_manager(self, qapp):

        w = MainWindow()

        try:
            for action_id in HOTKEY_ACTIONS:
                assert w._shortcuts[action_id].key() == w.hotkey_manager.sequence(action_id)
        finally:
            w.close()

    def test_on_hotkey_changed_updates_live_shortcut(self, qapp):

        w = MainWindow()

        try:
            w.hotkey_manager.set_sequence("open_folder", QKeySequence("Ctrl+Shift+K"))
            w._on_hotkey_changed("open_folder")

            assert w._shortcuts["open_folder"].key() == QKeySequence("Ctrl+Shift+K")
        finally:
            w.close()

    def test_toggle_fullscreen_shortcut_triggers_handler(self, qapp):
        """Проверяет саму привязку activated -> обработчик (не то, что
        F11 физически нажат) — activated.emit() эквивалентен
        фактическому срабатыванию QShortcut."""

        w = MainWindow()

        try:
            assert not w.isFullScreen()

            w._shortcuts["toggle_fullscreen"].activated.emit()

            assert w.isFullScreen()
        finally:
            w.close()


class TestSelectionHotkeys:
    """_hotkey_* методы, действующие на GenerationList.selected_ids()."""

    def test_toggle_favorite_noop_without_selection(self, qapp, monkeypatch):

        w = MainWindow()

        try:
            monkeypatch.setattr(w.generation_list, "selected_ids", _FakeSelection([]))

            calls = []
            monkeypatch.setattr(w.gallery, "toggle_favorite", lambda gid: calls.append(gid))
            monkeypatch.setattr(
                w.gallery, "set_multiple_favorite",
                lambda ids, value: calls.append((ids, value))
            )

            w._hotkey_toggle_favorite()

            assert calls == []
        finally:
            w.close()

    def test_toggle_favorite_single_selection_calls_toggle(self, qapp, monkeypatch):

        w = MainWindow()

        try:
            monkeypatch.setattr(w.generation_list, "selected_ids", _FakeSelection([42]))

            calls = []
            monkeypatch.setattr(w.gallery, "toggle_favorite", lambda gid: calls.append(gid))

            w._hotkey_toggle_favorite()

            assert calls == [42]
        finally:
            w.close()

    def test_toggle_favorite_multi_selection_calls_set_multiple(self, qapp, monkeypatch):

        w = MainWindow()

        try:
            monkeypatch.setattr(w.generation_list, "selected_ids", _FakeSelection([1, 2, 3]))

            calls = []
            monkeypatch.setattr(
                w.gallery, "set_multiple_favorite",
                lambda ids, value: calls.append((ids, value))
            )

            w._hotkey_toggle_favorite()

            assert calls == [([1, 2, 3], True)]
        finally:
            w.close()

    def test_delete_from_library_noop_without_selection(self, qapp, monkeypatch):

        w = MainWindow()

        try:
            monkeypatch.setattr(w.generation_list, "selected_ids", _FakeSelection([]))

            calls = []
            monkeypatch.setattr(w, "_on_delete_from_library", lambda ids: calls.append(ids))

            w._hotkey_delete_from_library()

            assert calls == []
        finally:
            w.close()

    def test_delete_from_library_forwards_selection(self, qapp, monkeypatch):

        w = MainWindow()

        try:
            monkeypatch.setattr(w.generation_list, "selected_ids", _FakeSelection([7, 8]))

            calls = []
            monkeypatch.setattr(w, "_on_delete_from_library", lambda ids: calls.append(ids))

            w._hotkey_delete_from_library()

            assert calls == [[7, 8]]
        finally:
            w.close()

    def test_edit_metadata_single_vs_bulk(self, qapp, monkeypatch):

        w = MainWindow()

        try:
            single_calls = []
            bulk_calls = []
            monkeypatch.setattr(w, "_on_edit_requested", lambda gid: single_calls.append(gid))
            monkeypatch.setattr(w, "_on_bulk_edit_requested", lambda ids: bulk_calls.append(ids))

            monkeypatch.setattr(w.generation_list, "selected_ids", _FakeSelection([5]))
            w._hotkey_edit_metadata()
            assert single_calls == [5]
            assert bulk_calls == []

            monkeypatch.setattr(w.generation_list, "selected_ids", _FakeSelection([5, 6]))
            w._hotkey_edit_metadata()
            assert bulk_calls == [[5, 6]]
        finally:
            w.close()
