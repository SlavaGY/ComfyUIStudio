"""Тесты для app/core/generation_filter.py.

Запуск: pytest tests/test_generation_filter.py -v
"""

from pathlib import Path

import pytest

from comfyui_studio.promptvault.core.generation import Generation, ImageData, LoraData
from comfyui_studio.promptvault.core.generation_filter import FilterOptions, GenerationFilter


def _make_gen(
    id: int,
    positive: str = "",
    negative: str = "",
    model: str = "modelA",
    sampler: str = "euler",
    cfg: float = 7.0,
    steps: int = 20,
    favorite: bool = False,
    rating: int = 0,
    loras: list[str] | None = None,
    custom_tags: list[str] | None = None,
) -> Generation:

    return Generation(
        id=id,
        path=Path(f"/tmp/gen_{id}.json"),
        timestamp=f"t{id:05d}",
        generation_time=float(id),
        model=model,
        cfg=cfg,
        steps=steps,
        sampler=sampler,
        positive=positive,
        negative=negative,
        images=[ImageData(file=f"img_{id}.png")],
        loras=[LoraData(filename=name, strength=1.0) for name in (loras or [])],
        favorite=favorite,
        rating=rating,
        custom_tags=custom_tags or [],
    )


@pytest.fixture
def generations() -> list[Generation]:

    return [
        _make_gen(1, positive="a cat sitting on a mat", cfg=7.0, steps=20),
        _make_gen(2, positive="a dog running in a park", cfg=5.0, steps=30),
        _make_gen(3, positive="a cat and a dog playing", cfg=9.0, steps=40),
    ]


class TestSearch:

    def test_single_word_matches_substring(self, generations):

        options = FilterOptions(search="cat")
        result = GenerationFilter.apply(generations, options)

        assert {g.id for g in result} == {1, 3}

    def test_multiple_words_require_all_present_and(self, generations):
        """Ключевое поведение: несколько слов ищутся через И — должны
        встретиться ВСЕ слова (в любых полях, не обязательно рядом)."""

        options = FilterOptions(search="cat dog")
        result = GenerationFilter.apply(generations, options)

        # только gen3 содержит оба слова одновременно
        assert {g.id for g in result} == {3}

    def test_words_can_match_in_any_order(self, generations):

        options = FilterOptions(search="dog cat")
        result = GenerationFilter.apply(generations, options)

        assert {g.id for g in result} == {3}

    def test_no_match_returns_empty(self, generations):

        options = FilterOptions(search="elephant")
        result = GenerationFilter.apply(generations, options)

        assert result == []

    def test_empty_search_returns_all(self, generations):

        options = FilterOptions(search="")
        result = GenerationFilter.apply(generations, options)

        assert len(result) == len(generations)

    def test_search_is_case_insensitive(self, generations):

        options = FilterOptions(search="CAT")
        result = GenerationFilter.apply(generations, options)

        assert {g.id for g in result} == {1, 3}


class TestCfgStepsRange:

    def test_min_cfg(self, generations):

        options = FilterOptions(min_cfg=7.0)
        result = GenerationFilter.apply(generations, options)

        assert {g.id for g in result} == {1, 3}

    def test_max_cfg(self, generations):

        options = FilterOptions(max_cfg=7.0)
        result = GenerationFilter.apply(generations, options)

        assert {g.id for g in result} == {1, 2}

    def test_cfg_range_both_bounds(self, generations):

        options = FilterOptions(min_cfg=6.0, max_cfg=8.0)
        result = GenerationFilter.apply(generations, options)

        assert {g.id for g in result} == {1}

    def test_min_steps(self, generations):

        options = FilterOptions(min_steps=30)
        result = GenerationFilter.apply(generations, options)

        assert {g.id for g in result} == {2, 3}

    def test_max_steps(self, generations):

        options = FilterOptions(max_steps=20)
        result = GenerationFilter.apply(generations, options)

        assert {g.id for g in result} == {1}

    def test_no_range_set_returns_all(self, generations):

        options = FilterOptions()
        result = GenerationFilter.apply(generations, options)

        assert len(result) == len(generations)


class TestCombinedFilters:

    def test_search_and_cfg_range_combine_with_and(self, generations):

        options = FilterOptions(search="cat", min_cfg=8.0)
        result = GenerationFilter.apply(generations, options)

        # только gen3 одновременно содержит "cat" И имеет cfg >= 8
        assert {g.id for g in result} == {3}

    def test_model_and_search(self, generations):

        options = FilterOptions(model="modelA", search="dog")

        result = GenerationFilter.apply(generations, options)

        # все генерации фикстуры имеют model="modelA" — фильтр по модели
        # не сужает выборку, дальше действует только search
        assert {g.id for g in result} == {2, 3}


class TestFavoritesAndRating:

    def test_favorites_only_true(self):

        gens = [
            _make_gen(1, favorite=True),
            _make_gen(2, favorite=False),
        ]

        options = FilterOptions(favorites_only=True)
        result = GenerationFilter.apply(gens, options)

        assert {g.id for g in result} == {1}

    def test_favorites_only_false(self):

        gens = [
            _make_gen(1, favorite=True),
            _make_gen(2, favorite=False),
        ]

        options = FilterOptions(favorites_only=False)
        result = GenerationFilter.apply(gens, options)

        assert {g.id for g in result} == {2}

    def test_min_rating(self):

        gens = [
            _make_gen(1, rating=2),
            _make_gen(2, rating=4),
            _make_gen(3, rating=0),
        ]

        options = FilterOptions(min_rating=3)
        result = GenerationFilter.apply(gens, options)

        assert {g.id for g in result} == {2}


class TestLoraFilter:

    def test_requires_all_selected_loras_present(self):

        gens = [
            _make_gen(1, loras=["A"]),
            _make_gen(2, loras=["A", "B"]),
            _make_gen(3, loras=["A", "B", "C"]),
        ]

        options = FilterOptions(loras=["A", "B"])
        result = GenerationFilter.apply(gens, options)

        # логика "И": должны быть ОБЕ A и B; лишние (C) не мешают
        assert {g.id for g in result} == {2, 3}

    def test_excludes_generations_with_any_excluded_lora(self):

        gens = [
            _make_gen(1, loras=["A"]),
            _make_gen(2, loras=["A", "B"]),
            _make_gen(3, loras=["C"]),
        ]

        options = FilterOptions(excluded_loras=["B"])
        result = GenerationFilter.apply(gens, options)

        assert {g.id for g in result} == {1, 3}

    def test_include_and_exclude_combine(self):

        gens = [
            _make_gen(1, loras=["A"]),
            _make_gen(2, loras=["A", "B"]),
            _make_gen(3, loras=["A", "C"]),
        ]

        options = FilterOptions(loras=["A"], excluded_loras=["B"])
        result = GenerationFilter.apply(gens, options)

        assert {g.id for g in result} == {1, 3}


class TestCustomTagsFilter:

    def test_requires_all_selected_tags_present(self):

        gens = [
            _make_gen(1, custom_tags=["cat"]),
            _make_gen(2, custom_tags=["cat", "outdoors"]),
            _make_gen(3, custom_tags=["cat", "outdoors", "sunny"]),
        ]

        options = FilterOptions(custom_tags=["cat", "outdoors"])
        result = GenerationFilter.apply(gens, options)

        assert {g.id for g in result} == {2, 3}

    def test_matches_case_insensitively(self):

        gens = [_make_gen(1, custom_tags=["Cat"])]

        options = FilterOptions(custom_tags=["cat"])
        result = GenerationFilter.apply(gens, options)

        assert {g.id for g in result} == {1}

    def test_none_means_no_filtering(self):

        gens = [_make_gen(1, custom_tags=[]), _make_gen(2, custom_tags=["cat"])]

        options = FilterOptions(custom_tags=None)
        result = GenerationFilter.apply(gens, options)

        assert {g.id for g in result} == {1, 2}

    def test_excludes_generations_with_any_excluded_tag(self):

        gens = [
            _make_gen(1, custom_tags=["cat"]),
            _make_gen(2, custom_tags=["cat", "dog"]),
            _make_gen(3, custom_tags=["bird"]),
        ]

        options = FilterOptions(excluded_custom_tags=["dog"])
        result = GenerationFilter.apply(gens, options)

        assert {g.id for g in result} == {1, 3}

    def test_exclusion_matches_case_insensitively(self):

        gens = [_make_gen(1, custom_tags=["Cat"])]

        options = FilterOptions(excluded_custom_tags=["cat"])
        result = GenerationFilter.apply(gens, options)

        assert result == []

    def test_include_and_exclude_combine(self):

        gens = [
            _make_gen(1, custom_tags=["cat"]),
            _make_gen(2, custom_tags=["cat", "dog"]),
            _make_gen(3, custom_tags=["cat", "bird"]),
        ]

        options = FilterOptions(custom_tags=["cat"], excluded_custom_tags=["dog"])
        result = GenerationFilter.apply(gens, options)

        assert {g.id for g in result} == {1, 3}
