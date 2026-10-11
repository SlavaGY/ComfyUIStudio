"""Каждая строка интерфейса приложения RVC должна иметь английский перевод в
static/js/i18n.js (иначе в EN-режиме останется русский текст). Строки берутся
из index.html (data-i18n*), из t("...") и из описания полей в app.js, плюс
подписи файлов результата из backend/engine.py."""

import re
from pathlib import Path

from comfyui_studio.rvc.backend.engine import SONG_RESULT_LABELS

STATIC = Path(__file__).resolve().parents[2] / "comfyui_studio" / "rvc" / "static"
# Подписи, которые собираются не литералом (тернарник внутри int(...)) или
# приходят от сервера.
EXTRA = [
    "Сдвиг тона, полутонов",
    "Сдвиг тона, полутонов (голос и музыка)",
    "Озвучка",
    "Файл",
]


def _dictionary():
    text = (STATIC / "js" / "i18n.js").read_text(encoding="utf-8")
    block = text[text.index("const EN = {"): text.index("const THEMES")]
    return set(re.findall(r'^\s*"([^"\n]+)":', block, re.M))


def _used():
    js = (STATIC / "js" / "app.js").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    keys = set(re.findall(r'\bt\("([^"]+)"\)', js))
    keys |= set(re.findall(r'(?:int|bool|sel|rng)\("[a-z_0-9]+", "([^"]+)"', js))
    keys |= set(re.findall(r'(?:title|label): "([^"]+)"', js))
    keys |= set(re.findall(r'data-i18n(?:-title|-placeholder)?="([^"]+)"', html))
    keys |= set(re.findall(r'^\s*(?:queued|running|done|error|cancelled): "([^"]+)"', js, re.M))
    keys |= set(re.findall(r'(?:queued|running|done|error|cancelled): "([^"]+)"', js))
    keys |= set(SONG_RESULT_LABELS) | set(EXTRA)
    return keys


def test_every_ui_string_has_english_translation():
    missing = sorted(k for k in _used() - _dictionary())
    assert missing == []


def test_dictionary_has_no_stale_entries():
    # Лишние ключи -- признак переименованной строки, у которой перевод остался
    # от старого текста, а новый -- без перевода.
    stale = sorted(k for k in _dictionary() - _used())
    assert stale == []
