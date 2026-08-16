"""Тесты для app/core/gallery_manager.py — редактирования метаданных,
массовых операций (избранное/рейтинг) и удаления генераций.

Требуют QT_QPA_PLATFORM=offscreen (см. tests/conftest.py).

Запуск: QT_QPA_PLATFORM=offscreen pytest tests/test_gallery_manager.py -v
"""

import json

import pytest

from comfyui_studio.promptvault.core.gallery_manager import GalleryManager
from comfyui_studio.promptvault.core.generation_filter import FilterOptions
from comfyui_studio.promptvault.core.repository import GenerationRepository


def _write_json(folder, name, **overrides):

    data = {
        "timestamp": name,
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
    data.update(overrides)

    path = folder / f"{name}.json"

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    return path


@pytest.fixture
def gallery(qapp, tmp_path, monkeypatch):
    """GalleryManager с изолированной БД и изолированными QSettings
    (иначе тесты читали/писали бы настройки фильтров реального
    пользователя из ~/.config).

    Подмены HOME/XDG_CONFIG_HOME одной по себе недостаточно (Qt
    кеширует уже вычисленный путь к файлу настроек внутри процесса) —
    нужен ещё явный .clear() перед стартом, как в
    tests/test_app_settings.py/tests/test_settings_window.py. Раньше
    этого .clear() здесь не было — тесты в этом файле такое обычно не
    ловили (каждый сам сначала пишет нужный ключ, потом читает), но
    задача "сохранение пути к папке между сессиями" (last_folder,
    см. TestLastFolder ниже) уже проявила пропуск как настоящую
    межтестовую утечку в общем прогоне."""

    from PySide6.QtCore import QSettings

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))

    QSettings("PromptVault", "PromptVault").clear()

    db_path = tmp_path / "test.db"
    repository = GenerationRepository(db_path)
    gm = GalleryManager(repository)

    folder = tmp_path / "gens"
    folder.mkdir()

    yield gm, folder

    gm.close()

    QSettings("PromptVault", "PromptVault").clear()


def _load(gallery_folder_tuple, count=3):

    gm, folder = gallery_folder_tuple

    for i in range(count):
        _write_json(folder, f"ts{i:03d}", generation_time=float(i))

    gm.load_folder(str(folder))

    return gm, folder


class TestUpdateGenerationMetadata:

    def test_successful_edit_updates_in_memory_generation(self, gallery):

        gm, folder = _load(gallery, count=1)
        gen_id = gm.generations[0].id

        ok = gm.update_generation_metadata(gen_id, {"model": "modelB", "cfg": 9.0})

        assert ok is True
        assert gm.generations[0].model == "modelB"
        assert gm.generations[0].cfg == 9.0

    def test_successful_edit_emits_metadata_updated(self, gallery):

        gm, folder = _load(gallery, count=1)
        gen_id = gm.generations[0].id

        captured = []
        gm.metadata_updated.connect(captured.append)

        gm.update_generation_metadata(gen_id, {"model": "modelB"})

        assert len(captured) == 1
        assert captured[0].id == gen_id
        assert captured[0].model == "modelB"

    def test_updates_current_generation_if_it_was_selected(self, gallery):

        gm, folder = _load(gallery, count=1)
        gen_id = gm.generations[0].id
        gm.select_by_index(0)

        gm.update_generation_metadata(gen_id, {"model": "modelB"})

        assert gm.get_current_generation().model == "modelB"

    def test_failed_repository_write_emits_error_and_returns_false(
        self, gallery, monkeypatch
    ):

        gm, folder = _load(gallery, count=1)
        gen_id = gm.generations[0].id
        original_model = gm.generations[0].model

        monkeypatch.setattr(gm._repository, "update_generation", lambda *a, **k: False)

        errors = []
        gm.error_occurred.connect(errors.append)

        ok = gm.update_generation_metadata(gen_id, {"model": "modelB"})

        assert ok is False
        assert len(errors) == 1
        # ничего в памяти не поменялось — сохранение не удалось
        assert gm.generations[0].model == original_model

    def test_unknown_generation_id_returns_false_and_reports_error(self, gallery):

        gm, folder = _load(gallery, count=1)

        errors = []
        gm.error_occurred.connect(errors.append)

        ok = gm.update_generation_metadata(999999, {"model": "modelB"})

        assert ok is False
        assert errors

    def test_edit_that_drops_out_of_filters_emits_hidden_signal(self, gallery):
        """Регрессия: раньше отредактированная генерация, переставшая
        проходить активные фильтры, просто молча пропадала из списка
        без уведомления."""

        gm, folder = _load(gallery, count=1)
        gen_id = gm.generations[0].id

        options = FilterOptions()
        options.model = "modelA"
        gm.set_filter_options(options)

        assert len(gm.filtered_generations) == 1

        hidden = []
        gm.metadata_updated_hidden_by_filters.connect(hidden.append)

        gm.update_generation_metadata(gen_id, {"model": "modelZZZ"})

        assert len(hidden) == 1
        assert hidden[0].id == gen_id
        assert all(g.id != gen_id for g in gm.filtered_generations)

    def test_edit_that_still_matches_filters_does_not_emit_hidden_signal(self, gallery):

        gm, folder = _load(gallery, count=1)
        gen_id = gm.generations[0].id

        options = FilterOptions()
        options.model = "modelA"
        gm.set_filter_options(options)

        hidden = []
        gm.metadata_updated_hidden_by_filters.connect(hidden.append)

        # правим поле, не участвующее в активном фильтре — генерация
        # должна остаться видимой
        gm.update_generation_metadata(gen_id, {"cfg": 11.0})

        assert hidden == []
        assert any(g.id == gen_id for g in gm.filtered_generations)


class TestUpdateGenerationsMetadataBulk:
    """Задача: массовое редактирование метаданных —
    GalleryManager.update_generations_metadata."""

    def test_successful_bulk_edit_updates_in_memory_generations(self, gallery):

        gm, folder = _load(gallery, count=3)
        ids = [g.id for g in gm.generations]

        ok = gm.update_generations_metadata(ids, {"model": "modelZ"})

        assert ok is True
        assert all(g.model == "modelZ" for g in gm.generations)

    def test_successful_bulk_edit_emits_bulk_metadata_updated(self, gallery):

        gm, folder = _load(gallery, count=3)
        ids = [g.id for g in gm.generations]

        captured = []
        gm.bulk_metadata_updated.connect(captured.append)

        gm.update_generations_metadata(ids, {"model": "modelZ"})

        assert len(captured) == 1
        assert set(captured[0]) == set(ids)

    def test_partial_failure_still_applies_successful_ones(self, gallery):

        gm, folder = _load(gallery, count=2)
        ids = [g.id for g in gm.generations]

        errors = []
        gm.error_occurred.connect(errors.append)

        ok = gm.update_generations_metadata(ids + [999999], {"model": "modelZ"})

        assert ok is False
        assert errors
        assert all(g.model == "modelZ" for g in gm.generations)

    def test_updates_current_generation_if_it_was_among_edited(self, gallery):

        gm, folder = _load(gallery, count=1)
        gen_id = gm.generations[0].id
        gm.select_by_index(0)

        gm.update_generations_metadata([gen_id], {"model": "modelZ"})

        assert gm.get_current_generation().model == "modelZ"


class TestGetMetadataHistory:
    """Задача: история изменений метаданных —
    GalleryManager.get_metadata_history передаёт вызов в репозиторий."""

    def test_returns_empty_list_for_unedited_generation(self, gallery):

        gm, folder = _load(gallery, count=1)
        gen_id = gm.generations[0].id

        assert gm.get_metadata_history(gen_id) == []

    def test_returns_history_after_edit(self, gallery):

        gm, folder = _load(gallery, count=1)
        gen_id = gm.generations[0].id

        gm.update_generation_metadata(gen_id, {"model": "modelB"})

        history = gm.get_metadata_history(gen_id)

        assert len(history) == 1
        assert history[0]["field"] == "model"
        assert history[0]["old_value"] == "modelA"
        assert history[0]["new_value"] == "modelB"


class TestDeleteGenerations:

    def test_deletes_requested_ids_and_returns_count(self, gallery):

        gm, folder = _load(gallery, count=3)
        ids = [g.id for g in gm.generations]

        deleted = gm.delete_generations(ids[:2], delete_files=False)

        assert deleted == 2
        assert len(gm.generations) == 1
        assert gm.generations[0].id == ids[2]

    def test_delete_files_false_keeps_json_on_disk(self, gallery):

        gm, folder = _load(gallery, count=1)
        gen_id = gm.generations[0].id
        json_path = gm.generations[0].path

        gm.delete_generations([gen_id], delete_files=False)

        assert json_path.exists()

    def test_delete_clears_current_generation_if_it_was_deleted(self, gallery):
        """После удаления выбранной генерации apply_filters (вызванный
        изнутри delete_generations) откатывает выбор на первую
        оставшуюся генерацию — если оставшихся нет, current_generation
        становится None."""

        gm, folder = _load(gallery, count=1)
        gen_id = gm.generations[0].id
        gm.select_by_index(0)

        gm.delete_generations([gen_id], delete_files=False)

        assert gm.get_current_generation() is None

    def test_delete_falls_back_to_next_available_generation(self, gallery):

        gm, folder = _load(gallery, count=2)
        gen_id = gm.generations[0].id
        remaining_id = gm.generations[1].id
        gm.select_by_index(0)

        gm.delete_generations([gen_id], delete_files=False)

        current = gm.get_current_generation()
        assert current is not None
        assert current.id == remaining_id

    def test_delete_keeps_current_generation_if_a_different_one_was_deleted(self, gallery):

        gm, folder = _load(gallery, count=2)
        kept_id = gm.generations[0].id
        other_id = gm.generations[1].id
        gm.select_by_index(0)

        gm.delete_generations([other_id], delete_files=False)

        current = gm.get_current_generation()
        assert current is not None
        assert current.id == kept_id

    def test_partial_failure_reports_error_but_deletes_the_rest(self, gallery, monkeypatch):

        gm, folder = _load(gallery, count=2)
        ids = [g.id for g in gm.generations]

        real_delete = gm._repository.delete_generation

        def flaky_delete(generation_id, delete_files=False):
            if generation_id == ids[0]:
                raise OSError("simulated failure")
            return real_delete(generation_id, delete_files=delete_files)

        monkeypatch.setattr(gm._repository, "delete_generation", flaky_delete)

        errors = []
        gm.error_occurred.connect(errors.append)

        deleted = gm.delete_generations(ids, delete_files=False)

        assert deleted == 1
        assert len(errors) == 1
        assert len(gm.generations) == 1
        assert gm.generations[0].id == ids[0]

    def test_deleting_empty_list_is_a_no_op(self, gallery):

        gm, folder = _load(gallery, count=2)

        deleted = gm.delete_generations([], delete_files=False)

        assert deleted == 0
        assert len(gm.generations) == 2


class TestMassFavoriteAndRating:

    def test_set_multiple_favorite_updates_all_targeted_generations(self, gallery):

        gm, folder = _load(gallery, count=3)
        ids = [g.id for g in gm.generations]

        gm.set_multiple_favorite(ids[:2], True)

        favorites = {g.id: g.favorite for g in gm.generations}
        assert favorites[ids[0]] is True
        assert favorites[ids[1]] is True
        assert favorites[ids[2]] is False

    def test_set_multiple_favorite_persists_to_repository(self, gallery):

        gm, folder = _load(gallery, count=1)
        gen_id = gm.generations[0].id

        gm.set_multiple_favorite([gen_id], True)

        refreshed = gm._repository.get_generation(gen_id)
        assert refreshed.favorite is True

    def test_set_multiple_rating_updates_all_targeted_generations(self, gallery):

        gm, folder = _load(gallery, count=3)
        ids = [g.id for g in gm.generations]

        gm.set_multiple_rating(ids, 4)

        assert all(g.rating == 4 for g in gm.generations)

    def test_set_multiple_rating_ignores_unknown_ids(self, gallery):

        gm, folder = _load(gallery, count=1)
        gen_id = gm.generations[0].id

        # не должно падать на несуществующем id, соседний по списку id
        # должен всё равно обновиться
        gm.set_multiple_rating([gen_id, 999999], 5)

        assert gm.generations[0].rating == 5

    def test_mass_operations_schedule_a_debounced_refresh(self, gallery):
        """Массовые операции не должны пересобирать
        filtered_generations синхронно (иначе клик по большому
        множеству выделенных карточек лагал бы) — вместо этого
        запускается debounce-таймер."""

        gm, folder = _load(gallery, count=2)
        ids = [g.id for g in gm.generations]

        gm.set_multiple_favorite(ids, True)

        assert gm._refresh_timer.isActive()


class TestCustomTags:
    """Задача: пользовательские теги — GalleryManager.set_custom_tags/
    add_tags_to_generations/available_custom_tags."""

    def test_set_custom_tags_updates_in_memory_and_db(self, gallery):

        gm, folder = _load(gallery, count=1)
        gen_id = gm.generations[0].id

        gm.set_custom_tags(gen_id, ["cat", "outdoors"])

        assert gm.generations[0].custom_tags == ["cat", "outdoors"]
        assert gm._repository.get_custom_tags(gen_id) == ["cat", "outdoors"]

    def test_set_custom_tags_is_a_full_replace(self, gallery):

        gm, folder = _load(gallery, count=1)
        gen_id = gm.generations[0].id

        gm.set_custom_tags(gen_id, ["cat"])
        gm.set_custom_tags(gen_id, ["dog"])

        assert gm.generations[0].custom_tags == ["dog"]

    def test_add_tags_to_generations_unions_with_existing(self, gallery):

        gm, folder = _load(gallery, count=2)
        ids = [g.id for g in gm.generations]

        gm.set_custom_tags(ids[0], ["cat"])

        gm.add_tags_to_generations(ids, ["outdoors"])

        by_id = {g.id: g.custom_tags for g in gm.generations}
        assert sorted(by_id[ids[0]]) == ["cat", "outdoors"]
        assert by_id[ids[1]] == ["outdoors"]

    def test_available_custom_tags_reflects_current_folder(self, gallery):

        gm, folder = _load(gallery, count=2)
        ids = [g.id for g in gm.generations]

        gm.set_custom_tags(ids[0], ["cat"])
        gm.set_custom_tags(ids[1], ["dog"])

        assert gm.available_custom_tags() == {"cat", "dog"}

    def test_set_custom_tags_schedules_a_debounced_refresh(self, gallery):

        gm, folder = _load(gallery, count=1)
        gen_id = gm.generations[0].id

        gm.set_custom_tags(gen_id, ["cat"])

        assert gm._refresh_timer.isActive()


class TestEmbeddingModelAndDeviceSettings:
    """Задача: выбор модели эмбеддинга и устройства — настройки
    сохраняются в QSettings и переживают пересоздание GalleryManager."""

    def test_default_embedding_model_is_e5_large(self, gallery):

        gm, _ = gallery
        assert gm.embedding_model_key() == "e5-large-v2"

    def test_set_embedding_model_persists_across_instances(self, gallery, tmp_path):

        gm, folder = gallery

        gm.set_embedding_model("all-MiniLM-L6-v2")
        assert gm.embedding_model_key() == "all-MiniLM-L6-v2"

        repository2 = GenerationRepository(tmp_path / "test2.db")
        gm2 = GalleryManager(repository2)
        try:
            assert gm2.embedding_model_key() == "all-MiniLM-L6-v2"
        finally:
            gm2.close()

    def test_set_embedding_model_none_persists_as_disabled(self, gallery, tmp_path):

        gm, folder = gallery

        gm.set_embedding_model(None)
        assert gm.embedding_model_key() is None

        repository2 = GenerationRepository(tmp_path / "test2.db")
        gm2 = GalleryManager(repository2)
        try:
            assert gm2.embedding_model_key() is None
        finally:
            gm2.close()

    def test_available_embedding_models_matches_registry(self, gallery):

        gm, _ = gallery
        assert "e5-large-v2" in gm.available_embedding_models()

    def test_default_device_preference_is_auto(self, gallery):

        gm, _ = gallery
        assert gm.device_preference() == "auto"

    def test_set_device_preference_persists(self, gallery, tmp_path):

        gm, folder = gallery

        gm.set_device_preference("cpu")
        assert gm.device_preference() == "cpu"

        repository2 = GenerationRepository(tmp_path / "test2.db")
        gm2 = GalleryManager(repository2)
        try:
            assert gm2.device_preference() == "cpu"
        finally:
            gm2.close()

    def test_recompute_all_embeddings_delegates_to_repository(self, gallery, monkeypatch):

        gm, folder = _load(gallery, count=2)

        calls = {}

        def _fake_recompute(batch_size=200):
            calls["called"] = True
            return 2

        monkeypatch.setattr(gm._repository, "recompute_all_embeddings", _fake_recompute)

        total = gm.recompute_all_embeddings()

        assert total == 2
        assert calls.get("called") is True


class TestFilterStateNotPersisted:
    """Фильтры и поиск (обычный и семантический) больше НЕ переживают
    перезапуск приложения — новая сессия всегда начинается с пустых
    FilterOptions, даже если QSettings того же пользователя/приложения
    хранит что-то с прошлого раза. Сортировка (set_sort_mode) этим не
    затрагивается и продолжает сохраняться как раньше."""

    def test_new_instance_ignores_filters_set_by_previous_one(self, gallery, tmp_path):

        gm, folder = gallery

        options = FilterOptions()
        options.search = "cat"
        options.model = "modelA"
        options.favorites_only = True
        gm.set_filter_options(options)

        assert gm.filter_options().search == "cat"

        # "перезапуск приложения": новый GalleryManager, те же QSettings
        # (тот же HOME/XDG_CONFIG_HOME из фикстуры gallery), другая БД
        repository2 = GenerationRepository(tmp_path / "test2.db")
        gm2 = GalleryManager(repository2)

        try:
            fresh = gm2.filter_options()
            assert fresh.search == ""
            assert fresh.model is None
            assert fresh.favorites_only is None
        finally:
            gm2.close()

    def test_new_instance_ignores_semantic_query_set_by_previous_one(self, gallery, tmp_path):

        gm, folder = gallery

        gm.set_semantic_search("a cat sitting on a chair")
        assert gm.filter_options().semantic_query == "a cat sitting on a chair"

        repository2 = GenerationRepository(tmp_path / "test2.db")
        gm2 = GalleryManager(repository2)

        try:
            assert gm2.filter_options().semantic_query == ""
        finally:
            gm2.close()


class TestLastFolder:
    """Задача: сохранение пути к папке просмотра между сессиями."""

    def test_no_folder_opened_yet_returns_none(self, gallery):

        gm, _folder = gallery

        assert gm.last_folder() is None

    def test_load_folder_persists_it(self, gallery):

        gm, folder = _load(gallery)

        assert gm.last_folder() == str(folder)

    def test_new_instance_sees_folder_persisted_by_previous_one(self, gallery, tmp_path):
        """То же самое "перезапуск приложения", что и для фильтров/
        семантического запроса выше — новый GalleryManager (те же
        QSettings из фикстуры gallery, другая БД) должен увидеть
        путь, сохранённый предыдущим экземпляром."""

        gm, folder = _load(gallery)

        repository2 = GenerationRepository(tmp_path / "test2.db")
        gm2 = GalleryManager(repository2)

        try:
            assert gm2.last_folder() == str(folder)
        finally:
            gm2.close()

    def test_opening_a_second_folder_overwrites_the_first(self, gallery, tmp_path):

        gm, folder = _load(gallery)

        other = tmp_path / "other_gens"
        other.mkdir()
        _write_json(other, "ts000")
        gm.load_folder(str(other))

        assert gm.last_folder() == str(other)
