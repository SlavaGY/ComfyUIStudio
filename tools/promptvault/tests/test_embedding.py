"""Тесты для app/core/embedding.py.

Модель не загружается по-настоящему (это требует сети/скачивания
весов при первом запуске) — вместо неё подставляется фейковый объект
через monkeypatch на app.core.embedding.get_model, с детерминированным
encode(), достаточным, чтобы проверить сквозную логику модуля
(по-теговое кодирование документов, нормализация, батчинг, обработка
пустых строк, деградация при ошибках) независимо от реальной
ML-библиотеки.

Запуск: pytest tests/test_embedding.py -v
"""

from __future__ import annotations

import os

import numpy as np
import pytest

from app.core import embedding

# conftest.py заменяет embedding.get_model глобальной заглушкой для ВСЕХ
# тестов по умолчанию (см. _stub_embedding_model_by_default) — реальная
# реализация нужна здесь же (TestSetEnabled/TestTorchVersionCompatible),
# чтобы проверить её собственную логику (проверку _disabled_by_user и
# версии torch ДО попытки импорта sentence_transformers). Захватывается
# на уровне модуля — до того, как autouse-фикстура вообще успеет что-то
# запатчить для первого теста.
_REAL_GET_MODEL = embedding.get_model


class _FakeModel:
    """Простая детерминированная замена SentenceTransformer:
    отображает текст в вектор по набору "тем" (ключевых слов) — так,
    что тексты с общими темами дают близкие (после нормализации)
    вектора, а без общих тем — ортогональные. Этого достаточно, чтобы
    протестировать cosine_similarity/пороги без реальной модели."""

    TOPICS = ["cat", "dog", "forest", "night", "spaceship", "boy", "girl"]

    def encode(self, texts, normalize_embeddings=True, show_progress_bar=False, batch_size=None):

        single = isinstance(texts, str)

        if single:
            texts = [texts]

        vectors = []

        for text in texts:

            words = text.lower().split()
            vec = np.array(
                [words.count(topic) for topic in self.TOPICS],
                dtype=np.float32
            )

            if vec.sum() == 0:
                # хотя бы что-то ненулевое, чтобы не делить на ноль —
                # реальная модель никогда не отдаёт нулевой вектор
                vec = np.ones(len(self.TOPICS), dtype=np.float32)

            if normalize_embeddings:
                vec = vec / np.linalg.norm(vec)

            vectors.append(vec)

        return vectors[0] if single else np.array(vectors)


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """Сбрасывает ленивый синглтон модели между тестами, чтобы тесты
    не зависели от порядка выполнения.

    embedding.set_model/set_device_preference (задача: выбор модели
    эмбеддинга) мутируют MODEL_NAME/EMBEDDING_DIM/префиксы/
    _current_model_key/_device_preference НАПРЯМУЮ, а не через
    monkeypatch — сбрасываем их к дефолту (e5-large-v2/auto) явным
    вызовом set_model/set_device_preference и на входе, и на выходе,
    чтобы TestModelSelection/TestDevicePreference не просачивались в
    соседние тесты этого же файла."""

    monkeypatch.setattr(embedding, "_model", None)
    monkeypatch.setattr(embedding, "_load_failed", False)
    monkeypatch.setattr(embedding, "_disabled_by_user", False)
    embedding.set_model("e5-large-v2")
    embedding.set_device_preference("auto")
    yield
    monkeypatch.setattr(embedding, "_model", None)
    monkeypatch.setattr(embedding, "_load_failed", False)
    monkeypatch.setattr(embedding, "_disabled_by_user", False)
    embedding.set_model("e5-large-v2")
    embedding.set_device_preference("auto")


@pytest.fixture
def fake_model(monkeypatch):

    model = _FakeModel()
    monkeypatch.setattr(embedding, "get_model", lambda: model)
    # bytes_to_chunks/cosine_similarity опираются на EMBEDDING_DIM для
    # reshape — подменяем его на размер векторов фейковой модели,
    # иначе reshape будет считать по настоящей размерности (384)
    monkeypatch.setattr(embedding, "EMBEDDING_DIM", len(_FakeModel.TOPICS))
    return model


DIM = len(_FakeModel.TOPICS)


class TestSplitIntoChunks:

    def test_splits_on_commas(self):

        assert embedding._split_into_chunks("cat, dog, forest") == ["cat", "dog", "forest"]

    def test_splits_on_newlines(self):

        assert embedding._split_into_chunks("cat\ndog\nforest") == ["cat", "dog", "forest"]

    def test_strips_whitespace_and_drops_empty_parts(self):

        assert embedding._split_into_chunks("cat,, ,  dog  ,") == ["cat", "dog"]

    def test_no_separators_is_a_single_chunk(self):

        assert embedding._split_into_chunks("a cat in the forest") == ["a cat in the forest"]

    def test_empty_text_is_no_chunks(self):

        assert embedding._split_into_chunks("") == []
        assert embedding._split_into_chunks("   ") == []

    def test_caps_number_of_chunks(self):

        text = ",".join(f"tag{i}" for i in range(500))
        assert len(embedding._split_into_chunks(text)) == embedding.MAX_CHUNKS_PER_TEXT

    def test_semicolon_is_also_a_separator(self):

        assert embedding._split_into_chunks("cat; dog; forest") == ["cat", "dog", "forest"]

    def test_strips_lora_references_entirely(self):

        result = embedding._split_into_chunks("cat, <lora:someStyle:0.8>, forest")
        assert result == ["cat", "forest"]

    def test_strips_weight_suffix_but_keeps_tag_text(self):

        result = embedding._split_into_chunks("(masterpiece:1.2), (cat:0.8)")
        assert result == ["masterpiece", "cat"]

    def test_unwraps_brackets_preserving_inner_content(self):
        """Важно: скобки разворачиваются, а не вырезаются вместе с
        содержимым — иначе реальные теги внутри группировки
        ("(red hair, blue eyes:1.2)") терялись бы целиком."""

        result = embedding._split_into_chunks("(red hair, blue eyes:1.2), [bad hands]")
        assert result == ["red hair", "blue eyes", "bad hands"]

    def test_drops_pure_digit_tokens(self):

        result = embedding._split_into_chunks("cat, 12345, forest")
        assert result == ["cat", "forest"]

    def test_drops_single_character_tokens(self):

        result = embedding._split_into_chunks("cat, s, forest")
        assert result == ["cat", "forest"]


class TestComputeEmbedding:
    """compute_embedding() — эмбеддинг ПРОМПТА ГЕНЕРАЦИИ (документа),
    по тегам: bytes раскладываются на N векторов по EMBEDDING_DIM."""

    def test_empty_text_returns_none(self, fake_model):

        assert embedding.compute_embedding("") is None
        assert embedding.compute_embedding("   ") is None

    def test_single_tag_gives_one_chunk(self, fake_model):

        result = embedding.compute_embedding("cat")

        assert result is not None
        chunks = embedding.bytes_to_chunks(result)
        assert chunks.shape == (1, DIM)

    def test_comma_separated_tags_give_multiple_chunks(self, fake_model):

        result = embedding.compute_embedding("cat, dog, forest")

        chunks = embedding.bytes_to_chunks(result)
        assert chunks.shape == (3, DIM)

    def test_model_unavailable_returns_none_instead_of_raising(self, monkeypatch):

        def _raise():
            raise RuntimeError("модель недоступна")

        monkeypatch.setattr(embedding, "get_model", _raise)

        # не должно бросать исключение наружу — вызывающий код
        # (repository/generation_filter) не обязан оборачивать это в
        # try/except на каждый вызов
        assert embedding.compute_embedding("cat") is None

    def test_unexpected_model_error_is_caught(self, monkeypatch):

        class _BrokenModel:
            def encode(self, *a, **kw):
                raise ValueError("что-то пошло не так внутри модели")

        monkeypatch.setattr(embedding, "get_model", lambda: _BrokenModel())

        assert embedding.compute_embedding("cat") is None


class TestComputeEmbeddingsBatch:

    def test_empty_list_returns_empty_list(self, fake_model):

        assert embedding.compute_embeddings_batch([]) == []

    def test_preserves_order_and_length(self, fake_model):

        texts = ["cat", "", "dog, forest", "   "]
        results = embedding.compute_embeddings_batch(texts)

        assert len(results) == 4
        assert results[0] is not None
        assert results[1] is None
        assert results[2] is not None
        assert results[3] is None

        assert embedding.bytes_to_chunks(results[0]).shape == (1, DIM)
        assert embedding.bytes_to_chunks(results[2]).shape == (2, DIM)

    def test_batch_matches_single_computation(self, fake_model):

        batch_result = embedding.compute_embeddings_batch(["cat, dog"])[0]
        single_result = embedding.compute_embedding("cat, dog")

        assert batch_result == single_result

    def test_model_unavailable_returns_all_none(self, monkeypatch):

        def _raise():
            raise RuntimeError("недоступна")

        monkeypatch.setattr(embedding, "get_model", _raise)

        results = embedding.compute_embeddings_batch(["cat", "dog"])

        assert results == [None, None]


class TestComputeQueryEmbedding:
    """compute_query_embedding() — эмбеддинг ЗАПРОСА: всегда ОДИН
    вектор на весь текст, без разбиения на теги."""

    def test_empty_text_returns_none(self, fake_model):

        assert embedding.compute_query_embedding("") is None

    def test_not_split_by_commas(self, fake_model):

        result = embedding.compute_query_embedding("cat, dog, forest")

        arr = embedding.bytes_to_array(result)
        assert arr.shape == (DIM,)

    def test_model_unavailable_returns_none(self, monkeypatch):

        monkeypatch.setattr(embedding, "get_model", lambda: (_ for _ in ()).throw(RuntimeError()))

        assert embedding.compute_query_embedding("cat") is None


class TestCosineSimilarity:

    def test_identical_single_tag_gives_similarity_near_one(self, fake_model):

        query_vec = embedding.bytes_to_array(embedding.compute_query_embedding("cat"))
        doc = embedding.compute_embedding("cat")

        score = embedding.cosine_similarity(query_vec, doc)

        assert score == pytest.approx(1.0, abs=1e-5)

    def test_unrelated_topics_give_low_similarity(self, fake_model):

        query_vec = embedding.bytes_to_array(embedding.compute_query_embedding("cat"))
        doc = embedding.compute_embedding("spaceship")

        score = embedding.cosine_similarity(query_vec, doc)

        assert score == pytest.approx(0.0, abs=1e-5)

    def test_matches_best_tag_among_many_unrelated(self, fake_model):
        """Ключевое поведение исправления: короткий специфичный запрос
        должен находить СВОЙ тег даже среди многих несвязанных тегов —
        а не сравниваться с "усреднённым" вектором всего промпта."""

        query_vec = embedding.bytes_to_array(embedding.compute_query_embedding("boy"))

        doc = embedding.compute_embedding(
            "spaceship, forest, night, cat, dog, girl, masterpiece, boy, high quality"
        )

        score = embedding.cosine_similarity(query_vec, doc)

        assert score == pytest.approx(1.0, abs=1e-5)

    def test_mismatched_dimension_returns_zero(self, fake_model):

        query_vec = embedding.bytes_to_array(embedding.compute_query_embedding("cat"))
        wrong_dim_bytes = np.zeros(3, dtype=np.float32).tobytes()

        assert embedding.cosine_similarity(query_vec, wrong_dim_bytes) == 0.0

    def test_legacy_single_vector_embedding_still_readable(self, fake_model):
        """Эмбеддинги, посчитанные ДО перехода на по-теговое
        кодирование (один вектор на весь промпт), не должны ломать
        сравнение — bytes_to_chunks должен прочитать их как один чанк."""

        query_vec = embedding.bytes_to_array(embedding.compute_query_embedding("cat"))

        legacy_single_vector = embedding.bytes_to_array(
            embedding.compute_query_embedding("cat")
        ).tobytes()

        score = embedding.cosine_similarity(query_vec, legacy_single_vector)

        assert score == pytest.approx(1.0, abs=1e-5)


class TestBytesToChunks:

    def test_reshapes_flat_bytes_into_dim_sized_rows(self, fake_model):

        doc = embedding.compute_embedding("cat, dog, forest")
        chunks = embedding.bytes_to_chunks(doc)

        assert chunks.shape == (3, DIM)

    def test_corrupted_data_returns_empty_array(self, fake_model):

        garbage = b"\x01\x02\x03"  # длина не кратна EMBEDDING_DIM * 4 байта

        chunks = embedding.bytes_to_chunks(garbage)

        assert chunks.shape == (0, DIM)


class TestBoilerplateFiltering:
    """Регрессионные тесты на реальную жалобу: технические теги вроде
    score_9/rating_explicit/source_anime/BREAK не должны участвовать в
    эмбеддинге — их непредсказуемые вектора на практике оказываются
    ложно похожи на самые разные запросы (см. модуль docstring)."""

    def test_score_tags_are_filtered(self):

        assert embedding._is_boilerplate_tag("score_9") is True
        assert embedding._is_boilerplate_tag("score_8_up") is True
        assert embedding._is_boilerplate_tag("score_1_up") is True

    def test_rating_and_source_tags_are_filtered(self):

        assert embedding._is_boilerplate_tag("rating_explicit") is True
        assert embedding._is_boilerplate_tag("source_anime") is True

    def test_break_and_quality_boilerplate_are_filtered(self):

        assert embedding._is_boilerplate_tag("BREAK") is True
        assert embedding._is_boilerplate_tag("absurdres") is True
        assert embedding._is_boilerplate_tag("masterpiece") is True

    def test_case_insensitive(self):

        assert embedding._is_boilerplate_tag("Score_9") is True
        assert embedding._is_boilerplate_tag("MASTERPIECE") is True

    def test_descriptive_tags_are_not_filtered(self):

        assert embedding._is_boilerplate_tag("fox ears") is False
        assert embedding._is_boilerplate_tag("kimono") is False
        assert embedding._is_boilerplate_tag("boy") is False

    def test_filter_removes_boilerplate_but_keeps_content(self):

        chunks = ["score_9", "fox ears", "rating_explicit", "kimono", "BREAK"]

        result = embedding._filter_boilerplate(chunks)

        assert result == ["fox ears", "kimono"]

    def test_filter_keeps_everything_if_all_boilerplate(self):
        """Если промпт целиком состоит из технических тегов — лучше
        оставить их (шумный эмбеддинг), чем не получить эмбеддинг
        вообще."""

        chunks = ["score_9", "masterpiece", "BREAK"]

        assert embedding._filter_boilerplate(chunks) == chunks

    def test_compute_embedding_excludes_boilerplate_chunks(self, fake_model):
        """Сквозной тест реального сценария из жалобы: длинный промпт
        с кучей технических тегов и без единого упоминания мальчика —
        итоговый эмбеддинг не должен содержать чанк для этих тегов."""

        text = (
            "score_9, score_8_up, source_anime, fox ears, kimono, "
            "BREAK, rating_explicit, masterpiece"
        )

        result = embedding.compute_embedding(text)
        chunks = embedding.bytes_to_chunks(result)

        # после фильтрации остаются только "fox ears" и "kimono"
        assert chunks.shape == (2, DIM)


class TestE5Prefixes:
    """E5-модели требуют префиксов "query: "/"passage: " перед текстом
    — без них модель работает заметно хуже (см. модуль docstring).
    Эти тесты фиксируют, что префиксы реально применяются, а не просто
    задокументированы."""

    def test_query_gets_query_prefix(self, monkeypatch):

        received = {}

        class _RecordingModel:
            def encode(self, text, **kwargs):
                received["text"] = text
                return np.ones(embedding.EMBEDDING_DIM, dtype=np.float32)

        monkeypatch.setattr(embedding, "get_model", lambda: _RecordingModel())

        embedding.compute_query_embedding("мальчик")

        assert received["text"] == "query: мальчик"

    def test_document_chunks_get_passage_prefix(self, monkeypatch):

        received = {}

        class _RecordingModel:
            def encode(self, texts, **kwargs):
                received["texts"] = texts
                return np.ones((len(texts), embedding.EMBEDDING_DIM), dtype=np.float32)

        monkeypatch.setattr(embedding, "get_model", lambda: _RecordingModel())

        embedding.compute_embedding("cat, dog")

        assert received["texts"] == ["passage: cat", "passage: dog"]

    def test_batch_document_chunks_get_passage_prefix(self, monkeypatch):

        received = {}

        class _RecordingModel:
            def encode(self, texts, **kwargs):
                received["texts"] = texts
                return np.ones((len(texts), embedding.EMBEDDING_DIM), dtype=np.float32)

        monkeypatch.setattr(embedding, "get_model", lambda: _RecordingModel())

        embedding.compute_embeddings_batch(["cat", "dog, forest"])

        assert received["texts"] == ["passage: cat", "passage: dog", "passage: forest"]


class TestPickDevice:

    def test_returns_cpu_when_torch_not_installed(self, monkeypatch):

        import builtins

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "torch":
                raise ImportError("no torch")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)

        assert embedding._pick_device() == "cpu"

    def test_returns_cuda_when_available(self, monkeypatch):

        import types

        fake_torch = types.SimpleNamespace(
            cuda=types.SimpleNamespace(is_available=lambda: True)
        )

        import sys
        monkeypatch.setitem(sys.modules, "torch", fake_torch)

        assert embedding._pick_device() == "cuda"

    def test_returns_cpu_when_cuda_unavailable(self, monkeypatch):

        import types

        fake_torch = types.SimpleNamespace(
            cuda=types.SimpleNamespace(is_available=lambda: False)
        )

        import sys
        monkeypatch.setitem(sys.modules, "torch", fake_torch)

        assert embedding._pick_device() == "cpu"


class TestLoadAndVerifyModelOfflineFirst:
    """_load_and_verify_model пытается загрузить модель офлайн (из
    локального кэша HF Hub, без сети) первым делом, и только при
    неудаче откатывается на обычную загрузку с сетью."""

    def _patch_sentence_transformers(self, monkeypatch, factory):

        import sentence_transformers

        monkeypatch.setattr(sentence_transformers, "SentenceTransformer", factory)

    def test_offline_success_sets_and_restores_env_var(self, monkeypatch):

        seen_offline_flag = {}

        class _FakeModel:
            def encode(self, *a, **kw):
                seen_offline_flag["value"] = os.environ.get("HF_HUB_OFFLINE")
                return np.ones(4, dtype=np.float32)

        self._patch_sentence_transformers(monkeypatch, lambda name, device: _FakeModel())
        monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)

        result = embedding._load_and_verify_model("cpu")

        assert result is not None
        # во время попытки офлайн-загрузки флаг был выставлен...
        assert seen_offline_flag["value"] == "1"
        # ...а после успеха — восстановлен обратно (тут: убран, раз его
        # не было до вызова)
        assert "HF_HUB_OFFLINE" not in os.environ

    def test_restores_previous_env_var_value(self, monkeypatch):

        class _FakeModel:
            def encode(self, *a, **kw):
                return np.ones(4, dtype=np.float32)

        self._patch_sentence_transformers(monkeypatch, lambda name, device: _FakeModel())
        monkeypatch.setenv("HF_HUB_OFFLINE", "0")

        embedding._load_and_verify_model("cpu")

        assert os.environ.get("HF_HUB_OFFLINE") == "0"

    def test_falls_back_to_online_when_offline_cache_missing(self, monkeypatch):

        calls = []

        class _FailsOfflineModel:
            def __init__(self):
                calls.append(os.environ.get("HF_HUB_OFFLINE"))
                if len(calls) == 1:
                    raise OSError("не найдено в локальном кэше")

            def encode(self, *a, **kw):
                return np.ones(4, dtype=np.float32)

        self._patch_sentence_transformers(
            monkeypatch, lambda name, device: _FailsOfflineModel()
        )
        monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)

        result = embedding._load_and_verify_model("cpu")

        assert result is not None
        # первая попытка — офлайн (флаг выставлен), вторая — обычная
        # (флаг уже снят)
        assert calls == ["1", None]

    def test_raises_when_both_offline_and_online_fail(self, monkeypatch):

        class _AlwaysFailsModel:
            def __init__(self, *a, **kw):
                raise OSError("совсем недоступна")

        self._patch_sentence_transformers(
            monkeypatch, lambda name, device: _AlwaysFailsModel()
        )

        with pytest.raises(OSError):
            embedding._load_and_verify_model("cpu")


class TestIsAvailable:

    def test_true_when_load_has_not_failed(self):

        assert embedding.is_available() is True

    def test_false_after_load_failure(self, monkeypatch):

        monkeypatch.setattr(embedding, "_load_failed", True)

        assert embedding.is_available() is False

    def test_false_when_disabled_by_user(self):

        embedding.set_enabled(False)

        assert embedding.is_available() is False

    def test_true_again_after_re_enabling(self, monkeypatch):

        import sys
        import types

        monkeypatch.setitem(
            sys.modules, "sentence_transformers", types.ModuleType("sentence_transformers")
        )

        embedding.set_enabled(False)
        embedding.set_enabled(True)

        assert embedding.is_available() is True


class TestSetEnabled:
    """Задача: оптимизация памяти — пользователь может полностью
    отключить семантический поиск, чтобы модель эмбеддингов (~1.3 ГБ)
    никогда не загружалась."""

    def test_disabling_makes_get_model_raise_without_importing_sentence_transformers(
        self, monkeypatch
    ):
        """Критично: RuntimeError должен всплывать ДО попытки импорта
        sentence_transformers/создания SentenceTransformer — иначе
        отключение не спасает от аллокации весов модели."""

        monkeypatch.setattr(embedding, "get_model", _REAL_GET_MODEL)
        embedding.set_enabled(False)

        with pytest.raises(RuntimeError, match="отключён"):
            embedding.get_model()

    def test_disabling_does_not_set_load_failed(self, monkeypatch):
        """_load_failed — это "загрузка НЕ удалась и точка" (постоянный
        барьер до следующего перезапуска процесса); отключение
        пользователем — обратимо, поэтому не должно выставлять этот
        флаг, иначе повторное включение не позволило бы попробовать
        загрузку снова."""

        monkeypatch.setattr(embedding, "get_model", _REAL_GET_MODEL)
        embedding.set_enabled(False)

        try:
            embedding.get_model()
        except RuntimeError:
            pass

        assert embedding._load_failed is False

    def test_enabling_after_disabling_allows_load_attempt_again(self, monkeypatch):

        monkeypatch.setattr(embedding, "get_model", _REAL_GET_MODEL)

        embedding.set_enabled(False)
        embedding.set_enabled(True)

        # sentence-transformers не установлен в этом окружении -> упадёт
        # на импорте, а не на "отключено пользователем" — проверяем
        # именно это сообщение, чтобы отличить от предыдущего барьера
        with pytest.raises(RuntimeError, match="sentence-transformers"):
            embedding.get_model()


class TestTorchVersionCompatible:
    """_torch_version_compatible — задача: не выделять память под
    веса модели (~1.3 ГБ), если самопроверка в _load_and_verify_model
    всё равно гарантированно провалится из-за старого torch."""

    def test_compatible_version_returns_true(self, monkeypatch):

        monkeypatch.setattr(
            "importlib.metadata.version", lambda name: "2.4.0"
        )

        assert embedding._torch_version_compatible() is True

    def test_newer_version_returns_true(self, monkeypatch):

        monkeypatch.setattr(
            "importlib.metadata.version", lambda name: "2.9.1"
        )

        assert embedding._torch_version_compatible() is True

    def test_older_version_returns_false(self, monkeypatch):

        monkeypatch.setattr(
            "importlib.metadata.version", lambda name: "2.1.2"
        )

        assert embedding._torch_version_compatible() is False

    def test_much_older_major_version_returns_false(self, monkeypatch):

        monkeypatch.setattr(
            "importlib.metadata.version", lambda name: "1.13.0"
        )

        assert embedding._torch_version_compatible() is False

    def test_boundary_version_is_compatible(self, monkeypatch):

        monkeypatch.setattr(
            "importlib.metadata.version", lambda name: "2.4.0"
        )

        assert embedding._torch_version_compatible() is True

    def test_torch_not_installed_does_not_block(self, monkeypatch):
        """Если torch вообще не установлен — не блокируем заранее,
        пусть обычный путь загрузки/самопроверки разбирается сам
        (например, чтобы дать содержательную ошибку "torch не найден",
        а не запутывающую "версия слишком старая")."""

        def _raise(name):
            raise ModuleNotFoundError(name)

        monkeypatch.setattr("importlib.metadata.version", _raise)

        assert embedding._torch_version_compatible() is True

    def test_unparseable_version_does_not_block(self, monkeypatch):

        monkeypatch.setattr(
            "importlib.metadata.version", lambda name: "not-a-version"
        )

        assert embedding._torch_version_compatible() is True

    def test_get_model_skips_load_when_torch_incompatible(self, monkeypatch):
        """get_model() не должен даже пытаться импортировать
        SentenceTransformer, если версия torch заведомо несовместима —
        проверяем это через отдельное сообщение об ошибке."""

        import sys
        import types

        fake_module = types.ModuleType("sentence_transformers")
        fake_module.SentenceTransformer = object  # только чтобы импорт не упал
        monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
        monkeypatch.setattr(embedding, "get_model", _REAL_GET_MODEL)
        monkeypatch.setattr(embedding, "_torch_version_compatible", lambda: False)

        with pytest.raises(RuntimeError, match="torch"):
            embedding.get_model()

        assert embedding._load_failed is True


class TestModelSelection:
    """Задача: выбор модели эмбеддинга (см. EMBEDDING_MODELS в
    app/config.py) — set_model/current_model_key/available_models/
    current_similarity_threshold."""

    def test_available_models_returns_full_registry(self):

        models = embedding.available_models()

        assert set(models) == {
            "e5-large-v2", "e5-base-v2", "bge-base-en-v1.5",
            "all-MiniLM-L6-v2", "e5-small-v2",
        }

    def test_default_model_key_matches_previous_hardcoded_defaults(self):
        """Ничего не переключали — MODEL_NAME/EMBEDDING_DIM/префиксы
        должны совпадать с тем, что было до появления выбора модели
        (e5-large-v2, 1024, 'query: '/'passage: ')."""

        assert embedding.current_model_key() == "e5-large-v2"
        assert embedding.MODEL_NAME == "intfloat/e5-large-v2"
        assert embedding.EMBEDDING_DIM == 1024

    def test_set_model_updates_name_dim_and_prefixes(self):

        embedding.set_model("all-MiniLM-L6-v2")

        assert embedding.current_model_key() == "all-MiniLM-L6-v2"
        assert embedding.MODEL_NAME == "sentence-transformers/all-MiniLM-L6-v2"
        assert embedding.EMBEDDING_DIM == 384
        assert embedding._QUERY_PREFIX == ""
        assert embedding._PASSAGE_PREFIX == ""

    def test_set_model_bge_uses_asymmetric_prefix_scheme(self):
        """bge-base-en-v1.5 — только запрос получает инструктирующий
        префикс, документ — без префикса (в отличие от E5)."""

        embedding.set_model("bge-base-en-v1.5")

        assert embedding._QUERY_PREFIX.startswith("Represent this sentence")
        assert embedding._PASSAGE_PREFIX == ""

    def test_set_model_invalidates_cached_instance(self, monkeypatch):

        monkeypatch.setattr(embedding, "_model", object())
        monkeypatch.setattr(embedding, "_load_failed", True)

        embedding.set_model("e5-base-v2")

        assert embedding._model is None
        assert embedding._load_failed is False

    def test_set_model_none_disables_search(self, monkeypatch):

        monkeypatch.setattr(embedding, "get_model", _REAL_GET_MODEL)

        embedding.set_model(None)

        assert embedding.current_model_key() is None

        with pytest.raises(RuntimeError, match="отключён"):
            embedding.get_model()

    def test_set_model_unknown_key_raises(self):

        with pytest.raises(ValueError):
            embedding.set_model("does-not-exist")

    def test_current_similarity_threshold_matches_registry(self):

        embedding.set_model("bge-base-en-v1.5")
        assert embedding.current_similarity_threshold() == 0.75

        embedding.set_model("e5-large-v2")
        assert embedding.current_similarity_threshold() == 0.84


class TestDevicePreference:
    """Задача: выбор устройства (CPU/GPU) — set_device_preference/
    _pick_device."""

    def test_default_preference_is_auto(self):

        assert embedding.device_preference() == "auto"

    def test_cpu_preference_always_returns_cpu(self):

        embedding.set_device_preference("cpu")
        assert embedding._pick_device() == "cpu"

    def test_cuda_preference_falls_back_to_cpu_without_torch(self):
        """torch не установлен в тестовом окружении — принудительный
        выбор GPU должен молча откатиться на CPU, а не упасть."""

        embedding.set_device_preference("cuda")
        assert embedding._pick_device() == "cpu"

    def test_invalid_preference_raises(self):

        with pytest.raises(ValueError):
            embedding.set_device_preference("tpu")

    def test_set_device_preference_invalidates_cached_instance(self, monkeypatch):

        monkeypatch.setattr(embedding, "_model", object())
        monkeypatch.setattr(embedding, "_load_failed", True)

        embedding.set_device_preference("cpu")

        assert embedding._model is None
        assert embedding._load_failed is False

    def test_gpu_available_is_false_without_torch(self):

        assert embedding.gpu_available() is False
