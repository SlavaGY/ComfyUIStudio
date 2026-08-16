"""Тесты для оптимизации памяти: extra_data (произвольные "лишние"
верхнеуровневые ключи исходного JSON — например, полный workflow-граф
у ComfyUI-подобных генераторов, может весить сотни КБ на файл) больше
не загружается в резидентные в памяти объекты Generation при массовой
загрузке — только по явному запросу через get_generation_extra_data.

Это единственная причина держать список Generation'ов целой библиотеки
в памяти дорогим: без extra_data один объект — единицы КБ, поэтому
полноценный переход на id+LRU-кэш архитектуру не потребовался — см.
обсуждение в TODO.md.
"""

import json

import pytest

from app.core.repository import GenerationRepository


def _write_json(path, extra_data=None, **overrides):

    data = {
        "timestamp": "ts1",
        "generation_time": 1.0,
        "model_name": "modelA",
        "sampler_name": "Euler",
        "cfg": 7.0,
        "steps": 20,
        "positive_text": "a cat",
        "negative_text": "blurry",
        "images": [],
        "loras": [],
    }

    if extra_data:
        data.update(extra_data)  # незнакомые ключи -> extra_data

    data.update(overrides)

    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    return path


@pytest.fixture
def repo(tmp_path):

    db_path = tmp_path / "test.db"
    repository = GenerationRepository(db_path)

    yield repository, tmp_path

    repository.close()


class TestExtraDataNotEagerlyLoaded:

    def test_load_generations_does_not_populate_extra_data(self, repo):

        repository, root = repo

        _write_json(root / "g1.json", extra_data={"workflow": {"huge": "x" * 100_000}})

        repository.sync_folder(root)

        [gen] = repository.load_generations(root)

        assert gen.extra_data == {}

    def test_load_generations_page_does_not_populate_extra_data(self, repo):

        repository, root = repo

        _write_json(root / "g1.json", extra_data={"workflow": {"huge": "x" * 100_000}})

        repository.sync_folder(root)

        [gen] = repository.load_generations_page(root, offset=0, limit=10)

        assert gen.extra_data == {}

    def test_get_generation_does_not_populate_extra_data(self, repo):

        repository, root = repo

        _write_json(root / "g1.json", extra_data={"workflow": {"huge": "x" * 100_000}})

        repository.sync_folder(root)

        [gen] = repository.load_generations(root)
        fetched = repository.get_generation(gen.id)

        assert fetched.extra_data == {}

    def test_other_fields_are_still_fully_populated(self, repo):
        """Убедиться, что урезание _GENERATION_SELECT не задело
        остальные поля."""

        repository, root = repo

        _write_json(
            root / "g1.json",
            extra_data={"workflow": {"a": 1}},
            model_name="modelX", cfg=9.5, steps=33,
        )

        repository.sync_folder(root)

        [gen] = repository.load_generations(root)

        assert gen.model == "modelX"
        assert gen.cfg == 9.5
        assert gen.steps == 33
        assert gen.timestamp == "ts1"


class TestGetGenerationExtraDataOnDemand:

    def test_returns_full_extra_data_for_known_id(self, repo):

        repository, root = repo

        _write_json(root / "g1.json", extra_data={"workflow": {"node": 42}})

        repository.sync_folder(root)

        [gen] = repository.load_generations(root)

        extra = repository.get_generation_extra_data(gen.id)

        assert extra == {"workflow": {"node": 42}}

    def test_returns_empty_dict_for_unknown_id(self, repo):

        repository, _root = repo

        assert repository.get_generation_extra_data(999999) == {}

    def test_returns_empty_dict_when_no_extra_keys_in_source(self, repo):

        repository, root = repo

        _write_json(root / "g1.json")  # без лишних ключей

        repository.sync_folder(root)

        [gen] = repository.load_generations(root)

        assert repository.get_generation_extra_data(gen.id) == {}
