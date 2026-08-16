"""Тесты для переключателя семантического поиска (задача: оптимизация
памяти — модель эмбеддингов ~1.3 ГБ не должна загружаться, если
пользователь явно её отключил):

- GalleryManager.set_semantic_search_enabled / semantic_search_enabled
  (сохранение в QSettings, применение к app.core.embedding).

Сама кнопка теперь в SettingsWindow — см. tests/test_settings_window.py.

Требуют QT_QPA_PLATFORM=offscreen (см. tests/conftest.py).
"""

import pytest

from app.core import embedding
from app.core.gallery_manager import GalleryManager
from app.core.repository import GenerationRepository


@pytest.fixture(autouse=True)
def _reset_embedding_state():
    """embedding._disabled_by_user — модуль-глобальное состояние, не
    per-test — сбрасываем, чтобы тесты не зависели от порядка запуска."""

    yield
    embedding.set_enabled(True)


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    """QSettings("PromptVault", "PromptVault") иначе читал/писал бы
    настройку реального пользователя из ~/.config.

    Явная очистка стора (не только подмена HOME/XDG_CONFIG_HOME) — см.
    подробный комментарий в tests/test_app_settings.py об утечке
    состояния между тестовыми файлами при использовании одного и того
    же (organization, application) у QSettings."""

    from PySide6.QtCore import QSettings

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))

    QSettings("PromptVault", "PromptVault").clear()

    yield

    QSettings("PromptVault", "PromptVault").clear()


@pytest.fixture
def gallery(qapp, tmp_path, isolated_settings):

    repo = GenerationRepository(tmp_path / "test.db")
    gm = GalleryManager(repo)

    yield gm

    gm.close()


class TestGalleryManagerSemanticSearchToggle:

    def test_enabled_by_default(self, gallery):

        assert gallery.semantic_search_enabled() is True

    def test_disabling_applies_to_embedding_module(self, gallery):

        gallery.set_semantic_search_enabled(False)

        assert embedding.is_available() is False

    def test_re_enabling_applies_to_embedding_module(self, gallery, monkeypatch):

        import sys
        import types

        monkeypatch.setitem(
            sys.modules, "sentence_transformers", types.ModuleType("sentence_transformers")
        )

        gallery.set_semantic_search_enabled(False)
        gallery.set_semantic_search_enabled(True)

        assert embedding.is_available() is True

    def test_disabled_choice_persists_across_instances(
        self, qapp, tmp_path, isolated_settings
    ):
        """Новый GalleryManager (как при перезапуске приложения) должен
        подхватить сохранённый выбор пользователя."""

        repo1 = GenerationRepository(tmp_path / "test.db")
        gm1 = GalleryManager(repo1)
        gm1.set_semantic_search_enabled(False)
        gm1.close()

        repo2 = GenerationRepository(tmp_path / "test2.db")
        gm2 = GalleryManager(repo2)

        assert gm2.semantic_search_enabled() is False

        gm2.close()

    def test_disabled_choice_is_applied_at_construction_time(
        self, qapp, tmp_path, isolated_settings
    ):
        """Задача: модель не должна загружаться вообще ни разу за
        время жизни процесса, если пользователь отключил её раньше —
        значит, состояние должно примениться к embedding-модулю уже в
        __init__, до первого возможного load_folder()."""

        repo1 = GenerationRepository(tmp_path / "test.db")
        gm1 = GalleryManager(repo1)
        gm1.set_semantic_search_enabled(False)
        gm1.close()

        embedding.set_enabled(True)  # симулируем чистое состояние нового процесса

        repo2 = GenerationRepository(tmp_path / "test2.db")
        GalleryManager(repo2)  # __init__ должен сам применить сохранённый выбор

        assert embedding.is_available() is False

        repo2.close()


# Кнопка "Semantic search" переехала в SettingsWindow — см.
# tests/test_settings_window.py. GalleryManager-часть (сохранение в
# QSettings, применение к app.core.embedding) по-прежнему тестируется
# выше в этом файле.
