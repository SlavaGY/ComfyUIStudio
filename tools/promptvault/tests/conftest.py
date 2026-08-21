"""Общие fixtures для тестов, использующих реальные Qt-виджеты.

Тесты, которым нужен QApplication, должны запускаться с
QT_QPA_PLATFORM=offscreen (см. CONTRIBUTING.md):

    QT_QPA_PLATFORM=offscreen pytest
"""

import pytest

from comfyui_studio.promptvault.config import SEMANTIC_SIMILARITY_THRESHOLD
from comfyui_studio.promptvault.core import embedding, generation_filter

# Фикстура `qapp` больше не определяется здесь вручную — её предоставляет
# pytest-qt (см. requirements-dev.txt / pyproject.toml). Она даёт тот же
# единственный на сессию QApplication, но дополнительно снимает с нас
# обязанность вручную поддерживать qapp_args/qapp_cls и т.п.; qtbot,
# который тоже приносит pytest-qt, тестами пока не используется, но
# доступен на будущее.


@pytest.fixture(autouse=True)
def _stub_embedding_model_by_default(monkeypatch):
    """По умолчанию НИ ОДИН тест не должен дёргать настоящую модель
    эмбеддингов — sync_folder/update_generation в GenerationRepository
    вызывают embedding.compute_embedding(_batch) как часть обычной
    логики, так что любой тест репозитория/галереи, даже никак не
    относящийся к семантическому поиску, иначе попытался бы реально
    загрузить модель (сеть при первом запуске, torch).

    Это не просто медленно — при недоступной сети/несовместимом torch
    настоящая get_model() один раз падает и записывает
    embedding._load_failed=True НА УРОВНЕ ПРОЦЕССА (модуль хранит это
    состояние глобально, не per-test), из-за чего ВСЕ последующие
    тесты в этом же прогоне pytest начинают видеть
    embedding.is_available() == False, даже если сами замокали
    compute_embedding и никак не зависят от реальной модели.

    Тесты, которые целенаправленно проверяют app/core/embedding.py и
    семантический поиск, сами патчат нужные функции более специфичными
    fixtures (см. tests/test_embedding.py, test_generation_filter.py,
    test_repository_embeddings.py) — эти патчи применяются уже ПОСЛЕ
    данной autouse fixture (pytest выполняет autouse fixtures раньше
    фикстур, явно запрошенных тестом, в пределах одного scope) и
    корректно перекрывают собой этот дефолт.
    """

    def _raise():
        raise RuntimeError("модель эмбеддингов недоступна (тестовое окружение)")

    monkeypatch.setattr(embedding, "get_model", _raise)


@pytest.fixture(autouse=True)
def _terminate_embedding_worker_after_test():
    """embedding.gpu_available()/get_model() (когда явно замокан
    embedding._worker или сам тест намеренно проверяет реальный путь,
    см. test_embedding_worker.py) могут поднять настоящий подпроцесс
    (см. embedding_ipc.WorkerHandle, embedding_worker.py — вынесено
    туда, чтобы torch/sentence-transformers можно было полностью
    выгрузить из памяти при закрытии PromptVault, см. дорожную карту,
    запись от 2026-08-20) — без явного terminate() после теста
    подпроцесс пережил бы сам тест и продолжал бы висеть отдельным
    процессом до конца всего прогона pytest (или дольше)."""

    yield

    embedding.unload_model()


@pytest.fixture(autouse=True)
def _reset_embedding_model_and_device_state():
    """embedding.set_model/set_device_preference (задача: выбор модели
    эмбеддинга) мутируют module-level переменные НАПРЯМУЮ (MODEL_NAME,
    EMBEDDING_DIM, _current_model_key, _device_preference,
    _disabled_by_user и т.п.) — не через monkeypatch, поэтому вызов
    любого из них в тесте (или косвенно, через GalleryManager.__init__,
    который применяет сохранённый в QSettings выбор модели/устройства)
    без явного сброса просочился бы в СЛЕДУЮЩИЕ тесты того же прогона
    pytest (см. подробное объяснение той же проблемы для get_model
    выше, в _stub_embedding_model_by_default). Возвращаем состояние
    модуля к дефолтному после каждого теста."""

    yield

    embedding.set_model(embedding.DEFAULT_EMBEDDING_MODEL)
    embedding.set_device_preference("auto")
    embedding.set_enabled(True)
    generation_filter.SEMANTIC_SIMILARITY_THRESHOLD = SEMANTIC_SIMILARITY_THRESHOLD
