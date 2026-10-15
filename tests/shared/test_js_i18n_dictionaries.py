"""Словари переводов веб-приложений (Imagine, Imagine Pony, Ultimate RVC).

Переводы в JS -- один из «нескольких механизмов» локализации проекта (этап
6.5 роадмапа аудита); Qt-часть покрыта tests/shared/test_localization_
manager.py, а у словарей в js/i18n.js проверок не было вовсе: дубль ключа
(JS молча оставляет последнее значение) или строка в разметке без перевода
находились только глазами. Здесь словари разбираются без JS-движка и
сверяются с тем, что реально используется в index.html и app.js.

Принцип словаря: русская строка -- исходный текст и он же ключ; EN --
перевод; строка без записи показывается как есть (поэтому «нет перевода» --
не ошибка выполнения, а тихая недоработка, которую этот файл и ловит).
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent.parent / "comfyui_studio"
APPS = ["imagine", "imagine_pony", "rvc"]
PLACEHOLDER = re.compile(r"\{[A-Za-z_]+\}")


def _string_literal(text: str, i: int) -> tuple[str, int]:
    """JS-строка в одинарных или двойных кавычках -> (значение, индекс после неё)."""
    quote = text[i]
    j = i + 1
    while text[j] != quote:
        if text[j] == "\n":
            raise ValueError("перенос строки внутри строкового литерала")
        j += 2 if text[j] == "\\" else 1
    body = text[i + 1 : j]
    if quote == '"':
        return json.loads('"' + body + '"'), j + 1
    out: list[str] = []
    k = 0
    while k < len(body):  # одинарные кавычки -> эквивалентный JSON
        ch = body[k]
        if ch == "\\" and k + 1 < len(body):
            nxt = body[k + 1]
            out.append("'" if nxt == "'" else ch + nxt)
            k += 2
        else:
            out.append('\\"' if ch == '"' else ch)
            k += 1
    return json.loads('"' + "".join(out) + '"'), j + 1


def parse_en_dictionary(text: str) -> list[tuple[str, str]]:
    """Пары (ключ, значение) литерала ``const EN = { ... };`` в порядке
    следования, повторяющиеся ключи сохраняются."""
    i = text.index("const EN = {") + len("const EN = {")
    tokens: list[tuple[str, str]] = []
    while i < len(text):
        c = text[i]
        if c.isspace():
            i += 1
        elif text.startswith("//", i):
            i = text.index("\n", i)
        elif text.startswith("/*", i):
            i = text.index("*/", i) + 2
        elif c in "\"'":
            value, i = _string_literal(text, i)
            tokens.append(("str", value))
        elif c in ":,":
            tokens.append((c, c))
            i += 1
        elif c == "}":
            break
        else:
            raise ValueError(f"неожиданный символ {c!r} в словаре: ...{text[i : i + 30]!r}")
    pairs: list[tuple[str, str]] = []
    k = 0
    while k < len(tokens):
        if (
            k + 2 < len(tokens)
            and tokens[k][0] == "str"
            and tokens[k + 1][0] == ":"
            and tokens[k + 2][0] == "str"
        ):
            pairs.append((tokens[k][1], tokens[k + 2][1]))
            k += 3
            if k < len(tokens) and tokens[k][0] == ",":
                k += 1
        else:
            raise ValueError(f"нарушена форма «ключ: значение,» около токена {k}: {tokens[k : k + 4]}")
    return pairs


def _pairs(app: str) -> list[tuple[str, str]]:
    return parse_en_dictionary((PACKAGE / app / "static" / "js" / "i18n.js").read_text(encoding="utf-8"))


def _used_keys(app: str) -> set[str]:
    """Ключи, которые приложение реально просит перевести: атрибуты
    data-i18n* в index.html и вызовы ``t('литерал', ...)`` в app.js."""
    root = PACKAGE / app / "static"
    keys: set[str] = set()
    page = (root / "index.html").read_text(encoding="utf-8")
    for match in re.finditer(r'data-i18n(?:-title|-placeholder)?="([^"]*)"', page):
        keys.add(html.unescape(match.group(1)))
    script = (root / "js" / "app.js").read_text(encoding="utf-8")
    for match in re.finditer(r"""(?<![\w.$])t\(\s*(["'])""", script):
        value, _ = _string_literal(script, match.start(1))
        keys.add(value)
    return keys


@pytest.mark.parametrize("app", APPS)
def test_dictionary_is_parseable_and_not_empty(app):
    assert len(_pairs(app)) > 50


@pytest.mark.parametrize("app", APPS)
def test_no_duplicate_keys(app):
    """JS-литерал с повторным ключом не падает, а молча оставляет
    последнее значение -- лишняя строка либо мусор, либо потерянный вариант
    перевода."""
    keys = [key for key, _ in _pairs(app)]
    assert sorted({k for k in keys if keys.count(k) > 1}) == []


@pytest.mark.parametrize("app", APPS)
def test_translations_are_not_empty(app):
    assert [key for key, value in _pairs(app) if not value.strip()] == []


@pytest.mark.parametrize("app", APPS)
def test_placeholders_match_between_key_and_translation(app):
    """``{n}`` в русском ключе обязан остаться в английском тексте (и
    наоборот), иначе подстановка значения тихо пропадёт."""
    bad = [
        (key, value)
        for key, value in _pairs(app)
        if sorted(PLACEHOLDER.findall(key)) != sorted(PLACEHOLDER.findall(value))
    ]
    assert bad == []


@pytest.mark.parametrize("app", APPS)
def test_every_literal_string_in_markup_and_script_has_a_translation(app):
    """Каждая строка из data-i18n* и t('...') есть в словаре. Нелитеральные
    вызовы (``t(f.label)``) тестом не видны -- их ключи собирает движок
    приложения на лету."""
    known = {key for key, _ in _pairs(app)}
    assert sorted(_used_keys(app) - known) == []


@pytest.mark.parametrize("app", ["imagine", "imagine_pony"])
def test_shared_engine_is_wired_before_the_dictionary(app):
    """Страница подключает общий движок ОТНОСИТЕЛЬНЫМ путём и раньше
    словаря: за reverse-proxy Remote путь с ведущим «/» уходит мимо
    приложения, а словарь без движка не определит ни I18N, ни t()."""
    page = (PACKAGE / app / "static" / "index.html").read_text(encoding="utf-8")
    engine = '<script src="common/js/i18n_engine.js"></script>'
    dictionary = '<script src="js/i18n.js"></script>'
    assert engine in page and dictionary in page
    assert page.index(engine) < page.index(dictionary) < page.index('<script src="js/app.js"></script>')


@pytest.mark.parametrize(
    "app, prefix", [("imagine", "imagine"), ("imagine_pony", "imaginepony")]
)
def test_dictionary_file_only_creates_an_engine_instance(app, prefix):
    """Движка в файле словаря больше нет (раньше -- копия на ~150 строк в
    каждом приложении); у приложений разные префиксы localStorage, чтобы
    выбор темы/языка одного не затирал другое."""
    text = (PACKAGE / app / "static" / "js" / "i18n.js").read_text(encoding="utf-8")
    assert f'window.createI18n({{ storagePrefix: "{prefix}", EN }})' in text
    assert "function applyStaticTranslations" not in text
    assert "window.t = I18N.t;" in text
