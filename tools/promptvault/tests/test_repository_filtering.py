"""Тесты для SQL-фильтрации/сортировки в app/core/repository.py:

- count_filtered / load_filtered_page / get_filtered_ids —
  GenerationFilterSQL/GenerationSorterSQL применяются целиком в SQL
  (задача: перенос GenerationFilter/GenerationSorter на SQL);
- load_filtered_for_semantic — тот же набор условий (кроме
  semantic_query), без LIMIT/OFFSET, для ранжирования по векторному
  сходству в Python.

Запуск: pytest tests/test_repository_filtering.py -v
"""

import json

import pytest

from app.core.generation_filter import FilterOptions
from app.core.repository import GenerationRepository
from app.core.sort_options import SortMode


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
        "loras": [{"filename": "loraX.safetensors", "strength": 0.8}],
    }
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


class TestCountAndLoadFilteredPage:

    def test_no_filters_matches_count_generations(self, repo):

        repository, root = repo

        for i in range(3):
            _write_json(root / f"g{i}.json", timestamp=f"t{i}", generation_time=float(i))

        repository.sync_folder(root)

        options = FilterOptions()

        assert repository.count_filtered(root, options) == 3
        assert len(repository.load_filtered_page(root, options, SortMode.NEWEST)) == 3

    def test_filters_by_model(self, repo):

        repository, root = repo

        _write_json(root / "g1.json", timestamp="t1", model_name="modelA")
        _write_json(root / "g2.json", timestamp="t2", model_name="modelB")

        repository.sync_folder(root)

        options = FilterOptions(model="modelA")

        assert repository.count_filtered(root, options) == 1

        page = repository.load_filtered_page(root, options, SortMode.NEWEST)
        assert [g.model for g in page] == ["modelA"]

    def test_filters_by_sampler(self, repo):

        repository, root = repo

        _write_json(root / "g1.json", timestamp="t1", sampler_name="Euler")
        _write_json(root / "g2.json", timestamp="t2", sampler_name="DPM++")

        repository.sync_folder(root)

        options = FilterOptions(sampler="DPM++")

        page = repository.load_filtered_page(root, options, SortMode.NEWEST)
        assert [g.sampler for g in page] == ["DPM++"]

    def test_filters_by_cfg_range(self, repo):

        repository, root = repo

        for i, cfg in enumerate([1.0, 5.0, 9.0]):
            _write_json(root / f"g{i}.json", timestamp=f"t{i}", generation_time=float(i), cfg=cfg)

        repository.sync_folder(root)

        options = FilterOptions(min_cfg=4.0, max_cfg=9.0)

        page = repository.load_filtered_page(root, options, SortMode.CFG)
        assert sorted(g.cfg for g in page) == [5.0, 9.0]

    def test_filters_by_steps_range(self, repo):

        repository, root = repo

        for i, steps in enumerate([10, 20, 30]):
            _write_json(root / f"g{i}.json", timestamp=f"t{i}", generation_time=float(i), steps=steps)

        repository.sync_folder(root)

        options = FilterOptions(min_steps=15, max_steps=25)

        page = repository.load_filtered_page(root, options, SortMode.NEWEST)
        assert [g.steps for g in page] == [20]

    def test_filters_by_favorites_only_true(self, repo):

        repository, root = repo

        _write_json(root / "g1.json", timestamp="t1")
        _write_json(root / "g2.json", timestamp="t2")

        repository.sync_folder(root)

        gens = {g.timestamp: g.id for g in repository.load_generations(root)}
        repository.set_favorite(gens["t1"], True)

        options = FilterOptions(favorites_only=True)

        page = repository.load_filtered_page(root, options, SortMode.NEWEST)
        assert [g.timestamp for g in page] == ["t1"]

    def test_filters_by_favorites_only_false(self, repo):

        repository, root = repo

        _write_json(root / "g1.json", timestamp="t1")
        _write_json(root / "g2.json", timestamp="t2")

        repository.sync_folder(root)

        gens = {g.timestamp: g.id for g in repository.load_generations(root)}
        repository.set_favorite(gens["t1"], True)

        options = FilterOptions(favorites_only=False)

        page = repository.load_filtered_page(root, options, SortMode.NEWEST)
        assert [g.timestamp for g in page] == ["t2"]

    def test_filters_by_min_rating(self, repo):

        repository, root = repo

        _write_json(root / "g1.json", timestamp="t1")
        _write_json(root / "g2.json", timestamp="t2")

        repository.sync_folder(root)

        gens = {g.timestamp: g.id for g in repository.load_generations(root)}
        repository.set_rating(gens["t1"], 4)
        repository.set_rating(gens["t2"], 2)

        options = FilterOptions(min_rating=3)

        page = repository.load_filtered_page(root, options, SortMode.NEWEST)
        assert [g.timestamp for g in page] == ["t1"]

    def test_filters_by_required_lora(self, repo):

        repository, root = repo

        _write_json(
            root / "g1.json", timestamp="t1",
            loras=[{"filename": "loraA.safetensors", "strength": 1.0}],
        )
        _write_json(
            root / "g2.json", timestamp="t2",
            loras=[{"filename": "loraB.safetensors", "strength": 1.0}],
        )

        repository.sync_folder(root)

        options = FilterOptions(loras=["loraA.safetensors"])

        page = repository.load_filtered_page(root, options, SortMode.NEWEST)
        assert [g.timestamp for g in page] == ["t1"]

    def test_filters_by_excluded_lora(self, repo):

        repository, root = repo

        _write_json(
            root / "g1.json", timestamp="t1",
            loras=[{"filename": "loraA.safetensors", "strength": 1.0}],
        )
        _write_json(
            root / "g2.json", timestamp="t2",
            loras=[{"filename": "loraB.safetensors", "strength": 1.0}],
        )

        repository.sync_folder(root)

        options = FilterOptions(excluded_loras=["loraA.safetensors"])

        page = repository.load_filtered_page(root, options, SortMode.NEWEST)
        assert [g.timestamp for g in page] == ["t2"]

    def test_filters_by_required_custom_tag(self, repo):

        repository, root = repo

        _write_json(root / "g1.json", timestamp="t1")
        _write_json(root / "g2.json", timestamp="t2")

        repository.sync_folder(root)

        gens = {g.timestamp: g.id for g in repository.load_generations(root)}
        repository.set_custom_tags(gens["t1"], ["portrait"])

        options = FilterOptions(custom_tags=["portrait"])

        page = repository.load_filtered_page(root, options, SortMode.NEWEST)
        assert [g.timestamp for g in page] == ["t1"]

    def test_filters_by_excluded_custom_tag(self, repo):

        repository, root = repo

        _write_json(root / "g1.json", timestamp="t1")
        _write_json(root / "g2.json", timestamp="t2")

        repository.sync_folder(root)

        gens = {g.timestamp: g.id for g in repository.load_generations(root)}
        repository.set_custom_tags(gens["t1"], ["nsfw"])

        options = FilterOptions(excluded_custom_tags=["nsfw"])

        page = repository.load_filtered_page(root, options, SortMode.NEWEST)
        assert [g.timestamp for g in page] == ["t2"]

    def test_search_matches_positive_prompt(self, repo):

        repository, root = repo

        _write_json(root / "g1.json", timestamp="t1", positive_text="a cute cat")
        _write_json(root / "g2.json", timestamp="t2", positive_text="a big dog")

        repository.sync_folder(root)

        options = FilterOptions(search="cat")

        page = repository.load_filtered_page(root, options, SortMode.NEWEST)
        assert [g.timestamp for g in page] == ["t1"]

    def test_search_multiple_words_are_anded(self, repo):

        repository, root = repo

        _write_json(root / "g1.json", timestamp="t1", positive_text="a cute cat")
        _write_json(root / "g2.json", timestamp="t2", positive_text="a cute dog")

        repository.sync_folder(root)

        options = FilterOptions(search="cute cat")

        page = repository.load_filtered_page(root, options, SortMode.NEWEST)
        assert [g.timestamp for g in page] == ["t1"]

    def test_search_matches_lora_filename(self, repo):

        repository, root = repo

        _write_json(
            root / "g1.json", timestamp="t1",
            loras=[{"filename": "specialLora.safetensors", "strength": 1.0}],
        )
        _write_json(root / "g2.json", timestamp="t2", loras=[])

        repository.sync_folder(root)

        options = FilterOptions(search="speciallora")

        page = repository.load_filtered_page(root, options, SortMode.NEWEST)
        assert [g.timestamp for g in page] == ["t1"]

    def test_search_escapes_like_special_characters(self, repo):
        """% и _ в самом запросе поиска должны восприниматься буквально,
        а не как спецсимволы LIKE."""

        repository, root = repo

        _write_json(root / "g1.json", timestamp="t1", positive_text="50% off_sale")
        _write_json(root / "g2.json", timestamp="t2", positive_text="something else")

        repository.sync_folder(root)

        options = FilterOptions(search="50% off_sale")

        page = repository.load_filtered_page(root, options, SortMode.NEWEST)
        assert [g.timestamp for g in page] == ["t1"]

    def test_combines_multiple_conditions_with_and(self, repo):

        repository, root = repo

        _write_json(root / "g1.json", timestamp="t1", model_name="modelA", cfg=7.0)
        _write_json(root / "g2.json", timestamp="t2", model_name="modelA", cfg=1.0)
        _write_json(root / "g3.json", timestamp="t3", model_name="modelB", cfg=7.0)

        repository.sync_folder(root)

        options = FilterOptions(model="modelA", min_cfg=5.0)

        page = repository.load_filtered_page(root, options, SortMode.NEWEST)
        assert [g.timestamp for g in page] == ["t1"]

    def test_pagination_limit_offset(self, repo):

        repository, root = repo

        for i in range(5):
            _write_json(root / f"g{i}.json", timestamp=f"t{i}", generation_time=float(i))

        repository.sync_folder(root)

        options = FilterOptions()

        page1 = repository.load_filtered_page(root, options, SortMode.NEWEST, offset=0, limit=2)
        page2 = repository.load_filtered_page(root, options, SortMode.NEWEST, offset=2, limit=2)

        assert [g.timestamp for g in page1] == ["t4", "t3"]
        assert [g.timestamp for g in page2] == ["t2", "t1"]

    def test_favorites_always_sort_first(self, repo):

        repository, root = repo

        _write_json(root / "g1.json", timestamp="t1")
        _write_json(root / "g2.json", timestamp="t2")

        repository.sync_folder(root)

        gens = {g.timestamp: g.id for g in repository.load_generations(root)}
        # t1 в порядке timestamp DESC оказался бы ПОСЛЕ t2 — избранное
        # должно поднять его наверх несмотря на это
        repository.set_favorite(gens["t1"], True)

        page = repository.load_filtered_page(root, FilterOptions(), SortMode.NEWEST)
        assert [g.timestamp for g in page] == ["t1", "t2"]


class TestGetFilteredIds:

    def test_returns_only_matching_ids(self, repo):

        repository, root = repo

        _write_json(root / "g1.json", timestamp="t1", model_name="modelA")
        _write_json(root / "g2.json", timestamp="t2", model_name="modelB")

        repository.sync_folder(root)

        gens = {g.timestamp: g.id for g in repository.load_generations(root)}

        ids = repository.get_filtered_ids(root, FilterOptions(model="modelA"))
        assert ids == [gens["t1"]]


class TestLoadFilteredForSemantic:

    def test_applies_non_semantic_filters_without_limit(self, repo):

        repository, root = repo

        _write_json(root / "g1.json", timestamp="t1", model_name="modelA")
        _write_json(root / "g2.json", timestamp="t2", model_name="modelA")
        _write_json(root / "g3.json", timestamp="t3", model_name="modelB")

        repository.sync_folder(root)

        options = FilterOptions(model="modelA")

        result = repository.load_filtered_for_semantic(root, options, SortMode.NEWEST)
        assert {g.timestamp for g in result} == {"t1", "t2"}
