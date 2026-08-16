"""Тесты для app/ui/settings_window.py — окно настроек, куда перенесены
тема/язык/семантический поиск из Toolbar, плюс новые настройки
производительности (размер страницы) и автоочистки (миниатюры/логи).

Требуют QT_QPA_PLATFORM=offscreen (см. tests/conftest.py).
"""

import sys

import pytest
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QMessageBox

from app.core import embedding
from app.core.gallery_manager import GalleryManager
from app.core.hotkeys import DEFAULT_HOTKEYS, HOTKEY_ACTIONS, HotkeyManager
from app.core.repository import GenerationRepository
from app.i18n import LocalizationManager
from app.themes.theme_manager import ThemeManager
from app.ui.settings_window import SettingsWindow
from app.ui.toolbar import Toolbar


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    """QSettings("PromptVault", "PromptVault") иначе читал/писал бы
    настройки реального пользователя из ~/.config.

    Явная очистка стора (не только подмена HOME/XDG_CONFIG_HOME) — см.
    подробный комментарий в tests/test_app_settings.py, откуда это и
    было скопировано после обнаруженной там же утечки состояния между
    тестовыми файлами."""

    from PySide6.QtCore import QSettings

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))

    QSettings("PromptVault", "PromptVault").clear()

    yield

    QSettings("PromptVault", "PromptVault").clear()


@pytest.fixture(autouse=True)
def _reset_embedding_state():

    yield
    embedding.set_enabled(True)


@pytest.fixture
def settings_window(qapp, tmp_path):

    repo = GenerationRepository(tmp_path / "test.db")
    gallery = GalleryManager(repo)
    theme_manager = ThemeManager()
    localization_manager = LocalizationManager()
    toolbar = Toolbar()

    window = SettingsWindow(
        gallery=gallery,
        theme_manager=theme_manager,
        localization_manager=localization_manager,
        toolbar=toolbar,
    )

    yield window, gallery, theme_manager, localization_manager, toolbar

    gallery.close()


class TestAppearanceSection:

    def test_theme_combo_lists_available_themes(self, settings_window):

        window, _gallery, theme_manager, _loc, _toolbar = settings_window

        items = [window.theme_box.itemText(i) for i in range(window.theme_box.count())]

        assert items == theme_manager.available_themes()

    def test_changing_theme_applies_it(self, settings_window):

        window, _gallery, theme_manager, _loc, _toolbar = settings_window

        window.theme_box.setCurrentText("Nord")

        assert theme_manager.current_theme() == "Nord"

    def test_language_combo_reflects_current_language(self, settings_window):

        window, _gallery, _theme, _loc, _toolbar = settings_window

        assert window.language_box.currentText() == "English"

    def test_changing_language_applies_and_retranslates_toolbar(self, settings_window):

        window, _gallery, _theme, _loc, toolbar = settings_window

        try:
            window.language_box.setCurrentText("Русский")

            assert toolbar.stats_btn.text() == "📊 Статистика"
            assert window.windowTitle() == "PromptVault — Настройки"
        finally:
            window.language_box.setCurrentText("English")

    def test_changing_language_emits_signal(self, settings_window):

        window, _gallery, _theme, _loc, _toolbar = settings_window

        received = []
        window.languageChanged.connect(received.append)

        try:
            window.language_box.setCurrentText("Русский")

            assert received == ["ru"]
        finally:
            window.language_box.setCurrentText("English")


class TestHotkeysSection:

    def test_all_actions_have_a_row(self, settings_window):

        window, _gallery, _theme, _loc, _toolbar = settings_window

        assert set(window._hotkey_rows.keys()) == set(HOTKEY_ACTIONS)

    def test_rows_show_current_sequence(self, settings_window):

        window, _gallery, _theme, _loc, _toolbar = settings_window

        for action_id, default_text in DEFAULT_HOTKEYS.items():
            _label, edit, _reset_btn = window._hotkey_rows[action_id]
            assert edit.keySequence() == QKeySequence(default_text)

    def test_editing_a_sequence_persists_it(self, settings_window):

        window, _gallery, _theme, _loc, _toolbar = settings_window

        _label, edit, _reset_btn = window._hotkey_rows["open_folder"]
        edit.setKeySequence(QKeySequence("Ctrl+Shift+O"))
        window._on_hotkey_edited("open_folder")

        assert HotkeyManager().sequence_text("open_folder") == "Ctrl+Shift+O"

    def test_editing_a_sequence_emits_hotkey_changed(self, settings_window):

        window, _gallery, _theme, _loc, _toolbar = settings_window

        received = []
        window.hotkeyChanged.connect(received.append)

        _label, edit, _reset_btn = window._hotkey_rows["open_folder"]
        edit.setKeySequence(QKeySequence("Ctrl+Shift+O"))
        window._on_hotkey_edited("open_folder")

        assert received == ["open_folder"]

    def test_conflicting_sequence_is_rejected(self, settings_window):

        window, _gallery, _theme, _loc, _toolbar = settings_window

        taken = window.hotkey_manager.sequence("open_folder")

        received = []
        window.hotkeyChanged.connect(received.append)

        _label, edit, _reset_btn = window._hotkey_rows["focus_search"]
        edit.setKeySequence(taken)
        window._on_hotkey_edited("focus_search")

        # ничего не сохранено, ничего не эмитировано, поле откатилось
        assert received == []
        assert window.hotkey_manager.is_default("focus_search")
        assert edit.keySequence() == window.hotkey_manager.sequence("focus_search")
        assert not window.hotkey_conflict_hint.isHidden()

    def test_reset_button_restores_default(self, settings_window):

        window, _gallery, _theme, _loc, _toolbar = settings_window

        _label, edit, _reset_btn = window._hotkey_rows["open_folder"]
        edit.setKeySequence(QKeySequence("Ctrl+Shift+O"))
        window._on_hotkey_edited("open_folder")

        window._on_hotkey_reset("open_folder")

        assert window.hotkey_manager.is_default("open_folder")
        assert edit.keySequence() == QKeySequence(DEFAULT_HOTKEYS["open_folder"])

    def test_reset_all_restores_every_action(self, settings_window):

        window, _gallery, _theme, _loc, _toolbar = settings_window

        # меняем через сам HotkeyManager напрямую (не через
        # _on_hotkey_edited/UI), иначе второе и последующие действия
        # конфликтовали бы с уже назначенной тем же путём комбинацией
        # первого — здесь же нужен просто факт "что-то не по
        # умолчанию везде", а не проверка конфликтов
        for i, action_id in enumerate(HOTKEY_ACTIONS):
            window.hotkey_manager.set_sequence(action_id, QKeySequence(f"Ctrl+Alt+F{i + 1}"))

        window._on_reset_all_hotkeys()

        for action_id in HOTKEY_ACTIONS:
            _label, edit, _reset_btn = window._hotkey_rows[action_id]
            assert window.hotkey_manager.is_default(action_id)
            assert edit.keySequence() == QKeySequence(DEFAULT_HOTKEYS[action_id])


class TestSearchSection:

    def test_checkbox_reflects_initial_state(self, settings_window):

        window, gallery, _theme, _loc, _toolbar = settings_window

        assert window.semantic_search_checkbox.isChecked() == gallery.semantic_search_enabled()

    def test_unchecking_disables_semantic_search(self, settings_window):

        window, gallery, _theme, _loc, _toolbar = settings_window

        window.semantic_search_checkbox.setChecked(False)

        assert gallery.semantic_search_enabled() is False
        assert embedding.is_available() is False


class TestEmbeddingModelSection:
    """Задача: выбор модели эмбеддинга и устройства."""

    def test_model_combo_lists_all_models_plus_no_model_option(self, settings_window):

        window, _gallery, _theme, _loc, _toolbar = settings_window

        items = [
            window.embedding_model_box.itemText(i)
            for i in range(window.embedding_model_box.count())
        ]

        assert any(item.startswith("e5-large-v2") for item in items)
        assert any(item.startswith("all-MiniLM-L6-v2") for item in items)
        assert any("No model" in item for item in items)

    def test_model_combo_reflects_current_selection(self, settings_window):

        window, gallery, _theme, _loc, _toolbar = settings_window

        current_key = window._embedding_model_keys[window.embedding_model_box.currentIndex()]
        assert current_key == gallery.embedding_model_key() == "e5-large-v2"

    def test_selecting_a_different_model_updates_gallery(self, settings_window, monkeypatch):

        window, gallery, _theme, _loc, _toolbar = settings_window

        # ответ "No" на предложение немедленного пересчёта — сама смена
        # модели должна примениться независимо от этого ответа
        monkeypatch.setattr(
            "app.ui.settings_window.QMessageBox.question",
            lambda *a, **kw: QMessageBox.No,
        )

        index = window._embedding_model_keys.index("all-MiniLM-L6-v2")
        window.embedding_model_box.setCurrentIndex(index)

        assert gallery.embedding_model_key() == "all-MiniLM-L6-v2"

    def test_selecting_no_model_disables_semantic_search(self, settings_window, monkeypatch):

        window, gallery, _theme, _loc, _toolbar = settings_window

        monkeypatch.setattr(
            "app.ui.settings_window.QMessageBox.question",
            lambda *a, **kw: QMessageBox.No,
        )

        index = window._embedding_model_keys.index(None)
        window.embedding_model_box.setCurrentIndex(index)

        assert gallery.embedding_model_key() is None
        assert embedding.is_available() is False

    def test_confirming_recompute_prompt_triggers_recompute(self, settings_window, monkeypatch):

        window, gallery, _theme, _loc, _toolbar = settings_window

        monkeypatch.setattr(
            "app.ui.settings_window.QMessageBox.question",
            lambda *a, **kw: QMessageBox.Yes,
        )
        monkeypatch.setattr(
            "app.ui.settings_window.QMessageBox.information",
            lambda *a, **kw: None,
        )

        calls = []
        monkeypatch.setattr(gallery, "recompute_all_embeddings", lambda: calls.append(1) or 0)

        index = window._embedding_model_keys.index("e5-base-v2")
        window.embedding_model_box.setCurrentIndex(index)

        assert calls == [1]

    def test_declining_recompute_prompt_does_not_recompute(self, settings_window, monkeypatch):

        window, gallery, _theme, _loc, _toolbar = settings_window

        monkeypatch.setattr(
            "app.ui.settings_window.QMessageBox.question",
            lambda *a, **kw: QMessageBox.No,
        )

        calls = []
        monkeypatch.setattr(gallery, "recompute_all_embeddings", lambda: calls.append(1) or 0)

        index = window._embedding_model_keys.index("e5-base-v2")
        window.embedding_model_box.setCurrentIndex(index)

        assert calls == []

    def test_device_combo_reflects_default_auto(self, settings_window):

        window, gallery, _theme, _loc, _toolbar = settings_window

        assert window.embedding_device_box.currentText() == "Auto"
        assert gallery.device_preference() == "auto"

    def test_selecting_cpu_device_updates_gallery(self, settings_window):

        window, gallery, _theme, _loc, _toolbar = settings_window

        window.embedding_device_box.setCurrentText("CPU")

        assert gallery.device_preference() == "cpu"

    def test_selecting_gpu_without_torch_warns_but_still_applies(
        self, settings_window, monkeypatch
    ):

        window, gallery, _theme, _loc, _toolbar = settings_window

        warned = []
        monkeypatch.setattr(
            "app.ui.settings_window.QMessageBox.warning",
            lambda *a, **kw: warned.append(1),
        )

        window.embedding_device_box.setCurrentText("GPU")

        assert warned == [1]
        assert gallery.device_preference() == "cuda"

    def test_recompute_button_recomputes_after_confirmation(self, settings_window, monkeypatch):

        window, gallery, _theme, _loc, _toolbar = settings_window

        monkeypatch.setattr(
            "app.ui.settings_window.QMessageBox.question",
            lambda *a, **kw: QMessageBox.Yes,
        )
        monkeypatch.setattr(
            "app.ui.settings_window.QMessageBox.information",
            lambda *a, **kw: None,
        )

        calls = []
        monkeypatch.setattr(gallery, "recompute_all_embeddings", lambda: calls.append(1) or 3)

        window._on_recompute_clicked()

        assert calls == [1]


class TestPerformanceSection:

    def test_spin_reflects_current_page_size(self, settings_window):

        window, gallery, _theme, _loc, _toolbar = settings_window

        assert window.page_size_spin.value() == gallery.generations_page_size()

    def test_changing_spin_updates_gallery_page_size(self, settings_window):

        window, gallery, _theme, _loc, _toolbar = settings_window

        window.page_size_spin.setValue(1234)

        assert gallery.generations_page_size() == 1234


class TestStorageSection:

    def test_spins_reflect_defaults(self, settings_window):

        from app.config import (
            LOG_DIR_MAX_BYTES,
            LOG_MAX_AGE_DAYS,
            THUMBNAIL_CACHE_MAX_BYTES,
            THUMBNAIL_MAX_AGE_DAYS,
        )

        window, _gallery, _theme, _loc, _toolbar = settings_window

        assert window.thumbnail_age_spin.value() == THUMBNAIL_MAX_AGE_DAYS
        assert window.thumbnail_size_spin.value() == THUMBNAIL_CACHE_MAX_BYTES // (1024 * 1024)
        assert window.log_age_spin.value() == LOG_MAX_AGE_DAYS
        assert window.log_size_spin.value() == LOG_DIR_MAX_BYTES // (1024 * 1024)

    def test_changing_spins_persists_via_app_settings(self, settings_window):

        window, _gallery, _theme, _loc, _toolbar = settings_window

        window.thumbnail_age_spin.setValue(3)
        window.thumbnail_size_spin.setValue(111)
        window.log_age_spin.setValue(9)
        window.log_size_spin.setValue(222)

        assert window.app_settings.thumbnail_max_age_days() == 3
        assert window.app_settings.thumbnail_cache_max_mb() == 111
        assert window.app_settings.log_max_age_days() == 9
        assert window.app_settings.log_dir_max_mb() == 222


class TestRetranslateUi:

    def test_close_button_translates(self, settings_window):

        window, _gallery, _theme, loc, toolbar = settings_window

        try:
            loc.apply_language("ru")
            window.retranslate_ui()
            toolbar.retranslate_ui()

            assert window.close_btn.text() == "Закрыть"
        finally:
            loc.apply_language("en")
            window.retranslate_ui()
            toolbar.retranslate_ui()


class TestMainWindowIntegration:
    """MainWindow.show_settings — открывается кнопкой "⚙" тулбара."""

    def test_settings_button_opens_settings_window(self, qapp, tmp_path, monkeypatch):

        from app.ui.main_window import MainWindow

        w = MainWindow()

        try:
            assert w.settings_window is None

            w.toolbar.settings_btn.click()

            assert w.settings_window is not None
            assert w.settings_window.isVisible()
        finally:
            w.close()

    def test_reopening_reuses_same_window(self, qapp, tmp_path, monkeypatch):

        from app.ui.main_window import MainWindow

        w = MainWindow()

        try:
            w.show_settings()
            first = w.settings_window

            w.show_settings()

            assert w.settings_window is first
        finally:
            w.close()


class TestApplicationSection:
    """Кнопки "Restart"/"Quit" — задача: добавить перезапуск и выход
    в настройки."""

    def test_restart_button_requires_confirmation(self, settings_window, monkeypatch):

        window, _gallery, _theme, _loc, _toolbar = settings_window

        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))

        received = []
        window.restartRequested.connect(lambda: received.append(True))

        window.restart_btn.click()

        assert received == []

    def test_restart_button_emits_signal_when_confirmed(self, settings_window, monkeypatch):

        window, _gallery, _theme, _loc, _toolbar = settings_window

        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))

        received = []
        window.restartRequested.connect(lambda: received.append(True))

        window.restart_btn.click()

        assert received == [True]

    def test_quit_button_requires_confirmation(self, settings_window, monkeypatch):

        window, _gallery, _theme, _loc, _toolbar = settings_window

        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))

        received = []
        window.quitRequested.connect(lambda: received.append(True))

        window.quit_btn.click()

        assert received == []

    def test_quit_button_emits_signal_when_confirmed(self, settings_window, monkeypatch):

        window, _gallery, _theme, _loc, _toolbar = settings_window

        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))

        received = []
        window.quitRequested.connect(lambda: received.append(True))

        window.quit_btn.click()

        assert received == [True]


class TestMainWindowRestartAndQuit:
    """MainWindow.restart_application/quit_application — вызываются в
    ответ на сигналы SettingsWindow (см. show_settings)."""

    def test_restart_sets_pending_flag_and_calls_execv_via_close(
        self, qapp, tmp_path, monkeypatch
    ):
        """os.execv заменяет образ процесса и не должен реально
        вызываться в тесте — подменяем его, чтобы только проверить,
        что закрытие окна доходит до этой точки с правильными
        аргументами. Регрессия: раньше здесь переиспользовался
        "сырой" sys.argv, что ломало перезапуск при запуске через
        `python -m app.main` (см. TODO.md) — теперь всегда явно
        `-m app.main`."""

        import os

        from app.ui.main_window import MainWindow

        execv_calls = []
        monkeypatch.setattr(os, "execv", lambda *a: execv_calls.append(a))
        monkeypatch.setattr(sys, "argv", ["app/main.py"])

        w = MainWindow()

        try:
            assert w._pending_restart is False

            w.restart_application()

            assert w._pending_restart is True
            assert len(execv_calls) == 1
            assert execv_calls[0][0] == sys.executable
            assert execv_calls[0][1] == [sys.executable, "-m", "app.main"]
        finally:
            pass  # окно уже закрыто restart_application -> close()

    def test_restart_forwards_extra_cli_args(self, qapp, tmp_path, monkeypatch):
        """sys.argv[1:] (аргументы после имени модуля) должны
        сохраниться при пересборке инвокации как `-m app.main`."""

        import os

        from app.ui.main_window import MainWindow

        execv_calls = []
        monkeypatch.setattr(os, "execv", lambda *a: execv_calls.append(a))
        monkeypatch.setattr(sys, "argv", ["app/main.py", "--some-flag"])

        w = MainWindow()

        w.restart_application()

        assert execv_calls[0][1] == [sys.executable, "-m", "app.main", "--some-flag"]

    def test_quit_does_not_call_execv(self, qapp, tmp_path, monkeypatch):

        import os

        from app.ui.main_window import MainWindow

        execv_calls = []
        monkeypatch.setattr(os, "execv", lambda *a: execv_calls.append(a))

        w = MainWindow()

        w.quit_application()

        assert execv_calls == []
        assert w._pending_restart is False

    def test_restart_closes_child_windows(self, qapp, tmp_path, monkeypatch):

        import os

        from app.ui.main_window import MainWindow

        monkeypatch.setattr(os, "execv", lambda *a: None)

        w = MainWindow()
        w.show_statistics()
        w.show_settings()

        assert w.statistics_window.isVisible()
        assert w.settings_window.isVisible()

        w.restart_application()

        assert not w.statistics_window.isVisible()
        assert not w.settings_window.isVisible()
