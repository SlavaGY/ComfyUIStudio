"""Тесты для comfyui_studio/promptvault/core/embedding_worker.py.

Логика выбора устройства (_pick_device) и загрузки/самопроверки модели
(_load_model) переехала сюда из embedding.py при переносе загрузки
torch/sentence-transformers в отдельный подпроцесс (см. дорожную карту
рефакторинга, запись от 2026-08-20 — "утечка ~475 МБ на import
sentence_transformers, не освобождаемая в рамках одного процесса").
Эти тесты раньше были в test_embedding.py как TestPickDevice/
TestLoadAndVerifyModelOfflineFirst и проверяли embedding._pick_device()/
embedding._load_and_verify_model() — обеих функций в embedding.py
больше нет, их обязанности взяли на себя embedding_worker._pick_device()/
embedding_worker._load_model() (с немного другой сигнатурой — явные
параметры вместо чтения module-level состояния embedding.py, т.к.
воркер — отдельный процесс и этого состояния не видит).

Как и раньше, сама модель не грузится по-настоящему — SentenceTransformer
подменяется через monkeypatch на детерминированный фейк.

Запуск: pytest tests/test_embedding_worker.py -v
"""

from __future__ import annotations

import os
import types

import numpy as np
import pytest

from comfyui_studio.promptvault.core import embedding_worker


class TestPickDevice:
    """_pick_device(preference, torch_module) -> (device, fell_back_to_cpu)
    — портированная копия того, что раньше было embedding._pick_device()
    (там читала module-level _device_preference и делала свой
    `import torch`; здесь оба явные параметры — вызывающая сторона,
    embedding_worker._load_model, сама делает `import torch` один раз
    и передаёт модуль сюда)."""

    def test_cpu_preference_always_returns_cpu(self):

        fake_torch = types.SimpleNamespace(
            cuda=types.SimpleNamespace(is_available=lambda: True)
        )

        device, fell_back = embedding_worker._pick_device("cpu", fake_torch)

        assert device == "cpu"
        assert fell_back is False

    def test_returns_cuda_when_available(self):

        fake_torch = types.SimpleNamespace(
            cuda=types.SimpleNamespace(is_available=lambda: True)
        )

        device, fell_back = embedding_worker._pick_device("auto", fake_torch)

        assert device == "cuda"
        assert fell_back is False

    def test_returns_cpu_when_cuda_unavailable(self):

        fake_torch = types.SimpleNamespace(
            cuda=types.SimpleNamespace(is_available=lambda: False)
        )

        device, fell_back = embedding_worker._pick_device("auto", fake_torch)

        assert device == "cpu"
        assert fell_back is False

    def test_cuda_preference_falls_back_to_cpu_without_cuda(self):
        """Принудительный выбор "cuda" без реальной CUDA должен молча
        откатиться на CPU (fell_back_to_cpu=True — так вызывающая
        сторона, embedding.get_model(), узнаёт, что нужно залогировать
        предупреждение пользователю, см. её комментарии)."""

        fake_torch = types.SimpleNamespace(
            cuda=types.SimpleNamespace(is_available=lambda: False)
        )

        device, fell_back = embedding_worker._pick_device("cuda", fake_torch)

        assert device == "cpu"
        assert fell_back is True

    def test_cuda_available_does_not_report_fallback(self):

        fake_torch = types.SimpleNamespace(
            cuda=types.SimpleNamespace(is_available=lambda: True)
        )

        device, fell_back = embedding_worker._pick_device("cuda", fake_torch)

        assert device == "cuda"
        assert fell_back is False

    def test_cuda_is_available_raising_falls_back_to_cpu(self):
        """torch.cuda.is_available() сам иногда бросает исключение
        (повреждённая CUDA-установка и т.п.) — не должно ронять выбор
        устройства, только откатывать на CPU."""

        def _raise():
            raise RuntimeError("сломанная CUDA-установка")

        fake_torch = types.SimpleNamespace(
            cuda=types.SimpleNamespace(is_available=_raise)
        )

        device, fell_back = embedding_worker._pick_device("auto", fake_torch)

        assert device == "cpu"


class TestLoadModelOfflineFirst:
    """_load_model() пытается загрузить модель офлайн (из локального
    кэша HF Hub, без сети) первым делом, и только при неудаче
    откатывается на обычную загрузку с сетью — та же логика, что раньше
    была в embedding._load_and_verify_model(), один в один, просто
    переехавшая в подпроцесс."""

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

        model, device, fell_back = embedding_worker._load_model(
            "some/model", "cpu", "", 256,
        )

        assert model is not None
        assert device == "cpu"
        assert fell_back is False
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

        embedding_worker._load_model("some/model", "cpu", "", 256)

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

        model, device, fell_back = embedding_worker._load_model(
            "some/model", "cpu", "", 256,
        )

        assert model is not None
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
            embedding_worker._load_model("some/model", "cpu", "", 256)

    def test_sets_max_seq_length(self, monkeypatch):

        class _FakeModel:
            def encode(self, *a, **kw):
                return np.ones(4, dtype=np.float32)

        self._patch_sentence_transformers(monkeypatch, lambda name, device: _FakeModel())

        model, _device, _fell_back = embedding_worker._load_model(
            "some/model", "cpu", "", 512,
        )

        assert model.max_seq_length == 512
