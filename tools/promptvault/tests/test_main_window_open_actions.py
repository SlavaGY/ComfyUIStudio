"""Тесты для MainWindow._on_open_json_requested /
_on_open_in_folder_requested — обработчики контекстного меню
GenerationList "Open JSON" / "Open in folder" (перенесено из
тулбара, задача: поддержка массового выделения).
"""

import json

import pytest

import app.ui.main_window as main_window_module
from app.ui.main_window import MainWindow


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    """QSettings("PromptVault", "PromptVault") иначе читал/писал бы
    настройки реального пользователя из ~/.config — актуально и для
    этого файла с задачи "сохранение пути к папке между сессиями":
    gallery.load_folder(...) ниже теперь пишет last_folder в
    QSettings, а MainWindow() при создании его читает
    (_restore_last_folder), так что без изоляции один тест этого
    файла мог бы заставить MainWindow В ДРУГОМ тесте (или тестовом
    файле) неожиданно открыть чужую tmp_path-папку при старте.

    См. тот же паттерн (и почему одной подмены HOME недостаточно) в
    tests/test_app_settings.py."""

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


class TestOpenJsonRequested:

    def test_opens_each_selected_generation_json(self, qapp, tmp_path, monkeypatch):

        w = MainWindow()

        folder = tmp_path / "gens"
        folder.mkdir()
        _write_json(folder, "g1")
        _write_json(folder, "g2")

        w.gallery.load_folder(str(folder))
        for _ in range(5):
            qapp.processEvents()

        opened = []
        monkeypatch.setattr(
            main_window_module, "open_file_externally",
            lambda path: opened.append(path) or True
        )

        ids = [g.id for g in w.gallery.generations]
        w._on_open_json_requested(ids)

        assert len(opened) == 2
        assert {p.name for p in opened} == {"g1.json", "g2.json"}

        w.close()

    def test_skips_unknown_ids_without_raising(self, qapp, tmp_path, monkeypatch):

        w = MainWindow()

        opened = []
        monkeypatch.setattr(
            main_window_module, "open_file_externally",
            lambda path: opened.append(path) or True
        )

        w._on_open_json_requested([999999])

        assert opened == []

        w.close()


class TestOpenInFolderRequested:

    def test_calls_reveal_with_all_selected_paths(self, qapp, tmp_path, monkeypatch):

        w = MainWindow()

        folder = tmp_path / "gens"
        folder.mkdir()
        _write_json(folder, "g1")
        _write_json(folder, "g2")

        w.gallery.load_folder(str(folder))
        for _ in range(5):
            qapp.processEvents()

        revealed = []
        monkeypatch.setattr(
            main_window_module, "reveal_in_file_manager",
            lambda paths: revealed.append(list(paths))
        )

        ids = [g.id for g in w.gallery.generations]
        w._on_open_in_folder_requested(ids)

        assert len(revealed) == 1
        assert {p.name for p in revealed[0]} == {"g1.json", "g2.json"}

        w.close()

    def test_no_call_when_nothing_resolves(self, qapp, monkeypatch):

        w = MainWindow()

        revealed = []
        monkeypatch.setattr(
            main_window_module, "reveal_in_file_manager",
            lambda paths: revealed.append(paths)
        )

        w._on_open_in_folder_requested([999999])

        assert revealed == []

        w.close()

    def test_includes_generation_images_alongside_json(self, qapp, tmp_path, monkeypatch):

        w = MainWindow()

        folder = tmp_path / "gens"
        folder.mkdir()
        (folder / "img1.png").write_bytes(b"")
        (folder / "img2.png").write_bytes(b"")
        _write_json(
            folder, "g1",
            images=[{"file": "img1.png", "seed": 1}, {"file": "img2.png", "seed": 2}],
        )

        w.gallery.load_folder(str(folder))
        for _ in range(5):
            qapp.processEvents()

        revealed = []
        monkeypatch.setattr(
            main_window_module, "reveal_in_file_manager",
            lambda paths: revealed.append(list(paths))
        )

        ids = [g.id for g in w.gallery.generations]
        w._on_open_in_folder_requested(ids)

        assert len(revealed) == 1
        assert {p.name for p in revealed[0]} == {"g1.json", "img1.png", "img2.png"}

        w.close()

    def test_no_duplicate_paths_when_images_shared_across_selection(
        self, qapp, tmp_path, monkeypatch
    ):
        """Не обязательный сценарий в реальной жизни (у каждой генерации
        обычно свои файлы), но дедупликация — дешёвая защита от лишних
        повторов в списке на выделение."""

        w = MainWindow()

        folder = tmp_path / "gens"
        folder.mkdir()
        (folder / "shared.png").write_bytes(b"")
        _write_json(
            folder, "g1", images=[{"file": "shared.png", "seed": 1}]
        )
        _write_json(
            folder, "g2", images=[{"file": "shared.png", "seed": 1}]
        )

        w.gallery.load_folder(str(folder))
        for _ in range(5):
            qapp.processEvents()

        revealed = []
        monkeypatch.setattr(
            main_window_module, "reveal_in_file_manager",
            lambda paths: revealed.append(list(paths))
        )

        ids = [g.id for g in w.gallery.generations]
        w._on_open_in_folder_requested(ids)

        paths = revealed[0]
        assert len(paths) == len(set(paths))

        w.close()
