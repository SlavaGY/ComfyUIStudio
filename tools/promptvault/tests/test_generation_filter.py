"""Тесты для app/core/generation_filter.py.

Запуск: pytest tests/test_generation_filter.py -v
"""

from pathlib import Path

import numpy as np
import pytest

from comfyui_studio.promptvault.core import embedding, generation_filter
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
    embedding: bytes | None = None,
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
        embedding=embedding,
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


# ------------------------------------------------------------
# семантический поиск (задача 3.1)

# небольшой фиксированный "словарь тем" — тексты кодируются как
# multi-hot вектор по этим темам и L2-нормализуются, что позволяет
# детерминированно проверять cosine-based ранжирование без реальной
# ML-модели (см. также tests/test_embedding.py::_FakeModel)
_TOPICS = ["cat", "dog", "forest", "night", "spaceship", "boy", "girl"]


def _topic_vector(text: str) -> np.ndarray:

    words = text.lower().replace(",", " ").split()
    vec = np.array([words.count(t) for t in _TOPICS], dtype=np.float32)

    if vec.sum() == 0:
        vec = np.ones(len(_TOPICS), dtype=np.float32)

    return vec / np.linalg.norm(vec)


def _query_vector_bytes(text: str) -> bytes:
    """Имитирует compute_query_embedding — ОДИН вектор на весь текст."""

    return _topic_vector(text).astype(np.float32).tobytes()


def _doc_vector_bytes(*tags: str) -> bytes:
    """Имитирует compute_embedding для промпта ГЕНЕРАЦИИ — по вектору
    на каждый переданный тег, конкатенированные подряд (см.
    app/core/embedding.py: bytes_to_chunks/cosine_similarity берёт
    максимум сходства среди этих векторов, а не сходство с одним
    усреднённым)."""

    return np.stack([_topic_vector(tag) for tag in tags]).astype(np.float32).tobytes()


@pytest.fixture
def fake_semantic_embedding(monkeypatch):
    """Подменяет embedding.compute_query_embedding на детерминированную
    функцию по темам — GenerationFilter вызывает именно её для запроса
    (через `from comfyui_studio.promptvault.core import embedding; embedding.compute_query_embedding(...)`),
    поэтому патч на атрибут модуля подхватывается автоматически.
    EMBEDDING_DIM тоже подменяется — иначе bytes_to_chunks/
    cosine_similarity внутри GenerationFilter будут резать чанки по
    настоящей размерности модели, а не по размеру тестовых векторов.

    SEMANTIC_SIMILARITY_THRESHOLD патчится на фиксированное тестовое
    значение — продакшен-значение в app/config.py настраивается
    эмпирически под конкретную ML-модель и периодически меняется (при
    смене MODEL_NAME в embedding.py), тесты не должны от него зависеть.
    Патчим ИМЕННО comfyui_studio.promptvault.core.generation_filter.SEMANTIC_SIMILARITY_THRESHOLD
    (а не comfyui_studio.promptvault.config) — generation_filter делает `from comfyui_studio.promptvault.config import
    SEMANTIC_SIMILARITY_THRESHOLD`, так что имя уже привязано к его
    собственному пространству имён и патч на comfyui_studio.promptvault.config его не затронет."""

    def _fake_compute_query(text: str) -> bytes | None:
        if not text or not text.strip():
            return None
        return _query_vector_bytes(text)

    monkeypatch.setattr(embedding, "compute_query_embedding", _fake_compute_query)
    monkeypatch.setattr(embedding, "EMBEDDING_DIM", len(_TOPICS))
    monkeypatch.setattr(generation_filter, "SEMANTIC_SIMILARITY_THRESHOLD", 0.5)


@pytest.fixture
def semantic_generations() -> list[Generation]:

    return [
        _make_gen(1, positive="a cat in the forest", embedding=_doc_vector_bytes("cat", "forest")),
        _make_gen(2, positive="a dog at night", embedding=_doc_vector_bytes("dog", "night")),
        _make_gen(3, positive="spaceship flying", embedding=_doc_vector_bytes("spaceship")),
        _make_gen(4, positive="no embedding yet", embedding=None),
    ]


class TestSemanticSearch:

    def test_empty_semantic_query_returns_all(self, semantic_generations, fake_semantic_embedding):

        options = FilterOptions(semantic_query="")
        result = GenerationFilter.apply(semantic_generations, options)

        assert len(result) == len(semantic_generations)

    def test_matches_related_meaning(self, semantic_generations, fake_semantic_embedding):

        options = FilterOptions(semantic_query="cat forest")
        result = GenerationFilter.apply(semantic_generations, options)

        assert {g.id for g in result} == {1}

    def test_unrelated_generations_are_excluded_by_threshold(
        self, semantic_generations, fake_semantic_embedding
    ):

        options = FilterOptions(semantic_query="spaceship")
        result = GenerationFilter.apply(semantic_generations, options)

        assert {g.id for g in result} == {3}

    def test_short_query_finds_matching_tag_among_many_unrelated_tags(
        self, fake_semantic_embedding
    ):
        """Ключевое поведение исправления по-тегового поиска: короткий
        запрос из одного слова должен находить свой тег даже в промпте
        с кучей других, несвязанных тегов — не тонуть в их среднем."""

        gens = [
            _make_gen(
                1,
                embedding=_doc_vector_bytes(
                    "forest", "night", "cat", "girl", "masterpiece", "boy", "high quality"
                ),
            ),
        ]

        options = FilterOptions(semantic_query="boy")
        result = GenerationFilter.apply(gens, options)

        assert {g.id for g in result} == {1}

    def test_generation_without_embedding_never_matches(
        self, semantic_generations, fake_semantic_embedding
    ):

        options = FilterOptions(semantic_query="cat dog forest night spaceship")
        result = GenerationFilter.apply(semantic_generations, options)

        assert 4 not in {g.id for g in result}

    def test_results_sorted_by_relevance_descending(self, fake_semantic_embedding):

        gens = [
            _make_gen(1, embedding=_doc_vector_bytes("cat")),
            _make_gen(2, embedding=_doc_vector_bytes("cat", "forest")),
            _make_gen(3, embedding=_doc_vector_bytes("cat", "forest", "night")),
        ]

        options = FilterOptions(semantic_query="cat forest night")
        result = GenerationFilter.apply(gens, options)

        # у всех трёх есть тег "cat forest night" целиком не совпадающий
        # ни с одним отдельным тегом идеально, но gen3 содержит тег
        # "night" который есть и в запросе — как минимум порядок по
        # убыванию релевантности должен соблюдаться
        scores = [g.semantic_score for g in result]
        assert scores == sorted(scores, reverse=True)

    def test_combines_with_other_filters_via_and(self, fake_semantic_embedding):

        gens = [
            _make_gen(1, model="modelA", embedding=_doc_vector_bytes("cat", "forest")),
            _make_gen(2, model="modelB", embedding=_doc_vector_bytes("cat", "forest")),
        ]

        options = FilterOptions(semantic_query="cat forest", model="modelA")
        result = GenerationFilter.apply(gens, options)

        assert {g.id for g in result} == {1}

    def test_falls_back_to_keyword_search_when_model_unavailable(
        self, semantic_generations, monkeypatch
    ):
        """Если библиотека эмбеддингов недоступна, семантический поиск
        не должен молча возвращать пустой список — деградирует до
        обычного текстового AND-поиска по positive/negative."""

        monkeypatch.setattr(embedding, "compute_query_embedding", lambda text: None)

        options = FilterOptions(semantic_query="cat")
        result = GenerationFilter.apply(semantic_generations, options)

        assert {g.id for g in result} == {1}


class TestRankBySemanticQuery:
    """GenerationFilter.rank_by_semantic_query — та же логика
    ранжирования по семантическому сходству, что и ветка
    semantic_query внутри apply(), вынесенная отдельно для случая,
    когда остальные условия уже отфильтрованы в SQL (см.
    GenerationFilterSQL/GenerationRepository.load_filtered_for_semantic)
    и GalleryManager.apply_filters передаёт сюда уже суженный список
    кандидатов вместо всей папки целиком."""

    def test_empty_query_returns_all(self, semantic_generations, fake_semantic_embedding):

        result = GenerationFilter.rank_by_semantic_query(semantic_generations, "")

        assert len(result) == len(semantic_generations)

    def test_matches_related_meaning(self, semantic_generations, fake_semantic_embedding):

        result = GenerationFilter.rank_by_semantic_query(semantic_generations, "cat forest")

        assert {g.id for g in result} == {1}

    def test_unrelated_generations_are_excluded_by_threshold(
        self, semantic_generations, fake_semantic_embedding
    ):

        result = GenerationFilter.rank_by_semantic_query(semantic_generations, "spaceship")

        assert {g.id for g in result} == {3}

    def test_generation_without_embedding_never_matches(
        self, semantic_generations, fake_semantic_embedding
    ):

        result = GenerationFilter.rank_by_semantic_query(
            semantic_generations, "cat dog forest night spaceship"
        )

        assert 4 not in {g.id for g in result}

    def test_results_sorted_by_relevance_descending(self, fake_semantic_embedding):

        gens = [
            _make_gen(1, embedding=_doc_vector_bytes("cat")),
            _make_gen(2, embedding=_doc_vector_bytes("cat", "forest")),
            _make_gen(3, embedding=_doc_vector_bytes("cat", "forest", "night")),
        ]

        result = GenerationFilter.rank_by_semantic_query(gens, "cat forest night")

        scores = [g.semantic_score for g in result]
        assert scores == sorted(scores, reverse=True)

    def test_falls_back_to_keyword_search_when_model_unavailable(
        self, semantic_generations, monkeypatch
    ):
        """Если библиотека эмбеддингов недоступна, ранжирование не
        должно молча возвращать пустой список — деградирует до обычного
        текстового AND-поиска по positive/negative, сохраняя порядок
        входного списка (реальную сортировку уже сделал вызывающий SQL
        — см. GenerationSorterSQL — переупорядочивать здесь нечем)."""

        monkeypatch.setattr(embedding, "compute_query_embedding", lambda text: None)

        result = GenerationFilter.rank_by_semantic_query(semantic_generations, "cat")

        assert [g.id for g in result] == [1]

    def test_strips_whitespace_only_query_to_return_all(
        self, semantic_generations, fake_semantic_embedding
    ):

        result = GenerationFilter.rank_by_semantic_query(semantic_generations, "   ")

        assert len(result) == len(semantic_generations)
