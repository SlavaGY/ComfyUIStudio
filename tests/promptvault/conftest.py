"""Общие fixtures для тестов, использующих реальные Qt-виджеты.

Тесты, которым нужен QApplication, должны запускаться с
QT_QPA_PLATFORM=offscreen (см. CONTRIBUTING.md):

    QT_QPA_PLATFORM=offscreen pytest
"""

# Фикстура `qapp` больше не определяется здесь вручную — её предоставляет
# pytest-qt (см. requirements-dev.txt / pyproject.toml). Она даёт тот же
# единственный на сессию QApplication, но дополнительно снимает с нас
# обязанность вручную поддерживать qapp_args/qapp_cls и т.п.; qtbot,
# который тоже приносит pytest-qt, тестами пока не используется, но
# доступен на будущее.


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_promptvault_home_dir(tmp_path, monkeypatch):
    """Данные PromptVault по умолчанию лежат в ~/.promptvault (миниатюры, БД)
    (promptvault/config.py). Без подмены экспорт с превью
    (test_repository_scaling.py) писал .webp в настоящий профиль и не
    убирал за собой."""
    from comfyui_studio.promptvault import config as pv_config
    from comfyui_studio.promptvault.core import database, thumbnails

    app_dir = tmp_path / "pv_home"
    cache_dir = app_dir / "thumbnails"
    monkeypatch.setattr(pv_config, "THUMBNAIL_CACHE_DIR", cache_dir)
    monkeypatch.setattr(thumbnails, "THUMBNAIL_CACHE_DIR", cache_dir)
    # MainWindow() без явного пути открывает БД по умолчанию
    # (database.connect -> DB_PATH): без подмены тесты создавали настоящую
    # ~/.promptvault/promptvault.db и писали в неё.
    monkeypatch.setattr(pv_config, "DB_PATH", app_dir / "promptvault.db")
    monkeypatch.setattr(database, "DB_PATH", app_dir / "promptvault.db")
