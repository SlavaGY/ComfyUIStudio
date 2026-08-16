"""Тесты для новых возможностей app/core/repository.py:

- SQL-фильтрация по пути (load_generations/load_generations_page/
  count_generations) вместо построчного разбора в Python (задача 3.3);
- SQL-версии available_models/available_samplers/available_loras;
- get_statistics() (задача 3.2);
- export_generations_zip / import_user_data / add_generation_file
  (задача 3.4).

Запуск: pytest tests/test_repository_scaling.py -v
"""

import json
import zipfile

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

    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    return path


def _write_png(path):

    from PySide6.QtGui import QImage

    path.parent.mkdir(parents=True, exist_ok=True)

    image = QImage(4, 4, QImage.Format_RGB32)
    image.fill(0xFF00FF00)
    image.save(str(path), "PNG")

    return path


@pytest.fixture
def repo(tmp_path):

    db_path = tmp_path / "test.db"
    repository = GenerationRepository(db_path)

    yield repository, tmp_path

    repository.close()


# ======================================================
# SQL-фильтрация по пути
# ======================================================


class TestSqlPathFiltering:

    def test_only_returns_generations_inside_folder(self, repo):

        repository, root = repo

        _write_json(root / "folderA" / "gen1.json", timestamp="a")
        _write_json(root / "folderB" / "gen2.json", timestamp="b")

        repository.sync_folder(root)

        result = repository.load_generations(root / "folderA")

        assert len(result) == 1
        assert result[0].timestamp == "a"

    def test_includes_nested_subfolders(self, repo):

        repository, root = repo

        _write_json(root / "folderA" / "sub" / "gen1.json", timestamp="a")

        repository.sync_folder(root)

        result = repository.load_generations(root / "folderA")

        assert len(result) == 1

    def test_does_not_match_sibling_folder_with_common_prefix(self, repo):
        """folderA и folderA_extra не должны пересекаться при LIKE-фильтрации
        (регрессия на неправильное экранирование/построение паттерна)."""

        repository, root = repo

        _write_json(root / "folderA" / "gen1.json", timestamp="a")
        _write_json(root / "folderA_extra" / "gen2.json", timestamp="b")

        repository.sync_folder(root)

        result = repository.load_generations(root / "folderA")

        assert len(result) == 1
        assert result[0].timestamp == "a"

    def test_handles_percent_and_underscore_in_path(self, repo):
        """% и _ — спецсимволы LIKE, должны быть корректно экранированы."""

        repository, root = repo

        folder = root / "50%_off"
        _write_json(folder / "gen1.json", timestamp="a")

        repository.sync_folder(root)

        result = repository.load_generations(folder)

        assert len(result) == 1

    def test_count_generations_matches_load_generations(self, repo):

        repository, root = repo

        for i in range(3):
            _write_json(root / "folderA" / f"gen{i}.json", timestamp=f"t{i}", generation_time=float(i))

        repository.sync_folder(root)

        assert repository.count_generations(root / "folderA") == 3

    def test_load_generations_page_paginates_correctly(self, repo):

        repository, root = repo

        # timestamp по возрастанию -> сортировка DESC даёт t4, t3, ..., t0
        for i in range(5):
            _write_json(root / f"gen{i}.json", timestamp=f"t{i}", generation_time=float(i))

        repository.sync_folder(root)

        page1 = repository.load_generations_page(root, offset=0, limit=2)
        page2 = repository.load_generations_page(root, offset=2, limit=2)
        page3 = repository.load_generations_page(root, offset=4, limit=2)

        assert [g.timestamp for g in page1] == ["t4", "t3"]
        assert [g.timestamp for g in page2] == ["t2", "t1"]
        assert [g.timestamp for g in page3] == ["t0"]

    def test_pages_cover_all_generations_without_overlap_or_gaps(self, repo):

        repository, root = repo

        for i in range(7):
            _write_json(root / f"gen{i}.json", timestamp=f"t{i}", generation_time=float(i))

        repository.sync_folder(root)

        seen_ids = set()

        for offset in range(0, 7, 3):
            for g in repository.load_generations_page(root, offset=offset, limit=3):
                assert g.id not in seen_ids
                seen_ids.add(g.id)

        assert len(seen_ids) == 7


# ======================================================
# available_models / available_samplers / available_loras (SQL)
# ======================================================


class TestAvailableValuesSql:

    def test_available_models_scoped_to_folder(self, repo):

        repository, root = repo

        _write_json(root / "folderA" / "g1.json", model_name="modelA")
        _write_json(root / "folderB" / "g2.json", model_name="modelB", timestamp="ts2")

        repository.sync_folder(root)

        assert repository.available_models(root / "folderA") == {"modelA"}

    def test_available_samplers_scoped_to_folder(self, repo):

        repository, root = repo

        _write_json(root / "g1.json", sampler_name="Euler")

        repository.sync_folder(root)

        assert repository.available_samplers(root) == {"Euler"}

    def test_available_loras_scoped_to_folder(self, repo):

        repository, root = repo

        _write_json(
            root / "g1.json",
            loras=[{"filename": "loraA.safetensors", "strength": 1.0}],
        )

        repository.sync_folder(root)

        assert repository.available_loras(root) == {"loraA.safetensors"}


# ======================================================
# get_statistics
# ======================================================


class TestGetStatistics:

    def test_empty_db(self, repo):

        repository, _root = repo

        stats = repository.get_statistics()

        assert stats.total_generations == 0
        assert stats.total_favorites == 0
        assert stats.average_rating == 0.0
        assert stats.top_models == []

    def test_totals_and_average_rating(self, repo):

        repository, root = repo

        _write_json(root / "g1.json", timestamp="t1")
        _write_json(root / "g2.json", timestamp="t2")

        repository.sync_folder(root)

        gens = repository.load_generations(root)
        ids = {g.timestamp: g.id for g in gens}

        repository.set_favorite(ids["t1"], True)
        repository.set_rating(ids["t1"], 4)
        repository.set_rating(ids["t2"], 2)

        stats = repository.get_statistics()

        assert stats.total_generations == 2
        assert stats.total_favorites == 1
        assert stats.average_rating == 3.0

    def test_top_models_ordered_by_count(self, repo):

        repository, root = repo

        _write_json(root / "g1.json", timestamp="t1", model_name="popular")
        _write_json(root / "g2.json", timestamp="t2", model_name="popular")
        _write_json(root / "g3.json", timestamp="t3", model_name="rare")

        repository.sync_folder(root)

        stats = repository.get_statistics()

        assert stats.top_models[0] == ("popular", 2)
        assert ("rare", 1) in stats.top_models

    def test_top_loras_counts_usages_across_generations(self, repo):

        repository, root = repo

        _write_json(
            root / "g1.json", timestamp="t1",
            loras=[{"filename": "shared.safetensors", "strength": 1.0}],
        )
        _write_json(
            root / "g2.json", timestamp="t2",
            loras=[{"filename": "shared.safetensors", "strength": 1.0}],
        )

        repository.sync_folder(root)

        stats = repository.get_statistics()

        assert stats.top_loras[0] == ("shared.safetensors", 2)

    def test_cfg_histogram_buckets_sum_to_total(self, repo):

        repository, root = repo

        for i, cfg in enumerate([1.0, 3.0, 5.0, 7.0, 9.0]):
            _write_json(root / f"g{i}.json", timestamp=f"t{i}", generation_time=float(i), cfg=cfg)

        repository.sync_folder(root)

        stats = repository.get_statistics()

        assert sum(b.count for b in stats.cfg_histogram) == 5
        assert len(stats.cfg_histogram) == 10

    def test_histogram_single_distinct_value_collapses_to_one_bucket(self, repo):

        repository, root = repo

        for i in range(3):
            _write_json(root / f"g{i}.json", timestamp=f"t{i}", generation_time=float(i), cfg=7.0)

        repository.sync_folder(root)

        stats = repository.get_statistics()

        assert len(stats.cfg_histogram) == 1
        assert stats.cfg_histogram[0].count == 3

    def test_rating_distribution(self, repo):

        repository, root = repo

        _write_json(root / "g1.json", timestamp="t1")
        _write_json(root / "g2.json", timestamp="t2")

        repository.sync_folder(root)

        gens = repository.load_generations(root)
        ids = {g.timestamp: g.id for g in gens}

        repository.set_rating(ids["t1"], 5)
        repository.set_rating(ids["t2"], 5)

        stats = repository.get_statistics()

        assert stats.rating_distribution == [(5, 2)]


# ======================================================
# export_generations_zip
# ======================================================


class TestExportGenerationsZip:

    def test_exports_json_and_image(self, repo, tmp_path, qapp):

        repository, root = repo

        json_path = _write_json(root / "g1.json", timestamp="t1")
        _write_png(root / "img1.png")

        repository.sync_folder(root)
        [gen] = repository.load_generations(root)

        zip_path = tmp_path / "out.zip"
        count = repository.export_generations_zip([gen.id], zip_path, include_previews=False)

        assert count == 1
        assert zip_path.exists()

        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
            assert f"{gen.id}/{json_path.name}" in names
            assert f"{gen.id}/img1.png" in names

    def test_includes_preview_when_requested(self, repo, tmp_path, qapp):

        repository, root = repo

        _write_json(root / "g1.json", timestamp="t1")
        _write_png(root / "img1.png")

        repository.sync_folder(root)
        [gen] = repository.load_generations(root)

        zip_path = tmp_path / "out.zip"
        repository.export_generations_zip([gen.id], zip_path, include_previews=True)

        with zipfile.ZipFile(zip_path) as archive:
            preview_names = [n for n in archive.namelist() if n.startswith("previews/")]
            assert len(preview_names) == 1

    def test_skips_missing_generation_without_raising(self, repo, tmp_path):

        repository, root = repo

        zip_path = tmp_path / "out.zip"
        count = repository.export_generations_zip([999], zip_path)

        assert count == 0

    def test_partial_export_returns_actual_count(self, repo, tmp_path):

        repository, root = repo

        _write_json(root / "g1.json", timestamp="t1")

        repository.sync_folder(root)
        [gen] = repository.load_generations(root)

        zip_path = tmp_path / "out.zip"
        count = repository.export_generations_zip([gen.id, 999], zip_path)

        assert count == 1


# ======================================================
# import_user_data
# ======================================================


class TestImportUserData:

    def test_imports_favorite_and_rating_by_identity(self, repo, tmp_path):

        repository, root = repo

        _write_json(root / "g1.json", timestamp="shared_ts", generation_time=1.0)
        repository.sync_folder(root)

        # "другая машина": отдельная БД с той же identity, но своим id
        other_db_path = tmp_path / "other.db"
        other_repo = GenerationRepository(other_db_path)
        _write_json(root / "g1.json", timestamp="shared_ts", generation_time=1.0)
        other_repo.sync_folder(root)
        [other_gen] = other_repo.load_generations(root)
        other_repo.set_favorite(other_gen.id, True)
        other_repo.set_rating(other_gen.id, 5)
        other_repo.close()

        updated, unmatched = repository.import_user_data(other_db_path)

        assert updated == 1
        assert unmatched == 0

        [local_gen] = repository.load_generations(root)
        assert local_gen.favorite is True
        assert local_gen.rating == 5

    def test_does_not_downgrade_existing_higher_rating(self, repo, tmp_path):

        repository, root = repo

        _write_json(root / "g1.json", timestamp="shared_ts", generation_time=1.0)
        repository.sync_folder(root)
        [local_gen] = repository.load_generations(root)
        repository.set_rating(local_gen.id, 5)

        other_db_path = tmp_path / "other.db"
        other_repo = GenerationRepository(other_db_path)
        other_repo.sync_folder(root)
        [other_gen] = other_repo.load_generations(root)
        other_repo.set_rating(other_gen.id, 2)
        other_repo.close()

        repository.import_user_data(other_db_path)

        [local_gen] = repository.load_generations(root)
        assert local_gen.rating == 5

    def test_counts_unmatched_records(self, repo, tmp_path):

        repository, root = repo

        # локальная БД пустая — ничего не синхронизировано

        other_db_path = tmp_path / "other.db"
        other_repo = GenerationRepository(other_db_path)
        _write_json(root / "g1.json", timestamp="only_in_other", generation_time=1.0)
        other_repo.sync_folder(root)
        [other_gen] = other_repo.load_generations(root)
        other_repo.set_favorite(other_gen.id, True)
        other_repo.close()

        updated, unmatched = repository.import_user_data(other_db_path)

        assert updated == 0
        assert unmatched == 1

    def test_missing_db_file_returns_zero(self, repo, tmp_path):

        repository, _root = repo

        updated, unmatched = repository.import_user_data(tmp_path / "does_not_exist.db")

        assert (updated, unmatched) == (0, 0)


# ======================================================
# add_generation_file (drag & drop)
# ======================================================


class TestAddGenerationFile:

    def test_adds_single_json_file(self, repo):

        repository, root = repo

        json_path = _write_json(root / "dropped.json", timestamp="dropped_ts")

        gen_id = repository.add_generation_file(json_path)

        assert gen_id is not None

        gen = repository.get_generation(gen_id)
        assert gen.timestamp == "dropped_ts"

    def test_updates_existing_generation_on_second_drop(self, repo):

        repository, root = repo

        json_path = _write_json(root / "dropped.json", timestamp="ts1", cfg=5.0)
        gen_id = repository.add_generation_file(json_path)

        _write_json(json_path, timestamp="ts1", cfg=9.0)
        gen_id_2 = repository.add_generation_file(json_path)

        assert gen_id == gen_id_2

        gen = repository.get_generation(gen_id)
        assert gen.cfg == 9.0

    def test_invalid_json_returns_none(self, repo):

        repository, root = repo

        bad_path = root / "bad.json"
        bad_path.write_text("not valid json", encoding="utf-8")

        assert repository.add_generation_file(bad_path) is None
