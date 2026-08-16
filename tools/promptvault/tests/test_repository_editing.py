"""Тесты для app/core/repository.py — редактирования метаданных
(update_generation) и удаления (delete_generation).

Запуск: pytest tests/test_repository_editing.py -v
"""

import json
import os

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
        "loras": [{"filename": "loraX.safetensors", "strength": 0.8}],
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


class TestUpdateGeneration:

    def test_updates_db_fields(self, repo):

        repository, folder = repo
        json_path = _write_json(folder / "gen1.json")

        gen_id = _sync_and_get_id(repository, folder)

        ok = repository.update_generation(gen_id, {"cfg": 9.5, "steps": 30})
        assert ok is True

        refreshed = repository.get_generation(gen_id)
        assert refreshed.cfg == 9.5
        assert refreshed.steps == 30

    def test_writes_back_to_json_file_with_correct_field_names(self, repo):
        """update_dict использует имена атрибутов Generation, а не
        сырые ключи JSON — проверяем, что маппинг (_JSON_FIELD_MAP)
        действительно применяется при записи на диск."""

        repository, folder = repo
        json_path = _write_json(folder / "gen1.json")

        gen_id = _sync_and_get_id(repository, folder)

        repository.update_generation(gen_id, {
            "model": "modelB",
            "positive": "a dog",
        })

        with open(json_path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        assert raw["model_name"] == "modelB"
        assert raw["positive_text"] == "a dog"

    def test_does_not_touch_identity_fields(self, repo):
        """timestamp/generation_time не редактируются через этот метод,
        даже если бы их случайно передали в update_dict — это ключ
        идентичности записи в БД."""

        repository, folder = repo
        json_path = _write_json(folder / "gen1.json")

        gen_id = _sync_and_get_id(repository, folder)

        repository.update_generation(gen_id, {"cfg": 1.0})

        refreshed = repository.get_generation(gen_id)
        assert refreshed.timestamp == "ts1"
        assert refreshed.generation_time == 1.0

    def test_unknown_generation_id_returns_false(self, repo):

        repository, folder = repo
        _write_json(folder / "gen1.json")
        _sync_and_get_id(repository, folder)

        assert repository.update_generation(9999, {"cfg": 1.0}) is False

    def test_missing_file_on_disk_returns_false(self, repo):

        repository, folder = repo
        json_path = _write_json(folder / "gen1.json")

        gen_id = _sync_and_get_id(repository, folder)

        json_path.unlink()

        assert repository.update_generation(gen_id, {"cfg": 1.0}) is False

    def test_external_modification_between_read_and_write_is_detected(
        self, repo, monkeypatch
    ):
        """Регрессия: если файл на диске меняется снаружи (другим
        процессом/автосинхронизацией) между чтением и записью внутри
        update_generation, сохранение должно отмениться, а не молча
        затереть внешние изменения тем, что было прочитано раньше."""

        repository, folder = repo
        json_path = _write_json(folder / "gen1.json")

        gen_id = _sync_and_get_id(repository, folder)

        from app.core import repository as repository_module

        real_stat = os.stat
        call_count = {"n": 0}

        def flaky_stat(path, *args, **kwargs):

            result = real_stat(path, *args, **kwargs)

            if str(path) == str(json_path):
                call_count["n"] += 1

                # первый stat() — "до чтения" (mtime_before), второй —
                # "перед записью" (mtime_check). Эмулируем внешнее
                # изменение файла ровно между ними.
                if call_count["n"] == 2:
                    return os.stat_result((
                        result.st_mode, result.st_ino, result.st_dev,
                        result.st_nlink, result.st_uid, result.st_gid,
                        result.st_size, result.st_atime,
                        result.st_mtime + 5.0,  # "снаружи" файл стал новее
                        result.st_ctime,
                    ))

            return result

        monkeypatch.setattr(repository_module.os, "stat", flaky_stat)

        ok = repository.update_generation(gen_id, {"cfg": 42.0})

        assert ok is False

        # содержимое файла не тронуто нашей (устаревшей) записью
        with open(json_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        assert raw["cfg"] == 7.0

    def test_write_uses_atomic_replace_not_in_place_write(self, repo):
        """Проверяем сам механизм: временный файл создаётся и
        переименовывается поверх исходного (os.replace), а не
        открывается на запись напрямую — так частичная/прерванная
        запись не может оставить исходный JSON повреждённым."""

        repository, folder = repo
        json_path = _write_json(folder / "gen1.json")

        gen_id = _sync_and_get_id(repository, folder)

        repository.update_generation(gen_id, {"cfg": 3.0})

        tmp_path = json_path.with_suffix(json_path.suffix + ".tmp")
        assert not tmp_path.exists(), "временный файл должен быть переименован, не оставлен"
        assert json_path.exists()

        with open(json_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        assert raw["cfg"] == 3.0

    def test_failed_write_cleans_up_tmp_file(self, repo, monkeypatch):

        repository, folder = repo
        json_path = _write_json(folder / "gen1.json")

        gen_id = _sync_and_get_id(repository, folder)

        from app.core import repository as repository_module

        def failing_replace(src, dst):
            raise OSError("simulated disk error")

        monkeypatch.setattr(repository_module.os, "replace", failing_replace)

        ok = repository.update_generation(gen_id, {"cfg": 3.0})
        assert ok is False

        tmp_path = json_path.with_suffix(json_path.suffix + ".tmp")
        assert not tmp_path.exists()


class TestMetadataHistory:
    """Задача: история изменений метаданных — update_generation теперь
    записывает предыдущее значение каждого реально изменившегося поля
    в metadata_history, доступную через get_metadata_history."""

    def test_recorded_for_each_changed_field(self, repo):

        repository, folder = repo
        _write_json(folder / "gen1.json")
        gen_id = _sync_and_get_id(repository, folder)

        repository.update_generation(gen_id, {"model": "modelB", "cfg": 9.5})

        history = repository.get_metadata_history(gen_id)
        fields = {h["field"] for h in history}

        assert fields == {"model", "cfg"}

    def test_records_old_and_new_values(self, repo):

        repository, folder = repo
        _write_json(folder / "gen1.json")
        gen_id = _sync_and_get_id(repository, folder)

        repository.update_generation(gen_id, {"positive": "a dog"})

        history = repository.get_metadata_history(gen_id)
        entry = next(h for h in history if h["field"] == "positive")

        assert entry["old_value"] == "a cat"
        assert entry["new_value"] == "a dog"

    def test_unchanged_field_is_not_recorded(self, repo):
        """Если update_dict передаёт то же самое значение, что уже
        было (пользователь открыл редактор и сохранил без изменений),
        история не должна засоряться пустой записью "было X, стало X".
        """

        repository, folder = repo
        _write_json(folder / "gen1.json")
        gen_id = _sync_and_get_id(repository, folder)

        repository.update_generation(gen_id, {"model": "modelA", "cfg": 9.5})

        history = repository.get_metadata_history(gen_id)
        fields = {h["field"] for h in history}

        assert fields == {"cfg"}

    def test_multiple_edits_accumulate_history_newest_first(self, repo):

        repository, folder = repo
        _write_json(folder / "gen1.json")
        gen_id = _sync_and_get_id(repository, folder)

        repository.update_generation(gen_id, {"cfg": 8.0})
        repository.update_generation(gen_id, {"cfg": 9.0})

        history = [h for h in repository.get_metadata_history(gen_id) if h["field"] == "cfg"]

        assert len(history) == 2
        assert history[0]["old_value"] == "8.0"
        assert history[0]["new_value"] == "9.0"
        assert history[1]["old_value"] == "7.0"
        assert history[1]["new_value"] == "8.0"

    def test_no_history_for_unknown_generation(self, repo):

        repository, folder = repo
        _write_json(folder / "gen1.json")
        _sync_and_get_id(repository, folder)

        assert repository.get_metadata_history(999999) == []

    def test_history_deleted_when_generation_deleted(self, repo):

        repository, folder = repo
        _write_json(folder / "gen1.json")
        gen_id = _sync_and_get_id(repository, folder)

        repository.update_generation(gen_id, {"cfg": 8.0})
        repository.delete_generation(gen_id, delete_files=False)

        raw_conn = repository._conn
        count = raw_conn.execute(
            "SELECT COUNT(*) FROM metadata_history WHERE generation_id=?", (gen_id,)
        ).fetchone()[0]

        assert count == 0

    def test_failed_update_does_not_record_history(self, repo):

        repository, folder = repo
        json_path = _write_json(folder / "gen1.json")

        gen_id = _sync_and_get_id(repository, folder)

        json_path.unlink()

        repository.update_generation(gen_id, {"cfg": 8.0})

        assert repository.get_metadata_history(gen_id) == []


class TestUpdateGenerationsBulk:
    """Задача: массовое редактирование метаданных —
    GenerationRepository.update_generations применяет один update_dict
    к нескольким генерациям, каждой из которых на диске соответствует
    свой собственный JSON-файл."""

    def test_applies_update_to_all_generations(self, repo):

        repository, folder = repo
        _write_json(folder / "gen1.json", timestamp="ts1")
        _write_json(folder / "gen2.json", timestamp="ts2")

        repository.sync_folder(folder)
        ids = [g.id for g in repository.load_generations(folder)]
        assert len(ids) == 2

        failed = repository.update_generations(ids, {"model": "modelZ"})

        assert failed == []
        for gid in ids:
            assert repository.get_generation(gid).model == "modelZ"

    def test_returns_failed_ids_without_aborting_the_rest(self, repo):

        repository, folder = repo
        _write_json(folder / "gen1.json", timestamp="ts1")
        _write_json(folder / "gen2.json", timestamp="ts2")

        repository.sync_folder(folder)
        ids = [g.id for g in repository.load_generations(folder)]

        failed = repository.update_generations(ids + [999999], {"model": "modelZ"})

        assert failed == [999999]
        for gid in ids:
            assert repository.get_generation(gid).model == "modelZ"

    def test_each_generation_gets_its_own_history_entry(self, repo):

        repository, folder = repo
        _write_json(folder / "gen1.json", timestamp="ts1")
        _write_json(folder / "gen2.json", timestamp="ts2")

        repository.sync_folder(folder)
        ids = [g.id for g in repository.load_generations(folder)]

        repository.update_generations(ids, {"model": "modelZ"})

        for gid in ids:
            history = repository.get_metadata_history(gid)
            assert any(h["field"] == "model" and h["new_value"] == "modelZ" for h in history)


class TestDeleteGeneration:

    def test_removes_record_from_db(self, repo):

        repository, folder = repo
        _write_json(folder / "gen1.json")
        gen_id = _sync_and_get_id(repository, folder)

        assert repository.delete_generation(gen_id, delete_files=False) is True
        assert repository.get_generation(gen_id) is None

    def test_delete_files_false_keeps_files_on_disk(self, repo):

        repository, folder = repo
        json_path = _write_json(folder / "gen1.json")
        gen_id = _sync_and_get_id(repository, folder)

        repository.delete_generation(gen_id, delete_files=False)

        assert json_path.exists()

    def test_delete_files_true_removes_json_and_images(self, repo):

        repository, folder = repo
        json_path = _write_json(folder / "gen1.json")
        image_path = folder / "img1.png"
        image_path.write_bytes(b"fake png")

        gen_id = _sync_and_get_id(repository, folder)

        repository.delete_generation(gen_id, delete_files=True)

        assert not json_path.exists()
        assert not image_path.exists()

    def test_unknown_id_returns_false(self, repo):

        repository, folder = repo
        _write_json(folder / "gen1.json")
        _sync_and_get_id(repository, folder)

        assert repository.delete_generation(9999, delete_files=False) is False

    def test_delete_cascades_loras_and_images(self, repo):

        repository, folder = repo
        _write_json(folder / "gen1.json")
        gen_id = _sync_and_get_id(repository, folder)

        repository.delete_generation(gen_id, delete_files=False)

        # напрямую убеждаемся, что дочерние строки правда исчезли, а
        # не просто перестали быть видны через JOIN в load_generations
        raw_conn = repository._conn
        loras = raw_conn.execute("SELECT COUNT(*) FROM loras WHERE generation_id=?", (gen_id,)).fetchone()[0]
        images = raw_conn.execute("SELECT COUNT(*) FROM images WHERE generation_id=?", (gen_id,)).fetchone()[0]

        assert loras == 0
        assert images == 0
