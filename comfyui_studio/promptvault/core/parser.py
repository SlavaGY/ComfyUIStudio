"""Парсинг JSON-файлов генерации в нормализованный словарь для БД."""

import json
from pathlib import Path
from typing import Any

# ключи верхнего уровня, которые распознаются явно и не попадают в extra_data
KNOWN_KEYS = {
    "timestamp",
    "prefix",
    "counter",
    "images",
    "positive_text",
    "negative_text",
    "prompt",
    "negative_prompt",
    "cfg",
    "steps",
    "sampler_name",
    "add_noise",
    "noise_seed",
    "batch_size",
    "model_name",
    "generation_time",
    "loras",
}


def parse_generation_data(path: str | Path) -> dict[str, Any]:
    """Читает JSON-файл генерации и возвращает нормализованный словарь.

    Формат результата рассчитан на прямую вставку в БД
    (см. core/repository.py), а не на создание объекта Generation
    напрямую — это делает репозиторий, объединяя данные из БД
    (id, favorite, rating) с этими распарсенными полями.
    """

    path = Path(path)

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    images = []

    for i in data.get("images", []):

        # новая структура
        if isinstance(i, dict):
            images.append({
                "file": i.get("file", ""),
                "seed": i.get("seed"),
            })

        # старая структура
        elif isinstance(i, str):
            images.append({
                "file": i,
                "seed": None,
            })

    loras = []

    for lora_data in data.get("loras", []):
        loras.append({
            "filename": lora_data.get("filename") or lora_data.get("name", ""),
            "strength": lora_data.get("strength", 1.0),
            "source": lora_data.get("source"),
        })

    extra = {
        k: v
        for k, v in data.items()
        if k not in KNOWN_KEYS
    }

    return {
        "timestamp": data.get("timestamp", ""),
        "generation_time": data.get("generation_time", 0),
        "model": data.get("model_name", ""),
        "cfg": data.get("cfg", 0),
        "steps": data.get("steps", 0),
        "sampler": data.get("sampler_name", ""),
        "positive": data.get("positive_text", data.get("prompt", "")),
        "negative": data.get("negative_text", data.get("negative_prompt", "")),
        "extra_data": extra,
        "images": images,
        "loras": loras,
    }
