"""Late materialization preserves filtering, page boundaries and favorite order."""

import pytest

from comfyui_studio.promptvault.core.generation_filter import FilterOptions, GenerationFilterSQL
from comfyui_studio.promptvault.core.generation_sorter import GenerationSorterSQL
from comfyui_studio.promptvault.core.repository import GenerationRepository
from comfyui_studio.promptvault.core.sort_options import SortMode


@pytest.fixture
def library(tmp_path):
    repository = GenerationRepository(tmp_path / "library.sqlite3")
    for index in range(130):
        cursor = repository._conn.execute(
            "INSERT INTO generations(path, mtime, model, timestamp, generation_time, cfg, steps, positive) VALUES (?,?,?,?,?,?,?,?)",
            (str(tmp_path / f"{index}.json"), 1, "modelA" if index % 2 else "modelB", f"{index // 3:04}", index, index % 10, index % 30,
             ("needle" if index % 3 else "other") + " x" * 1000),
        )
        identifier = cursor.lastrowid
        if index % 2:
            repository._conn.execute("INSERT INTO user_data(generation_id, favorite, rating) VALUES (?,?,?)", (identifier, index % 3 == 0, index % 6))
        repository._conn.execute("INSERT INTO loras(generation_id, filename) VALUES (?,?)", (identifier, "example"))
    repository._conn.commit()
    try:
        yield repository, tmp_path
    finally:
        repository.close()


@pytest.mark.parametrize("sort_mode", list(SortMode))
@pytest.mark.parametrize("options", [FilterOptions(), FilterOptions(search="needle"), FilterOptions(model="modelA"), FilterOptions(favorites_only=True), FilterOptions(favorites_only=False), FilterOptions(min_rating=3), FilterOptions(loras=["EXAMPLE"])])
def test_narrow_pages_match_wide_query(library, sort_mode, options):
    repository, folder = library
    extra, params = GenerationFilterSQL.build_where(options)
    for offset in (0, 7, 100, 200):
        expected = repository._conn.execute(
            f"{repository._GENERATION_SELECT} WHERE g.path LIKE ? ESCAPE '\\'{extra} "
            f"ORDER BY {GenerationSorterSQL.build_order_by(sort_mode)} LIMIT ? OFFSET ?",
            [repository._folder_like_pattern(folder), *params, 15, offset],
        ).fetchall()
        actual = repository.load_filtered_page(folder, options, sort_mode, offset, 15)
        assert actual == repository._build_generations(expected)


def test_page_selection_uses_covering_metadata_index(library):
    repository, folder = library
    plans = repository._conn.execute(
        "EXPLAIN QUERY PLAN SELECT g.id FROM generations g LEFT JOIN user_data u ON u.generation_id = g.id "
        "WHERE g.path LIKE ? ESCAPE '\\' ORDER BY COALESCE(u.favorite,0) DESC, g.timestamp DESC LIMIT 10 OFFSET 100",
        [repository._folder_like_pattern(folder)],
    ).fetchall()
    assert any("COVERING INDEX idx_generations_browse" in row[3] for row in plans)
