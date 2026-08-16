"""Тесты для семантического поиска в GenerationRepository (задача 3.1):
вычисление и сохранение эмбеддингов при sync_folder/update_generation,
backfill/recompute и search_semantic().

Модель эмбеддингов подменяется детерминированной фейковой функцией
(см. tests/test_embedding.py) — тесты не требуют сети/реальной ML-модели.
compute_embedding/compute_embeddings_batch (документы — промпт
генерации) кодируют ПО ТЕГАМ, как настоящая реализация в
app/core/embedding.py — режут текст по запятым/переносам строк и
кодируют каждый тег отдельно; compute_query_embedding (запрос) —
всегда один вектор на весь текст.

Запуск: pytest tests/test_repository_embeddings.py -v
"""

from __future__ import annotations

import json
import re

import numpy as np
import pytest

from comfyui_studio.promptvault.core import embedding
from comfyui_studio.promptvault.core.repository import GenerationRepository

_TOPICS = ["cat", "dog", "forest", "spaceship"]
_SPLIT_RE = re.compile(r"[,\n]+")


def _topic_vector(text: str) -> np.ndarray:

    words = text.lower().split()
    vec = np.array([words.count(t) for t in _TOPICS], dtype=np.float32)

    if vec.sum() == 0:
        vec = np.ones(len(_TOPICS), dtype=np.float32)

    return vec / np.linalg.norm(vec)


def _fake_compute_embedding(text: str) -> bytes | None:
    """Документ (промпт генерации) — по вектору на тег, как настоящая
    compute_embedding в app/core/embedding.py."""

    chunks = [c.strip() for c in _SPLIT_RE.split(text or "") if c.strip()]

    if not chunks:
        return None

    return np.stack([_topic_vector(c) for c in chunks]).astype(np.float32).tobytes()


def _fake_compute_query_embedding(text: str) -> bytes | None:
    """Запрос — всегда один вектор на весь текст, без разбиения."""

    if not text or not text.strip():
        return None

    return _topic_vector(text).astype(np.float32).tobytes()


@pytest.fixture(autouse=True)
def fake_embeddings(monkeypatch):

    monkeypatch.setattr(embedding, "compute_embedding", _fake_compute_embedding)
    monkeypatch.setattr(
        embedding,
        "compute_embeddings_batch",
        lambda texts: [_fake_compute_embedding(t) for t in texts],
    )
    monkeypatch.setattr(embedding, "compute_query_embedding", _fake_compute_query_embedding)
    # search_semantic/cosine_similarity реконструируют форму чанков по
    # EMBEDDING_DIM — подменяем его на размерность тестовых векторов
    monkeypatch.setattr(embedding, "EMBEDDING_DIM", len(_TOPICS))


def _write_json(path, **overrides):

    data = {
        "timestamp": "ts1",
        "generation_time": 1.0,
        "model_name": "modelA",
        "sampler_name": "Euler",
        "cfg": 7.0,
        "steps": 20,
        "positive_text": "a cat in the forest",
        "negative_text": "",
        "images": [{"file": "img1.png", "seed": 1}],
        "loras": [],
    }
    data.update(overrides)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    return path


@pytest.fixture
def repo(tmp_path):

    db_path = tmp_path / "test.db"
    repository = GenerationRepository(db_path)

    yield repository, tmp_path

    repository.close()


class TestSyncFolderStoresEmbedding:

    def test_embedding_computed_and_stored_on_sync(self, repo):

        repository, folder = repo
        _write_json(folder / "gen1.json")

        repository.sync_folder(folder)
        [gen] = repository.load_generations(folder)

        assert gen.embedding is not None
        assert gen.embedding == _fake_compute_embedding("a cat in the forest")

    def test_negative_prompt_is_not_used_for_embedding(self, repo):
        """Регрессионный тест: негативный промпт больше не участвует в
        эмбеддинге (см. _embedding_text) — раньше он добавлял шумовые
        теги, из-за которых генерации ложно попадали в семантический
        поиск по совершенно не связанным запросам."""

        repository, folder = repo
        _write_json(
            folder / "gen1.json",
            positive_text="a cat",
            negative_text="spaceship, forest, night",
        )

        repository.sync_folder(folder)
        [gen] = repository.load_generations(folder)

        assert gen.embedding == _fake_compute_embedding("a cat")

    def test_prompt_with_tags_is_stored_as_multiple_chunks(self, repo):

        repository, folder = repo
        _write_json(folder / "gen1.json", positive_text="cat, forest, masterpiece")

        repository.sync_folder(folder)
        [gen] = repository.load_generations(folder)

        chunks = embedding.bytes_to_chunks(gen.embedding)
        assert chunks.shape == (3, len(_TOPICS))

    def test_no_embedding_for_empty_prompt(self, repo):

        repository, folder = repo
        _write_json(folder / "gen1.json", positive_text="", negative_text="")

        repository.sync_folder(folder)
        [gen] = repository.load_generations(folder)

        assert gen.embedding is None

    def test_unchanged_file_does_not_recompute_embedding(self, repo, monkeypatch):
        """Файлы, чей mtime не изменился, пропускаются на самом раннем
        этапе sync_folder — эмбеддинг не должен пересчитываться (не
        должно быть повторного вызова батч-кодировщика)."""

        repository, folder = repo
        _write_json(folder / "gen1.json")

        repository.sync_folder(folder)

        calls = []
        original = embedding.compute_embeddings_batch

        def _tracking(texts):
            calls.append(list(texts))
            return original(texts)

        monkeypatch.setattr(embedding, "compute_embeddings_batch", _tracking)

        repository.sync_folder(folder)  # ничего на диске не поменялось

        assert calls == [[]] or calls == []

    def test_embedding_recomputed_when_prompt_changes(self, repo):

        repository, folder = repo
        path = _write_json(folder / "gen1.json", positive_text="a cat")

        repository.sync_folder(folder)
        [gen_before] = repository.load_generations(folder)

        import os
        import time

        time.sleep(0.01)
        _write_json(path, positive_text="a spaceship", generation_time=1.0)
        os.utime(path, None)

        repository.sync_folder(folder)
        [gen_after] = repository.load_generations(folder)

        assert gen_before.embedding != gen_after.embedding
        assert gen_after.embedding == _fake_compute_embedding("a spaceship")


class TestUpdateGenerationRecomputesEmbedding:

    def test_editing_positive_prompt_updates_embedding(self, repo):

        repository, folder = repo
        _write_json(folder / "gen1.json", positive_text="a cat")

        repository.sync_folder(folder)
        [gen] = repository.load_generations(folder)

        repository.update_generation(gen.id, {"positive": "a spaceship"})

        refreshed = repository.get_generation(gen.id)
        assert refreshed.embedding == _fake_compute_embedding("a spaceship")


class TestBackfillMissingEmbeddings:

    def test_backfills_rows_left_null_by_migration(self, repo):
        """Симулирует запись, унаследованную от версии до задачи 3.1:
        embedding=NULL в БД, но JSON на диске не менялся — sync_folder
        сам её не тронет (см. docstring backfill_missing_embeddings)."""

        repository, folder = repo
        _write_json(folder / "gen1.json", positive_text="a cat")
        repository.sync_folder(folder)

        # эмулируем "унаследованную" запись без эмбеддинга
        repository._conn.execute("UPDATE generations SET embedding = NULL")
        repository._conn.commit()

        updated = repository.backfill_missing_embeddings()

        assert updated == 1
        [gen] = repository.load_generations(folder)
        assert gen.embedding is not None

    def test_returns_zero_when_nothing_to_backfill(self, repo):

        repository, folder = repo
        _write_json(folder / "gen1.json")
        repository.sync_folder(folder)

        assert repository.backfill_missing_embeddings() == 0

    def test_respects_batch_size(self, repo):

        repository, folder = repo

        for i in range(5):
            _write_json(folder / f"gen{i}.json", timestamp=f"ts{i}", generation_time=float(i))

        repository.sync_folder(folder)
        repository._conn.execute("UPDATE generations SET embedding = NULL")
        repository._conn.commit()

        updated = repository.backfill_missing_embeddings(batch_size=2)

        assert updated == 2

    def test_model_unavailable_does_nothing(self, repo, monkeypatch):

        repository, folder = repo
        _write_json(folder / "gen1.json")
        repository.sync_folder(folder)
        repository._conn.execute("UPDATE generations SET embedding = NULL")
        repository._conn.commit()

        monkeypatch.setattr(embedding, "is_available", lambda: False)

        assert repository.backfill_missing_embeddings() == 0


class TestRecomputeAllEmbeddings:

    def test_recomputes_even_rows_that_already_have_an_embedding(self, repo):

        repository, folder = repo
        _write_json(folder / "gen1.json", positive_text="a cat")
        repository.sync_folder(folder)

        [before] = repository.load_generations(folder)
        assert before.embedding is not None

        total = repository.recompute_all_embeddings()

        assert total == 1
        [after] = repository.load_generations(folder)
        assert after.embedding is not None

    def test_processes_all_rows_across_multiple_batches(self, repo):

        repository, folder = repo

        for i in range(5):
            _write_json(folder / f"gen{i}.json", timestamp=f"ts{i}", generation_time=float(i))

        repository.sync_folder(folder)

        total = repository.recompute_all_embeddings(batch_size=2)

        assert total == 5


class TestSearchSemantic:

    def test_returns_ids_ranked_by_similarity(self, repo):

        repository, folder = repo
        _write_json(folder / "gen1.json", timestamp="ts1", positive_text="a cat")
        _write_json(folder / "gen2.json", timestamp="ts2", generation_time=2.0, positive_text="a spaceship")

        repository.sync_folder(folder)

        result_ids = repository.search_semantic("cat")

        assert result_ids
        [gen] = [
            g for g in repository.load_generations(folder)
            if g.embedding == _fake_compute_embedding("a cat")
        ]
        assert result_ids[0] == gen.id

    def test_finds_matching_tag_among_many_unrelated_tags(self, repo):
        """Регрессионный тест на исходную жалобу: короткий запрос
        должен находить свой тег, даже когда он один из многих в
        длинном промпте."""

        repository, folder = repo
        _write_json(
            folder / "gen1.json",
            positive_text="forest, spaceship, dog, cat, masterpiece, high quality",
        )
        _write_json(
            folder / "gen2.json",
            generation_time=2.0,
            positive_text="forest, spaceship, dog, masterpiece, high quality",
        )

        repository.sync_folder(folder)

        result_ids = repository.search_semantic("cat")

        gens = {g.id: g for g in repository.load_generations(folder)}
        cat_gen_id = next(
            gid for gid, g in gens.items()
            if "cat" in (g.positive or "")
        )

        assert cat_gen_id in result_ids

    def test_empty_query_returns_empty_list(self, repo):

        repository, folder = repo
        _write_json(folder / "gen1.json")
        repository.sync_folder(folder)

        assert repository.search_semantic("") == []

    def test_respects_limit(self, repo):

        repository, folder = repo

        for i in range(5):
            _write_json(
                folder / f"gen{i}.json",
                timestamp=f"ts{i}",
                generation_time=float(i),
                positive_text="a cat in the forest",
            )

        repository.sync_folder(folder)

        result_ids = repository.search_semantic("cat", limit=2)
        assert len(result_ids) == 2

    def test_model_unavailable_returns_empty_list(self, repo, monkeypatch):

        repository, folder = repo
        _write_json(folder / "gen1.json")
        repository.sync_folder(folder)

        monkeypatch.setattr(embedding, "compute_query_embedding", lambda text: None)

        assert repository.search_semantic("cat") == []
