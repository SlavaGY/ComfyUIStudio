"""Тест для MainWindow._extract_json_paths_from_mime_data (задача 3.4:
drag & drop JSON-файлов в главное окно).
"""

from PySide6.QtCore import QMimeData, QUrl

from app.ui.main_window import MainWindow


def _mime_with_urls(local_paths, non_file_urls=None):

    mime = QMimeData()

    urls = [QUrl.fromLocalFile(p) for p in local_paths]

    if non_file_urls:
        urls.extend(QUrl(u) for u in non_file_urls)

    mime.setUrls(urls)

    return mime


class TestExtractJsonPathsFromMimeData:

    def test_extracts_local_json_files(self, qapp, tmp_path):

        json_path = tmp_path / "gen.json"
        json_path.write_text("{}")

        mime = _mime_with_urls([str(json_path)])

        result = MainWindow._extract_json_paths_from_mime_data(mime)

        assert result == [str(json_path)]

    def test_ignores_non_json_files(self, qapp, tmp_path):

        png_path = tmp_path / "image.png"
        png_path.write_text("")

        mime = _mime_with_urls([str(png_path)])

        assert MainWindow._extract_json_paths_from_mime_data(mime) == []

    def test_ignores_remote_urls(self, qapp):

        mime = _mime_with_urls([], non_file_urls=["https://example.com/gen.json"])

        assert MainWindow._extract_json_paths_from_mime_data(mime) == []

    def test_filters_mixed_drop_to_json_only(self, qapp, tmp_path):

        json_path = tmp_path / "gen.json"
        json_path.write_text("{}")
        png_path = tmp_path / "image.png"
        png_path.write_text("")

        mime = _mime_with_urls([str(json_path), str(png_path)])

        assert MainWindow._extract_json_paths_from_mime_data(mime) == [str(json_path)]

    def test_no_urls_returns_empty(self, qapp):

        mime = QMimeData()

        assert MainWindow._extract_json_paths_from_mime_data(mime) == []
