"""Совместимость публичных вызовов GenerationRepository.

Фиксируются имена параметров, их порядок и значения по умолчанию:
на них опираются UI, синхронизация папок и менеджер галереи.
Приватные методы, SQL-константы и размещение класса по внутренним модулям
не входят в контракт. Аннотации типов проверяет mypy.

Сквозной сценарий проверяет результат работы всех групп операций.
"""

import inspect
import json
import zipfile

import pytest

from comfyui_studio.promptvault.core.generation_filter import FilterOptions
from comfyui_studio.promptvault.core.repository import GenerationRepository
from comfyui_studio.promptvault.core.sort_options import SortMode

# Сигнатуры вызовов существующих потребителей; новые методы допустимы.
PUBLIC_SIGNATURES = [
    ('__init__', '(self, db_path=None)'),
    ('add_generation_file', '(self, path)'),
    ('available_custom_tags', '(self, folder)'),
    ('available_loras', '(self, folder)'),
    ('available_models', '(self, folder)'),
    ('available_samplers', '(self, folder)'),
    ('close', '(self)'),
    ('count_filtered', '(self, folder, options)'),
    ('count_generations', '(self, folder)'),
    ('delete_generation', '(self, generation_id, delete_files=False)'),
    ('export_generations_zip', '(self, generation_ids, zip_path, include_previews=True)'),
    ('get_custom_tags', '(self, generation_id)'),
    ('get_filtered_ids', '(self, folder, options)'),
    ('get_generation', '(self, generation_id)'),
    ('get_generation_extra_data', '(self, generation_id)'),
    ('get_metadata_history', '(self, generation_id)'),
    ('get_statistics', '(self)'),
    ('import_user_data', '(self, other_db_path)'),
    ('load_filtered_page', '(self, folder, options, sort_mode, offset=0, limit=500)'),
    ('load_generations', '(self, folder)'),
    ('load_generations_page', '(self, folder, offset, limit)'),
    ('set_custom_tags', '(self, generation_id, tags)'),
    ('set_favorite', '(self, generation_id, value)'),
    ('set_rating', '(self, generation_id, value)'),
    ('sync_folder', '(self, folder)'),
    ('update_generation', '(self, generation_id, update_dict)'),
    ('update_generations', '(self, generation_ids, update_dict)'),
]


@pytest.mark.parametrize("name, expected", PUBLIC_SIGNATURES, ids=[row[0] for row in PUBLIC_SIGNATURES])
def test_public_call_signature(name, expected):
    signature = inspect.signature(getattr(GenerationRepository, name))
    call_signature = signature.replace(
        parameters=[p.replace(annotation=inspect.Parameter.empty) for p in signature.parameters.values()],
        return_annotation=inspect.Signature.empty,
    )
    assert str(call_signature) == expected


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
