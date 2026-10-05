"""Тесты shared_theme.py и shared_language.py (этап R8).

Оба модуля устроены одинаково (общий JSON-файл в %APPDATA%\\ComfyUIStudio),
поэтому все тесты прогоняются для обоих. Пути перенаправлены во
временную папку корневым conftest.py.

Главное, что здесь фиксируется: после замены ``except Exception`` на
конкретные исключения чтение по-прежнему НИКОГДА не бросает наружу — на
любом повреждённом файле возвращается None (раньше это гарантировал
широкий except), а запись при ошибке ФС молча не падает.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from comfyui_studio import shared_language, shared_theme

_CASES = [
    pytest.param(
        SimpleNamespace(
            mod=shared_theme,
            read="read_shared_theme",
            write="write_shared_theme",
            path_attr="SHARED_THEME_PATH",
            key="theme",
            value="Nord",
            logger="comfyui_studio.shared_theme",
        ),
        id="theme",
    ),
    pytest.param(
        SimpleNamespace(
            mod=shared_language,
            read="read_shared_language",
            write="write_shared_language",
            path_attr="SHARED_LANGUAGE_PATH",
            key="language",
            value="en",
            logger="comfyui_studio.shared_language",
        ),
        id="language",
    ),
]


@pytest.fixture(params=_CASES)
def api(request):
    case = request.param
    case.read_fn = getattr(case.mod, case.read)
    case.write_fn = getattr(case.mod, case.write)
    return case


def _path(api):
    import pathlib

    return pathlib.Path(getattr(api.mod, api.path_attr))


def _warnings(caplog, api):
    return [
        r for r in caplog.records if r.name == api.logger and r.levelno >= logging.WARNING
    ]


class TestRead:
    def test_missing_file_is_normal_and_silent(self, api, caplog):
        """Первый запуск: файла ещё нет — это не ошибка, шума в логе нет."""
        with caplog.at_level(logging.DEBUG, logger=api.logger):
            assert api.read_fn() is None
        assert _warnings(caplog, api) == []

    def test_roundtrip(self, api):
        api.write_fn(api.value)
        assert api.read_fn() == api.value

    def test_invalid_json_returns_none_and_warns(self, api, caplog):
        _path(api).write_text("{not json", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger=api.logger):
            assert api.read_fn() is None
        assert len(_warnings(caplog, api)) == 1

    def test_invalid_utf8_returns_none_and_warns(self, api, caplog):
        """UnicodeDecodeError — не json.JSONDecodeError, но тоже ValueError;
        сузить except до одного JSONDecodeError было бы ошибкой."""
        _path(api).write_bytes(b'{"x": "\xff\xfe"}')
        with caplog.at_level(logging.WARNING, logger=api.logger):
            assert api.read_fn() is None
        assert len(_warnings(caplog, api)) == 1

    @pytest.mark.parametrize("payload", ["[]", "[1, 2]", "42", '"Nord"', "null", "true"])
    def test_non_object_json_returns_none(self, api, caplog, payload):
        """Корень JSON — не объект. Раньше это «работало» только потому,
        что AttributeError от data.get() глотал широкий except."""
        _path(api).write_text(payload, encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger=api.logger):
            assert api.read_fn() is None
        assert len(_warnings(caplog, api)) == 1

    @pytest.mark.parametrize(
        "payload",
        ['{}', '{"other": "x"}', '{"%s": ""}', '{"%s": 5}', '{"%s": null}', '{"%s": ["a"]}'],
    )
    def test_missing_or_non_string_value_returns_none(self, api, payload):
        _path(api).write_text(payload.replace("%s", api.key), encoding="utf-8")
        assert api.read_fn() is None

    def test_unreadable_path_returns_none(self, api, monkeypatch, tmp_path):
        """Путь указывает на каталог: open() даёт OSError (IsADirectoryError
        / PermissionError на Windows), а не FileNotFoundError."""
        directory = tmp_path / "is_a_dir"
        directory.mkdir()
        monkeypatch.setattr(api.mod, api.path_attr, str(directory))
        assert api.read_fn() is None


class TestWrite:
    def test_creates_missing_directory(self, api, monkeypatch, tmp_path):
        new_dir = tmp_path / "does" / "not" / "exist"
        monkeypatch.setattr(api.mod, "SHARED_DIR", str(new_dir))
        monkeypatch.setattr(api.mod, api.path_attr, str(new_dir / f"{api.key}.json"))
        api.write_fn(api.value)
        assert api.read_fn() == api.value

    def test_overwrites_previous_value(self, api):
        api.write_fn("A")
        api.write_fn("B")
        assert api.read_fn() == "B"

    def test_unwritable_location_does_not_raise_but_warns(
        self, api, monkeypatch, tmp_path, caplog
    ):
        """Ошибка ФС (нет прав на APPDATA и т. п.) не должна ронять
        приложение или мешать локальному применению темы/языка."""
        blocker = tmp_path / "blocker"
        blocker.write_text("я файл, а не папка", encoding="utf-8")
        bad_dir = blocker / "sub"  # makedirs здесь гарантированно падает с OSError
        monkeypatch.setattr(api.mod, "SHARED_DIR", str(bad_dir))
        monkeypatch.setattr(api.mod, api.path_attr, str(bad_dir / f"{api.key}.json"))

        with caplog.at_level(logging.WARNING, logger=api.logger):
            api.write_fn(api.value)  # не должно бросить

        assert len(_warnings(caplog, api)) == 1


class TestWatcher:
    def test_watcher_reports_external_change_once_and_ignores_own_write(self, api, qapp):
        watcher_cls = getattr(
            api.mod, "SharedThemeWatcher" if api.key == "theme" else "SharedLanguageWatcher"
        )
        signal_name = "theme_changed" if api.key == "theme" else "language_changed"

        api.write_fn("A")
        watcher = watcher_cls()
        received: list[str] = []
        getattr(watcher, signal_name).connect(received.append)

        # «Свою» запись процесс помечает через mark_applied — эха быть не должно
        watcher.mark_applied("B")
        api.write_fn("B")
        watcher._on_changed("ignored")
        assert received == []

        # Чужая запись — один раз, повтор того же значения не дублируется
        api.write_fn("C")
        watcher._on_changed("ignored")
        watcher._on_changed("ignored")
        assert received == ["C"]

    def test_watcher_survives_corrupt_file(self, api, qapp):
        """Повреждённый файл не должен ронять слот и не даёт ложных сигналов."""
        watcher_cls = getattr(
            api.mod, "SharedThemeWatcher" if api.key == "theme" else "SharedLanguageWatcher"
        )
        signal_name = "theme_changed" if api.key == "theme" else "language_changed"
        api.write_fn("A")
        watcher = watcher_cls()
        received: list[str] = []
        getattr(watcher, signal_name).connect(received.append)

        _path(api).write_text("[[[", encoding="utf-8")
        watcher._on_changed("ignored")
        assert received == []
