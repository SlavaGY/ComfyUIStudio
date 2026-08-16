"""Тесты для пользовательских тегов (задача: пользовательские теги) —
GenerationRepository.get_custom_tags/set_custom_tags/available_custom_tags.

Запуск: pytest tests/test_repository_custom_tags.py -v
"""

import json

import pytest

from app.core.repository import GenerationRepository


def _write_json(path, **overrides):

    data = {
        "timestamp": "ts1",
        "generation_time": 1.0,
        "model_name": "modelA",
        "sampler_name": "Euler",
        "cfg": 7.0,
        "steps": 20,
        "positive_text": "a cat",
        "negative_text": "blurry",
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


def _sync_and_get_id(repository, folder):

    repository.sync_folder(folder)
    gens = repository.load_generations(folder)
    assert len(gens) == 1
    return gens[0].id


class TestSetAndGetCustomTags:

    def test_empty_by_default(self, repo):

        repository, folder = repo
        _write_json(folder / "gen1.json")
        gid = _sync_and_get_id(repository, folder)

        assert repository.get_custom_tags(gid) == []

    def test_set_then_get(self, repo):

        repository, folder = repo
        _write_json(folder / "gen1.json")
        gid = _sync_and_get_id(repository, folder)

        repository.set_custom_tags(gid, ["cat", "outdoors"])

        assert repository.get_custom_tags(gid) == ["cat", "outdoors"]

    def test_set_is_a_full_replace(self, repo):

        repository, folder = repo
        _write_json(folder / "gen1.json")
        gid = _sync_and_get_id(repository, folder)

        repository.set_custom_tags(gid, ["cat", "outdoors"])
        repository.set_custom_tags(gid, ["dog"])

        assert repository.get_custom_tags(gid) == ["dog"]

    def test_set_dedupes_case_insensitively_keeping_first_casing(self, repo):

        repository, folder = repo
        _write_json(folder / "gen1.json")
        gid = _sync_and_get_id(repository, folder)

        repository.set_custom_tags(gid, ["Cat", "cat", "CAT"])

        assert repository.get_custom_tags(gid) == ["Cat"]

    def test_set_drops_blank_and_whitespace_only_tags(self, repo):

        repository, folder = repo
        _write_json(folder / "gen1.json")
        gid = _sync_and_get_id(repository, folder)

        repository.set_custom_tags(gid, ["cat", "  ", "", "dog"])

        assert repository.get_custom_tags(gid) == ["cat", "dog"]

    def test_set_empty_list_clears_tags(self, repo):

        repository, folder = repo
        _write_json(folder / "gen1.json")
        gid = _sync_and_get_id(repository, folder)

        repository.set_custom_tags(gid, ["cat"])
        repository.set_custom_tags(gid, [])

        assert repository.get_custom_tags(gid) == []


class TestCustomTagsSurviveMetadataEdits:

    def test_tags_survive_update_generation(self, repo):
        """custom_tags — не часть исходного JSON (в отличие от loras),
        поэтому редактирование других метаданных не должно их стирать
        (как и favorite/rating)."""

        repository, folder = repo
        _write_json(folder / "gen1.json")
        gid = _sync_and_get_id(repository, folder)

        repository.set_custom_tags(gid, ["cat"])
        repository.update_generation(gid, {"cfg": 9.0})

        assert repository.get_custom_tags(gid) == ["cat"]

    def test_tags_loaded_into_generation_object(self, repo):

        repository, folder = repo
        _write_json(folder / "gen1.json")
        gid = _sync_and_get_id(repository, folder)

        repository.set_custom_tags(gid, ["cat", "outdoors"])

        [gen] = repository.load_generations(folder)
        assert gen.custom_tags == ["cat", "outdoors"]

        single = repository.get_generation(gid)
        assert single.custom_tags == ["cat", "outdoors"]

    def test_tags_deleted_when_generation_deleted(self, repo):

        repository, folder = repo
        _write_json(folder / "gen1.json")
        gid = _sync_and_get_id(repository, folder)

        repository.set_custom_tags(gid, ["cat"])
        repository.delete_generation(gid)

        # generation_id больше не существует — ON DELETE CASCADE должен
        # был убрать и связанные custom_tags; get_custom_tags на
        # несуществующий id просто возвращает пустой список
        assert repository.get_custom_tags(gid) == []


class TestAvailableCustomTags:

    def test_returns_distinct_tags_within_folder(self, repo):

        repository, folder = repo
        _write_json(folder / "gen1.json", timestamp="ts1")
        _write_json(folder / "gen2.json", timestamp="ts2", generation_time=2.0)
        repository.sync_folder(folder)

        gens = repository.load_generations(folder)
        repository.set_custom_tags(gens[0].id, ["cat", "shared"])
        repository.set_custom_tags(gens[1].id, ["dog", "shared"])

        assert repository.available_custom_tags(folder) == {"cat", "dog", "shared"}

    def test_empty_when_no_tags(self, repo):

        repository, folder = repo
        _write_json(folder / "gen1.json")
        repository.sync_folder(folder)

        assert repository.available_custom_tags(folder) == set()
