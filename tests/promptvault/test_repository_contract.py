"""Контракт GenerationRepository (этап 6.1: разрез repository.py).

Репозиторий разрезан на несколько модулей (запросы, редактирование,
экспорт/импорт, статистика), но снаружи это по-прежнему ОДИН класс с
прежними именами и сигнатурами -- на него опираются GalleryManager,
folder_sync, main_window и десятки тестов. Здесь это зафиксировано:

1. снимок публичной (и приватной, которой пользуются тесты) поверхности --
   имена, вид (метод / staticmethod / classmethod) и сигнатуры;
2. сквозной сценарий через все группы методов: sync -> чтение ->
   редактирование и история -> массовое обновление -> теги/избранное ->
   экспорт/импорт -> статистика -> удаление.

Если разрез (или любой будущий рефакторинг) что-то сломает или потеряет,
упадёт именно этот файл, а не «где-то в галерее».
"""

import inspect
import json
import zipfile

import pytest

from comfyui_studio.promptvault.core.generation_filter import FilterOptions
from comfyui_studio.promptvault.core.repository import GenerationRepository
from comfyui_studio.promptvault.core.sort_options import SortMode

# (имя, вид, сигнатура). Снято с repository.py до разреза.
SURFACE = [
    ("__init__", "method", "(self, db_path: 'str | Path | None' = None)"),
    ("_build_generations", "method", "(self, gen_rows: 'list[tuple]') -> 'list[Generation]'"),
    ("_folder_like_pattern", "class", "(cls, folder: 'str | Path') -> 'str'"),
    ("_format_bucket_value", "static", "(value: 'float') -> 'str'"),
    ("_like_escape", "static", "(value: 'str') -> 'str'"),
    (
        "_record_metadata_history",
        "method",
        "(self, conn: 'sqlite3.Connection', generation_id: 'int', old_values: 'dict[str, Any]', update_dict: 'dict[str, Any]') -> 'None'",
    ),
    (
        "_sql_histogram",
        "method",
        "(self, table: 'str', column: 'str', num_buckets: 'int' = 10) -> 'list[HistogramBucket]'",
    ),
    (
        "_upsert_generation",
        "method",
        "(self, folder: 'str', path: 'str', mtime: 'float', data: 'dict[str, Any]', extra_json: 'str') -> 'tuple[int, bool]'",
    ),
    ("add_generation_file", "method", "(self, path: 'str | Path') -> 'int | None'"),
    ("available_custom_tags", "method", "(self, folder: 'str | Path') -> 'set[str]'"),
    ("available_loras", "method", "(self, folder: 'str | Path') -> 'set[str]'"),
    ("available_models", "method", "(self, folder: 'str | Path') -> 'set[str]'"),
    ("available_samplers", "method", "(self, folder: 'str | Path') -> 'set[str]'"),
    ("close", "method", "(self) -> 'None'"),
    ("count_filtered", "method", "(self, folder: 'str | Path', options: 'FilterOptions') -> 'int'"),
    ("count_generations", "method", "(self, folder: 'str | Path') -> 'int'"),
    ("delete_generation", "method", "(self, generation_id: 'int', delete_files: 'bool' = False) -> 'bool'"),
    (
        "export_generations_zip",
        "method",
        "(self, generation_ids: 'list[int]', zip_path: 'str | Path', include_previews: 'bool' = True) -> 'int'",
    ),
    ("get_custom_tags", "method", "(self, generation_id: 'int') -> 'list[str]'"),
    ("get_filtered_ids", "method", "(self, folder: 'str | Path', options: 'FilterOptions') -> 'list[int]'"),
    ("get_generation", "method", "(self, generation_id: 'int') -> 'Generation | None'"),
    ("get_generation_extra_data", "method", "(self, generation_id: 'int') -> 'dict'"),
    ("get_metadata_history", "method", "(self, generation_id: 'int') -> 'list[dict[str, Any]]'"),
    ("get_statistics", "method", "(self) -> 'Statistics'"),
    ("import_user_data", "method", "(self, other_db_path: 'str | Path') -> 'tuple[int, int]'"),
    (
        "load_filtered_page",
        "method",
        "(self, folder: 'str | Path', options: 'FilterOptions', sort_mode: 'SortMode', offset: 'int' = 0, limit: 'int' = 500) -> 'list[Generation]'",
    ),
    ("load_generations", "method", "(self, folder: 'str | Path') -> 'list[Generation]'"),
    ("load_generations_page", "method", "(self, folder: 'str | Path', offset: 'int', limit: 'int') -> 'list[Generation]'"),
    ("set_custom_tags", "method", "(self, generation_id: 'int', tags: 'list[str]') -> 'None'"),
    ("set_favorite", "method", "(self, generation_id: 'int', value: 'bool') -> 'None'"),
    ("set_rating", "method", "(self, generation_id: 'int', value: 'int') -> 'None'"),
    ("sync_folder", "method", "(self, folder: 'str | Path') -> 'bool'"),
    ("update_generation", "method", "(self, generation_id: 'int', update_dict: 'dict[str, Any]') -> 'bool'"),
    ("update_generations", "method", "(self, generation_ids: 'list[int]', update_dict: 'dict[str, Any]') -> 'list[int]'"),
]

CLASS_ATTRIBUTES = ["_GENERATION_SELECT", "_JSON_FIELD_MAP"]


def _kind(name):
    raw = inspect.getattr_static(GenerationRepository, name)
    if isinstance(raw, staticmethod):
        return "static", raw.__func__
    if isinstance(raw, classmethod):
        return "class", raw.__func__
    return "method", raw


@pytest.mark.parametrize("name, kind, signature", SURFACE, ids=[row[0] for row in SURFACE])
def test_public_surface_is_unchanged(name, kind, signature):
    actual_kind, fn = _kind(name)
    assert actual_kind == kind
    assert str(inspect.signature(fn)) == signature


@pytest.mark.parametrize("name", CLASS_ATTRIBUTES)
def test_class_level_sql_constants_are_reachable(name):
    assert isinstance(getattr(GenerationRepository, name), (str, dict))


def test_no_unlisted_methods_appeared():
    """Новый метод -- осознанное решение: его надо добавить в SURFACE."""
    known = {row[0] for row in SURFACE} | set(CLASS_ATTRIBUTES)
    actual = {
        n
        for n in dir(GenerationRepository)
        if not (n.startswith("__") and n != "__init__") and n not in ("_abc_impl",)
    }
    # служебные атрибуты базовых классов (аннотации и т.п.) не считаем
    actual = {n for n in actual if callable(getattr(GenerationRepository, n)) or n in CLASS_ATTRIBUTES}
    assert actual == known, f"лишнее: {actual - known}, пропало: {known - actual}"


def test_class_is_defined_in_repository_module():
    """Импортируют именно отсюда (GalleryManager, folder_sync, main_window)."""
    assert GenerationRepository.__module__ == "comfyui_studio.promptvault.core.repository"


# --------------------------------------------------------------------------
# Сквозной сценарий
# --------------------------------------------------------------------------


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
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def repo(tmp_path):
    repository = GenerationRepository(tmp_path / "main.db")
    root = tmp_path / "gallery"
    root.mkdir()
    yield repository, root
    repository.close()


def test_end_to_end_lifecycle(repo, tmp_path):
    repository, root = repo

    # --- sync + чтение
    _write_json(root / "a.json", timestamp="t1", generation_time=1.0, positive_text="a cat")
    _write_json(
        root / "b.json", timestamp="t2", generation_time=2.0, model_name="modelB",
        sampler_name="DPM++", cfg=5.0, steps=30, positive_text="a dog",
        loras=[],
    )
    (root / "img1.png").write_bytes(b"png-bytes")
    assert repository.sync_folder(root) is True
    assert repository.count_generations(root) == 2
    assert len(repository.load_generations(root)) == 2
    assert len(repository.load_generations_page(root, 0, 1)) == 1
    assert repository.available_models(root) == {"modelA", "modelB"}
    assert repository.available_samplers(root) == {"Euler", "DPM++"}
    assert repository.available_loras(root) == {"loraX.safetensors"}

    # --- фильтрация
    options = FilterOptions(model="modelA")
    assert repository.count_filtered(root, options) == 1
    ids = repository.get_filtered_ids(root, options)
    assert len(ids) == 1
    [page_gen] = repository.load_filtered_page(root, options, SortMode.NEWEST)
    assert page_gen.id == ids[0]
    cat_id = ids[0]
    [dog_gen] = [g for g in repository.load_generations(root) if g.id != cat_id]

    # --- получение, редактирование, история
    assert repository.get_generation(cat_id).path.name == "a.json"
    assert repository.get_generation(987654) is None
    assert repository.update_generation(cat_id, {"positive": "a fluffy cat"}) is True
    assert repository.get_generation(cat_id).positive == "a fluffy cat"
    history = repository.get_metadata_history(cat_id)
    assert history and "positive" in json.dumps(history, ensure_ascii=False)
    on_disk = json.loads((root / "a.json").read_text(encoding="utf-8"))
    assert on_disk["positive_text"] == "a fluffy cat"

    # --- массовое обновление (возвращает id, которые НЕ удалось сохранить)
    assert repository.update_generations([cat_id, dog_gen.id], {"negative": "ugly"}) == []
    assert repository.update_generations([987654], {"negative": "x"}) == [987654]
    assert repository.get_generation(dog_gen.id).negative == "ugly"

    # --- избранное, рейтинг, теги
    repository.set_favorite(cat_id, True)
    repository.set_rating(cat_id, 5)
    repository.set_custom_tags(cat_id, ["pet", "cute"])
    assert set(repository.get_custom_tags(cat_id)) == {"pet", "cute"}
    assert repository.available_custom_tags(root) == {"pet", "cute"}
    assert repository.get_generation(cat_id).favorite is True
    assert repository.get_generation(cat_id).rating == 5

    # --- экстра-данные
    assert isinstance(repository.get_generation_extra_data(cat_id), dict)

    # --- экспорт в zip
    zip_path = tmp_path / "export.zip"
    assert repository.export_generations_zip([cat_id], zip_path, include_previews=False) == 1
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert any(n.endswith("a.json") for n in names)

    # --- импорт пользовательских данных в «чужой» репозиторий с той же галереей
    other = GenerationRepository(tmp_path / "other.db")
    try:
        other.sync_folder(root)
        [other_cat] = [g for g in other.load_generations(root) if g.path.name == "a.json"]
        assert other_cat.favorite is False
        other.close()
        other = GenerationRepository(tmp_path / "other.db")
        updated_count, unmatched = other.import_user_data(tmp_path / "main.db")
        assert updated_count >= 1 and unmatched == 0
        [other_cat] = [g for g in other.load_generations(root) if g.path.name == "a.json"]
        assert other_cat.favorite is True and other_cat.rating == 5
    finally:
        other.close()

    # --- статистика
    stats = repository.get_statistics()
    assert stats.total_generations == 2
    assert stats.total_favorites == 1
    assert dict(stats.top_models) == {"modelA": 1, "modelB": 1}
    assert stats.cfg_histogram and stats.steps_histogram

    # --- добавление одиночного файла
    extra = _write_json(root / "c.json", timestamp="t3", generation_time=3.0)
    new_id = repository.add_generation_file(extra)
    assert isinstance(new_id, int)
    assert repository.count_generations(root) == 3

    # --- удаление (с файлами) и «мягкое»
    assert repository.delete_generation(new_id, delete_files=True) is True
    assert not extra.exists()
    assert repository.delete_generation(dog_gen.id) is True
    assert (root / "b.json").exists()
    assert repository.delete_generation(dog_gen.id) is False
    assert repository.count_generations(root) == 1
