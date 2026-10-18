"""Публичные методы и Qt-сигналы GalleryManager, используемые UI.

Приватные методы, состав миксинов и определяющий класс модуль не фиксируются.
Путь импорта GalleryManager остаётся точкой доступа для потребителей.
"""

import pytest
from PySide6.QtCore import Signal

from comfyui_studio.promptvault.core.gallery_manager import GalleryManager

# Методы существующих потребителей; добавление новых методов допустимо.
PUBLIC_METHODS = [
    "load_folder", "resync",
    "available_models", "available_samplers", "available_loras",
    "available_custom_tags",
    "set_search", "set_filter_options", "filter_options",
    "set_sort_mode", "sort_mode",
    "generations_page_size", "set_generations_page_size", "last_folder",
    "apply_filters", "filtered_total", "load_more_filtered",
    "select_by_index", "select_generation", "get_current_generation",
    "current_index",
    "toggle_favorite", "set_rating",
    "set_custom_tags", "add_tags_to_generations",
    "set_multiple_favorite", "set_multiple_rating",
    "delete_generations", "export_generations", "export_generations_zip",
    "import_user_data", "add_dropped_files",
    "get_statistics", "get_library_statistics",
    "update_generation_metadata", "update_generations_metadata",
    "get_metadata_history",
    "close",
]

SIGNALS = [
    "generations_changed", "more_generations_loaded", "selection_changed",
    "filter_changed", "metadata_updated", "metadata_updated_hidden_by_filters",
    "bulk_metadata_updated", "error_occurred",
]


@pytest.mark.parametrize("name", PUBLIC_METHODS, ids=PUBLIC_METHODS)
def test_public_method_exists(name):
    assert callable(getattr(GalleryManager, name))


@pytest.mark.parametrize("name", SIGNALS, ids=SIGNALS)
def test_signal_exists(name):
    assert isinstance(getattr(GalleryManager, name), Signal)
