"""Тесты для восстановления последней открытой папки между сессиями
(задача: сохранение пути к папке просмотра между сессиями) —
GalleryManager.last_folder (см. tests/test_gallery_manager.py) плюс
MainWindow._restore_last_folder/_open_folder_path.
"""

import json
import shutil

import pytest

from comfyui_studio.promptvault.ui.main_window import MainWindow


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    """См. tests/test_app_settings.py. Здесь изоляция особенно
    важна — сам смысл теста в том, что ВТОРОЙ экземпляр MainWindow
    (та же фикстура tmp_path/тот же HOME в пределах одного теста)
    видит папку, открытую первым, так что тесты внутри этого файла
    сами полагаются на общее QSettings-хранилище — но оно не должно
    быть общим МЕЖДУ тестами/файлами."""

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


class TestRestoreLastFolder:

    def test_fresh_start_opens_nothing(self, qapp):
        """Ни одна папка ещё не открывалась в этом (изолированном)
        QSettings-хранилище — MainWindow не должен пытаться что-то
        восстановить."""

        w = MainWindow()

        try:
            assert w.gallery.current_folder is None
        finally:
            w.close()

    def test_second_session_reopens_the_same_folder(self, qapp, tmp_path):

        folder = tmp_path / "gens"
        folder.mkdir()
        _write_json(folder, "ts000")

        w1 = MainWindow()

        try:
            w1._open_folder_path(str(folder))
            assert w1.gallery.current_folder == str(folder)
        finally:
            w1.close()

        # "перезапуск приложения" — новый MainWindow, те же QSettings
        # (см. фикстуру _isolated_settings выше)
        w2 = MainWindow()

        try:
            assert w2.gallery.current_folder == str(folder)
            assert len(w2.gallery.filtered_generations) == 1
        finally:
            w2.close()

    def test_missing_folder_is_skipped_silently(self, qapp, tmp_path):
        """Папка была открыта, потом исчезла с диска (переименовали,
        отключили внешний накопитель) — следующий старт не должен ни
        падать, ни показывать диалог об ошибке, просто не открывает
        ничего."""

        folder = tmp_path / "gens"
        folder.mkdir()
        _write_json(folder, "ts000")

        w1 = MainWindow()
        try:
            w1._open_folder_path(str(folder))
        finally:
            w1.close()

        shutil.rmtree(folder)

        w2 = MainWindow()

        try:
            assert w2.gallery.current_folder is None
        finally:
            w2.close()

    def test_open_folder_dialog_persists_for_next_session(self, qapp, tmp_path, monkeypatch):
        """open_folder() (обычный путь через диалог выбора папки)
        тоже должен сохранять путь — не только программное открытие
        через _open_folder_path напрямую."""

        folder = tmp_path / "gens"
        folder.mkdir()
        _write_json(folder, "ts000")

        w1 = MainWindow()

        try:
            monkeypatch.setattr(
                "comfyui_studio.promptvault.ui.main_window.QFileDialog.getExistingDirectory",
                lambda *a, **k: str(folder),
            )
            w1.open_folder()
            assert w1.gallery.current_folder == str(folder)
        finally:
            w1.close()

        w2 = MainWindow()

        try:
            assert w2.gallery.current_folder == str(folder)
        finally:
            w2.close()
