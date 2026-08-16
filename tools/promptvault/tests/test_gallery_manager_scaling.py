"""Тесты для новых возможностей app/core/gallery_manager.py:

- ленивая постраничная загрузка папки (задача 3.3);
- кэширование available_models/available_samplers/available_loras;
- обёртки над export_generations_zip/import_user_data/add_dropped_files
  (задача 3.4) и get_statistics (задача 3.2).

Требуют QT_QPA_PLATFORM=offscreen (см. tests/conftest.py).
"""

import json
import zipfile

import pytest

import app.core.gallery_manager as gallery_manager_module
from app.core.gallery_manager import GalleryManager
from app.core.repository import GenerationRepository


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

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))

    db_path = tmp_path / "test.db"
    repository = GenerationRepository(db_path)
    gm = GalleryManager(repository)

    folder = tmp_path / "gens"
    folder.mkdir()

    yield gm, folder

    gm.close()


class TestLazyPagination:
    """Виртуальная пагинация отфильтрованного/отсортированного
    результата (задача: настоящая виртуальная пагинация) — только
    первая страница грузится сразу при открытии папки, остальные — по
    явному запросу через load_more_filtered() (в реальном приложении
    вызывается UI при прокрутке списка к уже показанному концу, см.
    GenerationList.moreNeeded), а не автоматически заранее в фоне для
    всей папки целиком."""

    def test_first_page_available_synchronously(self, gallery, qapp, monkeypatch):

        monkeypatch.setattr(gallery_manager_module, "GENERATIONS_PAGE_SIZE", 2)

        gm, folder = gallery

        for i in range(5):
            _write_json(folder, f"g{i}", generation_time=float(i))

        gm.load_folder(str(folder))

        # первая страница уже должна быть в generations сразу после
        # load_folder(), без ожидания обработки очереди событий Qt
        assert len(gm.generations) == 2
        assert gm.filtered_total() == 5

    def test_remaining_pages_do_not_load_automatically(self, gallery, qapp, monkeypatch):
        """В отличие от старого поведения (фоновая подгрузка всей
        папки через QTimer), теперь ничего, кроме первой страницы, не
        должно оказаться в памяти, пока это явно не запрошено —
        независимо от того, сколько обработано событий Qt."""

        monkeypatch.setattr(gallery_manager_module, "GENERATIONS_PAGE_SIZE", 2)

        gm, folder = gallery

        for i in range(5):
            _write_json(folder, f"g{i}", generation_time=float(i))

        gm.load_folder(str(folder))

        for _ in range(10):
            qapp.processEvents()

        assert len(gm.generations) == 2
        assert gm.filtered_total() == 5

    def test_load_more_filtered_appends_next_page(self, gallery, qapp, monkeypatch):

        monkeypatch.setattr(gallery_manager_module, "GENERATIONS_PAGE_SIZE", 2)

        gm, folder = gallery

        for i in range(5):
            _write_json(folder, f"g{i}", generation_time=float(i))

        gm.load_folder(str(folder))

        assert gm.load_more_filtered() is True
        assert len(gm.generations) == 4

        assert gm.load_more_filtered() is True
        assert len(gm.generations) == 5
        assert len({g.id for g in gm.generations}) == 5

        # больше страниц нет
        assert gm.load_more_filtered() is False
        assert len(gm.generations) == 5

    def test_load_more_filtered_emits_only_new_page(self, gallery, monkeypatch):

        monkeypatch.setattr(gallery_manager_module, "GENERATIONS_PAGE_SIZE", 2)

        gm, folder = gallery

        for i in range(3):
            _write_json(folder, f"g{i}", generation_time=float(i))

        gm.load_folder(str(folder))

        received = []
        gm.more_generations_loaded.connect(lambda batch: received.append(batch))

        gm.load_more_filtered()

        assert len(received) == 1
        assert len(received[0]) == 1  # только новая страница, не весь список

    def test_load_more_filtered_returns_false_without_open_folder(self, gallery):

        gm, _folder = gallery

        assert gm.load_more_filtered() is False

    def test_switching_folder_resets_pagination_state(self, gallery, qapp, monkeypatch):

        monkeypatch.setattr(gallery_manager_module, "GENERATIONS_PAGE_SIZE", 1)

        gm, folder = gallery

        folder_a = folder / "a"
        folder_a.mkdir()
        folder_b = folder / "b"
        folder_b.mkdir()

        for i in range(4):
            _write_json(folder_a, f"a{i}", generation_time=float(i))

        _write_json(folder_b, "b0", generation_time=100.0)

        gm.load_folder(str(folder_a))
        assert gm.filtered_total() == 4

        gm.load_folder(str(folder_b))

        # переключение папки должно полностью заменить и загруженную
        # страницу, и общий счётчик — ничего от folder_a остаться не должно
        assert [g.timestamp for g in gm.generations] == ["b0"]
        assert gm.filtered_total() == 1
        assert gm.load_more_filtered() is False




class TestAvailableValuesCache:

    def test_reflects_synced_folder(self, gallery):

        gm, folder = gallery

        _write_json(folder, "g1", model_name="modelA", sampler_name="Euler")

        gm.load_folder(str(folder))

        assert gm.available_models() == {"modelA"}
        assert gm.available_samplers() == {"Euler"}

    def test_updates_after_metadata_edit(self, gallery):

        gm, folder = gallery

        _write_json(folder, "g1", model_name="oldModel")

        gm.load_folder(str(folder))
        assert gm.available_models() == {"oldModel"}

        gen_id = gm.generations[0].id
        gm.update_generation_metadata(gen_id, {"model": "newModel"})

        assert gm.available_models() == {"newModel"}

    def test_updates_after_deletion(self, gallery):

        gm, folder = gallery

        _write_json(folder, "g1", model_name="onlyModel")

        gm.load_folder(str(folder))
        gen_id = gm.generations[0].id

        gm.delete_generations([gen_id], delete_files=False)

        assert gm.available_models() == set()


class TestExportImportWrappers:

    def test_export_generations_zip(self, gallery, tmp_path):

        gm, folder = gallery

        _write_json(folder, "g1")
        gm.load_folder(str(folder))

        zip_path = tmp_path / "out.zip"
        count = gm.export_generations_zip([gm.generations[0].id], str(zip_path))

        assert count == 1
        assert zipfile.is_zipfile(zip_path)

    def test_import_user_data_refreshes_in_memory_state(self, gallery, tmp_path):

        gm, folder = gallery

        _write_json(folder, "g1", timestamp="shared", generation_time=1.0)
        gm.load_folder(str(folder))

        other_db_path = tmp_path / "other.db"
        other_repo = GenerationRepository(other_db_path)
        other_repo.sync_folder(folder)
        [other_gen] = other_repo.load_generations(folder)
        other_repo.set_favorite(other_gen.id, True)
        other_repo.close()

        updated, unmatched = gm.import_user_data(str(other_db_path))

        assert updated == 1
        assert unmatched == 0
        assert gm.generations[0].favorite is True

    def test_add_dropped_files(self, gallery):

        gm, folder = gallery

        _write_json(folder, "g1")
        gm.load_folder(str(folder))

        dropped_path = folder / "dropped.json"
        _write_json(folder, "dropped", timestamp="dropped_ts")

        added = gm.add_dropped_files([str(dropped_path)])

        assert added == 1
        assert any(g.timestamp == "dropped_ts" for g in gm.generations)

    def test_get_statistics(self, gallery):

        gm, folder = gallery

        _write_json(folder, "g1")
        gm.load_folder(str(folder))

        stats = gm.get_statistics()

        assert stats.total_generations == 1
