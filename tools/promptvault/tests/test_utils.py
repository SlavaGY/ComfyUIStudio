"""Тесты для app/utils.py — group_paths_by_folder/reveal_in_file_manager
(контекстное меню "Open in folder", с поддержкой массового выделения
и нескольких папок сразу) и open_file_externally.

Требуют QT_QPA_PLATFORM=offscreen (см. tests/conftest.py) — используют
QDesktopServices, которому нужен QApplication.
"""

import app.utils as utils_module
from app.utils import group_paths_by_folder, open_file_externally, reveal_in_file_manager


class TestGroupPathsByFolder:

    def test_groups_by_parent_folder(self, tmp_path):

        a = tmp_path / "folderA" / "g1.json"
        b = tmp_path / "folderA" / "g2.json"
        c = tmp_path / "folderB" / "g3.json"

        groups = group_paths_by_folder([a, b, c])

        assert groups == {
            tmp_path / "folderA": [a, b],
            tmp_path / "folderB": [c],
        }

    def test_empty_input_returns_empty_dict(self):

        assert group_paths_by_folder([]) == {}

    def test_preserves_order_within_group(self, tmp_path):

        paths = [tmp_path / f"g{i}.json" for i in range(5)]

        groups = group_paths_by_folder(paths)

        assert groups[tmp_path] == paths


class TestOpenFileExternally:

    def test_missing_file_returns_false(self, qapp, tmp_path):

        assert open_file_externally(tmp_path / "does_not_exist.json") is False

    def test_existing_file_attempts_to_open(self, qapp, tmp_path, monkeypatch):

        path = tmp_path / "gen.json"
        path.write_text("{}")

        monkeypatch.setattr(utils_module.QDesktopServices, "openUrl", lambda url: True)

        assert open_file_externally(path) is True


class TestRevealInFileManager:
    """Диспетчеризация по платформе — сами платформенные реализации
    (_reveal_windows/_reveal_macos) требуют реальной ОС и здесь не
    вызываются напрямую, только через монки-патч."""

    def test_nonexistent_paths_are_skipped(self, qapp, tmp_path, monkeypatch):

        called = []
        monkeypatch.setattr(
            utils_module, "_reveal_single_folder",
            lambda folder, files: called.append((folder, files))
        )

        reveal_in_file_manager([tmp_path / "missing.json"])

        assert called == []

    def test_groups_by_folder_before_revealing(self, qapp, tmp_path, monkeypatch):

        folder_a = tmp_path / "a"
        folder_a.mkdir()
        folder_b = tmp_path / "b"
        folder_b.mkdir()

        file_a1 = folder_a / "g1.json"
        file_a1.write_text("{}")
        file_a2 = folder_a / "g2.json"
        file_a2.write_text("{}")
        file_b1 = folder_b / "g3.json"
        file_b1.write_text("{}")

        calls = []
        monkeypatch.setattr(
            utils_module, "_reveal_single_folder",
            lambda folder, files: calls.append((folder, list(files)))
        )

        reveal_in_file_manager([file_a1, file_b1, file_a2])

        assert len(calls) == 2  # одно окно на папку, а не на файл
        calls_by_folder = dict(calls)
        assert calls_by_folder[folder_a] == [file_a1, file_a2]
        assert calls_by_folder[folder_b] == [file_b1]

    def test_falls_back_to_opening_folder_when_platform_reveal_fails(
        self, qapp, tmp_path, monkeypatch
    ):

        file_path = tmp_path / "g1.json"
        file_path.write_text("{}")

        monkeypatch.setattr(utils_module.sys, "platform", "linux")

        opened_urls = []
        monkeypatch.setattr(
            utils_module.QDesktopServices, "openUrl",
            lambda url: opened_urls.append(url.toLocalFile()) or True
        )

        reveal_in_file_manager([file_path])

        assert opened_urls == [str(tmp_path)]

    def test_uses_windows_reveal_when_platform_is_windows(self, qapp, tmp_path, monkeypatch):

        file_path = tmp_path / "g1.json"
        file_path.write_text("{}")

        monkeypatch.setattr(utils_module.sys, "platform", "win32")

        windows_calls = []
        monkeypatch.setattr(
            utils_module, "_reveal_windows",
            lambda files: windows_calls.append(list(files)) or True
        )

        opened_urls = []
        monkeypatch.setattr(
            utils_module.QDesktopServices, "openUrl",
            lambda url: opened_urls.append(url.toLocalFile()) or True
        )

        reveal_in_file_manager([file_path])

        assert windows_calls == [[file_path]]
        assert opened_urls == []  # запасной вариант не понадобился

    def test_falls_back_when_windows_reveal_raises_nothing_but_returns_false(
        self, qapp, tmp_path, monkeypatch
    ):

        file_path = tmp_path / "g1.json"
        file_path.write_text("{}")

        monkeypatch.setattr(utils_module.sys, "platform", "win32")
        monkeypatch.setattr(utils_module, "_reveal_windows", lambda files: False)

        opened_urls = []
        monkeypatch.setattr(
            utils_module.QDesktopServices, "openUrl",
            lambda url: opened_urls.append(url.toLocalFile()) or True
        )

        reveal_in_file_manager([file_path])

        assert opened_urls == [str(tmp_path)]
