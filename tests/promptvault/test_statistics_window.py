"""Тесты для app/ui/statistics_window.py и кнопки статистики в Toolbar.

StatisticsWindow строится поверх GalleryManager и содержит две вкладки:
"Current view" (текущая папка + активные фильтры,
GalleryManager.get_statistics) и "Whole library" (вся когда-либо
просканированная в БД библиотека, GalleryManager.get_library_statistics).

Требуют QT_QPA_PLATFORM=offscreen (см. tests/conftest.py).
"""

import json

import pytest

from comfyui_studio.promptvault.core.gallery_manager import GalleryManager
from comfyui_studio.promptvault.core.repository import GenerationRepository
from comfyui_studio.promptvault.ui.statistics_window import StatisticsWindow
from comfyui_studio.promptvault.ui.toolbar import Toolbar


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

    repo = GenerationRepository(tmp_path / "test.db")
    gm = GalleryManager(repo)

    folder = tmp_path / "gens"
    folder.mkdir()

    yield gm, folder

    gm.close()


class TestStatisticsWindow:

    def test_builds_with_no_data(self, qapp, gallery):

        gm, _folder = gallery

        window = StatisticsWindow(gm)

        assert window.stats.total_generations == 0

    def test_builds_with_data(self, qapp, gallery):

        gm, folder = gallery

        _write_json(folder, "g1")
        _write_json(folder, "g2", model_name="modelB", timestamp="g2")

        gm.load_folder(str(folder))

        window = StatisticsWindow(gm)

        assert window.stats.total_generations == 2

    def test_refresh_picks_up_new_data(self, qapp, gallery):

        gm, folder = gallery

        window = StatisticsWindow(gm)
        assert window.stats.total_generations == 0

        _write_json(folder, "g1")
        gm.load_folder(str(folder))

        window.refresh()

        assert window.stats.total_generations == 1

    def test_current_view_excludes_other_folders(self, qapp, gallery):
        """Другая папка, ранее просканированная в ту же БД, не должна
        попадать в статистику вкладки "Current view" — только то, что
        сейчас открыто."""

        gm, folder = gallery

        other_folder = folder.parent / "other_gens"
        other_folder.mkdir()
        _write_json(other_folder, "o1")
        _write_json(other_folder, "o2", timestamp="o2")
        gm._repository.sync_folder(other_folder)

        _write_json(folder, "g1")
        gm.load_folder(str(folder))

        window = StatisticsWindow(gm)

        assert window.stats.total_generations == 1

    def test_reflects_active_filters(self, qapp, gallery):

        gm, folder = gallery

        _write_json(folder, "g1", model_name="modelA")
        _write_json(folder, "g2", model_name="modelB", timestamp="g2")
        gm.load_folder(str(folder))

        options = gm.filter_options()
        options.model = "modelA"
        gm.set_filter_options(options)

        window = StatisticsWindow(gm)

        assert window.stats.total_generations == 1
        assert window.stats.top_models == [("modelA", 1)]

    def test_library_tab_includes_other_folders_and_ignores_filters(self, qapp, gallery):
        """В отличие от вкладки "Current view", "Whole library" должна
        учитывать все папки, когда-либо просканированные в БД, и не
        зависеть от активных фильтров."""

        gm, folder = gallery

        other_folder = folder.parent / "other_gens"
        other_folder.mkdir()
        _write_json(other_folder, "o1")
        gm._repository.sync_folder(other_folder)

        _write_json(folder, "g1", model_name="modelA")
        _write_json(folder, "g2", model_name="modelB", timestamp="g2")
        gm.load_folder(str(folder))

        options = gm.filter_options()
        options.model = "modelA"
        gm.set_filter_options(options)

        window = StatisticsWindow(gm)

        # текущий вид отфильтрован до одной генерации из открытой папки
        assert window.stats.total_generations == 1
        # вся библиотека — обе генерации открытой папки + генерация
        # из другой папки, фильтр по модели не применяется
        assert window.library_stats.total_generations == 3

    def test_refresh_updates_both_tabs(self, qapp, gallery):

        gm, folder = gallery

        window = StatisticsWindow(gm)
        assert window.stats.total_generations == 0
        assert window.library_stats.total_generations == 0

        _write_json(folder, "g1")
        gm.load_folder(str(folder))

        window.refresh()

        assert window.stats.total_generations == 1
        assert window.library_stats.total_generations == 1


class TestToolbarStatisticsButton:

    def test_emits_statistics_requested(self, qapp):

        toolbar = Toolbar()

        received = []
        toolbar.statisticsRequested.connect(lambda: received.append(True))

        toolbar.stats_btn.click()

        assert received == [True]
