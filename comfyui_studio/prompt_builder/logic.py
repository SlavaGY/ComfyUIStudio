"""
logic.py
Вся не завязанная на конкретный GUI-фреймворк логика редактора:
валидация тегов, разбор/сборка записей LoRA, поиск и миграция
устаревшего формата LoRA в дереве блоков. Используется и характерами,
и конструктором промпта — без единого импорта Qt/Tk.
"""
from __future__ import annotations

import re
import time
from typing import Optional

_SUSPICIOUS_TAG_PATTERN = re.compile(r"[<>{}|\\]")
_MAX_TAG_LENGTH = 200


def validate_tags_text(tags: str) -> list[str]:
    """Возвращает список предупреждений по строке тегов (без обращения к диску)."""
    issues: list[str] = []
    if not tags:
        return issues

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    empty_count = sum(1 for t in tags.split(",") if not t.strip())
    if empty_count:
        issues.append(f"{empty_count} пустых элементов (лишние запятые)")

    long_tags = [t for t in tag_list if len(t) > _MAX_TAG_LENGTH]
    if long_tags:
        issues.append(f"есть теги длиннее {_MAX_TAG_LENGTH} символов")

    suspicious = [t for t in tag_list if _SUSPICIOUS_TAG_PATTERN.search(t)]
    if suspicious:
        issues.append("подозрительные символы: " + ", ".join(repr(t[:40]) for t in suspicious[:3]))

    seen: set[str] = set()
    dupes: list[str] = []
    for t in tag_list:
        if t in seen and t not in dupes:
            dupes.append(t)
        seen.add(t)
    if dupes:
        issues.append("дублирующиеся теги: " + ", ".join(dupes[:5]))

    return issues


def parse_lora_entry(entry: str) -> tuple[str, float]:
    """'name:1.0' -> ('name', 1.0); 'name' -> ('name', 1.0)."""
    entry = entry.strip()
    if ":" in entry:
        name, _, strength_s = entry.rpartition(":")
        try:
            strength = float(strength_s.strip())
        except ValueError:
            strength = 1.0
        return name.strip(), strength
    return entry, 1.0


def format_lora_entry(name: str, strength: float) -> str:
    return f"{name}:{strength:g}"


def new_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000) % 1000000}"


# --------------------------------------------------------------------------
# Персонажи (characters.json)
# --------------------------------------------------------------------------
class CharacterEntry:
    __slots__ = ("tags", "lora", "strength")

    def __init__(self, tags: str = "", lora: str = "", strength: float = 1.0):
        self.tags = tags
        self.lora = lora
        self.strength = strength

    @classmethod
    def from_raw(cls, raw) -> "CharacterEntry":
        if isinstance(raw, str):
            return cls(tags=raw)
        if isinstance(raw, dict):
            return cls(
                tags=str(raw.get("tags", "")),
                lora=str(raw.get("lora", "")).strip(),
                strength=float(raw.get("strength", raw.get("lora_strength", 1.0))),
            )
        return cls()

    def to_raw(self):
        if not self.lora and abs(self.strength - 1.0) < 1e-9:
            return self.tags
        return {"tags": self.tags, "lora": self.lora, "strength": self.strength}


# --------------------------------------------------------------------------
# Миграция старого формата LoRA у вариантов prompt_builder_config.json
# --------------------------------------------------------------------------
def legacy_lora_entry(opt: dict) -> Optional[str]:
    """Если у варианта задан старый формат {"lora": "...", "lora_strength": ...},
    возвращает строку 'name:strength' для него, иначе None."""
    name = str(opt.get("lora", "")).strip()
    if not name:
        return None
    strength = opt.get("lora_strength", opt.get("strength", 1.0))
    try:
        strength = float(strength)
    except (TypeError, ValueError):
        strength = 1.0
    return f"{name}:{strength:g}"


def find_legacy_lora_options(categories: list) -> list[dict]:
    """Рекурсивно обходит дерево блоков и возвращает список вариантов (options),
    использующих устаревший формат "lora"/"lora_strength" вместо "loras": [...]."""
    found: list[dict] = []

    def walk(node_list: list):
        for node in node_list:
            if node.get("type") == "group":
                walk(node.get("children", []))
            elif "options" in node:
                for opt in node.get("options", []):
                    if legacy_lora_entry(opt) is not None:
                        found.append(opt)

    walk(categories)
    return found


def migrate_legacy_lora_option(opt: dict) -> bool:
    """Переносит "lora"/"lora_strength" (и одиночный "strength") варианта в "loras": [...].
    Возвращает True, если что-то было перенесено."""
    entry = legacy_lora_entry(opt)
    if entry is None:
        return False
    loras = list(opt.get("loras", []))
    if entry not in loras:
        loras.append(entry)
    opt["loras"] = loras
    opt.pop("lora", None)
    opt.pop("lora_strength", None)
    opt.pop("strength", None)
    return True


def display_loras_for(node: dict) -> list[str]:
    """Список для показа в редакторе LoRA: включает и новые loras[],
    и (если есть) старый lora/lora_strength — чтобы ничего не пряталось."""
    entries = list(node.get("loras", []))
    legacy = legacy_lora_entry(node)
    if legacy is not None and legacy not in entries:
        entries.append(legacy)
    return entries
