"""Тесты для сквозной проводки виртуальной пагинации между
GenerationList и GalleryManager через MainWindow (задача: настоящая
виртуальная пагинация):

GenerationList.moreNeeded -> GalleryManager.load_more_filtered
GalleryManager.more_generations_loaded -> GenerationList.append_generations
"""

import json

import pytest

from app.ui.main_window import MainWindow


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    """См. tests/test_app_settings.py и тот же комментарий в
    tests/test_main_window_open_actions.py — нужно с задачи
    "сохранение пути к папке между сессиями": load_folder() пишет
    last_folder в QSettings, MainWindow() при создании читает его
    обратно (_restore_last_folder)."""

    from PySide6.QtCore import QSettings

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))

    QSettings("PromptVault", "PromptVault").clear()

    yield

    QSettings("PromptVault", "PromptVault").clear()


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


class TestVirtualPaginationWiring:

    def test_more_needed_from_list_loads_more_via_gallery(self, qapp, tmp_path):
        """Не проверяем, сколько именно уже подгружено сразу после
        load_folder() — это зависит от реального размера окна/вьюпорта
        (если видимая область и так вмещает все "заглушки", часть
        страниц может подгрузиться сама по себе, см.
        GenerationList.update_visible_cards). Важно, что сигнал
        moreNeeded реально доходит до GalleryManager.load_more_filtered,
        а его результат — обратно в GenerationList.append_generations,
        и в итоге оба конца сходятся на полном списке."""

        w = MainWindow()

        # изменение размера страницы применяется только к СЛЕДУЮЩЕЙ
        # загрузке папки — вызывать до load_folder (см.
        # GalleryManager.set_generations_page_size)
        w.gallery.set_generations_page_size(2)

        folder = tmp_path / "gens"
        folder.mkdir()

        for i in range(5):
            _write_json(folder, f"g{i}", timestamp=f"g{i}", generation_time=float(i))

        w.gallery.load_folder(str(folder))
        qapp.processEvents()

        try:
            assert w.gallery.filtered_total() == 5
            assert w.generation_list.total_count() == 5

            # догружаем всё, что осталось, эмулируя прокрутку списка к
            # уже показанному концу
            for _ in range(10):

                if len(w.gallery.generations) >= w.gallery.filtered_total():
                    break

                w.generation_list.moreNeeded.emit()
                qapp.processEvents()

            assert len(w.gallery.generations) == 5
            assert len(w.generation_list.generations) == 5
            assert {g.id for g in w.generation_list.generations} == {
                g.id for g in w.gallery.filtered_generations
            }
        finally:
            w.close()

    def test_no_more_needed_once_fully_loaded(self, qapp, tmp_path):

        w = MainWindow()
        w.gallery.set_generations_page_size(500)

        folder = tmp_path / "gens"
        folder.mkdir()

        for i in range(3):
            _write_json(folder, f"g{i}", timestamp=f"g{i}", generation_time=float(i))

        w.gallery.load_folder(str(folder))
        qapp.processEvents()

        try:
            assert w.gallery.filtered_total() == 3
            assert len(w.gallery.generations) == 3

            # уже всё загружено одной страницей — подгружать нечего
            assert w.gallery.load_more_filtered() is False
        finally:
            w.close()
