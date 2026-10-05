"""Тесты менеджеров тем (этап R8): общая база и три приложения.

Фиксируют три вещи, на которых держится «вид не изменился»:

1. ИТОГОВЫЙ QSS каждого приложения собирается ровно из тех кусков, что
   и до R8 (лаунчер = база + хвост лаунчера, Prompt Builder = база +
   довесок из палитры, PromptVault = база);
2. имена QSettings (организация/приложение) прежние — иначе сохранённая
   пользователем тема потерялась бы;
3. набор базовых .qss один и дубли не вернутся.

Пути к общему файлу темы и QSettings перенаправлены во временную папку
корневым conftest.py.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

import comfyui_studio.themes.base as themes_base
from comfyui_studio import shared_theme
from comfyui_studio.prompt_builder.theme_manager import EXTRA_PALETTE, _supplemental_qss
from comfyui_studio.prompt_builder.theme_manager import ThemeManager as PromptBuilderThemeManager
from comfyui_studio.promptvault.themes.theme_manager import (
    ThemeManager as PromptVaultThemeManager,
)
from comfyui_studio.themes.base import (
    AVAILABLE_THEMES,
    DEFAULT_THEME,
    BaseThemeManager,
)
from comfyui_studio.themes.theme_manager import ThemeManager as LauncherThemeManager

PACKAGE_DIR = Path(themes_base.__file__).resolve().parent.parent  # .../comfyui_studio
THEMES = list(AVAILABLE_THEMES)
ALL_MANAGERS = [LauncherThemeManager, PromptBuilderThemeManager, PromptVaultThemeManager]


@pytest.fixture
def clean_app_style(qapp):
    yield qapp
    qapp.setStyleSheet("")


def _base_text(theme: str) -> str:
    return (themes_base.THEMES_DIR / AVAILABLE_THEMES[theme]).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Один набор .qss
# --------------------------------------------------------------------------


class TestSingleThemeSet:
    def test_base_dir_has_exactly_the_six_themes(self):
        files = sorted(p.name for p in themes_base.THEMES_DIR.glob("*.qss"))
        assert files == sorted(AVAILABLE_THEMES.values())

    def test_launcher_addon_exists_for_every_theme(self):
        addon_dir = themes_base.THEMES_DIR / "launcher"
        assert sorted(p.name for p in addon_dir.glob("*.qss")) == sorted(
            AVAILABLE_THEMES.values()
        )

    def test_no_qss_outside_shared_themes_dir(self):
        """Дубли .qss в папках приложений (было: по копии у Prompt Builder и
        PromptVault, побайтно равных) не должны вернуться."""
        stray = [
            str(p.relative_to(PACKAGE_DIR))
            for p in PACKAGE_DIR.rglob("*.qss")
            if themes_base.THEMES_DIR not in p.parents
        ]
        assert stray == []

    def test_base_themes_do_not_contain_launcher_addon(self):
        """Хвост лаунчера обязан жить только в themes/launcher/, иначе он
        снова «просочится» в Prompt Builder и PromptVault."""
        for theme in THEMES:
            assert "Дополнено для ComfyUI Launcher" not in _base_text(theme)


# --------------------------------------------------------------------------
# Итоговый QSS каждого приложения
# --------------------------------------------------------------------------


class TestComposedStylesheets:
    @pytest.mark.parametrize("theme", THEMES)
    def test_promptvault_is_base_only(self, qapp, theme):
        assert PromptVaultThemeManager().load_stylesheet(theme) == _base_text(theme)

    @pytest.mark.parametrize("theme", THEMES)
    def test_promptbuilder_is_base_plus_palette_addon(self, qapp, theme):
        expected = _base_text(theme) + "\n" + _supplemental_qss(EXTRA_PALETTE[theme])
        assert PromptBuilderThemeManager().load_stylesheet(theme) == expected

    @pytest.mark.parametrize("theme", THEMES)
    def test_launcher_is_base_plus_launcher_addon(self, qapp, theme):
        addon = (themes_base.THEMES_DIR / "launcher" / AVAILABLE_THEMES[theme]).read_text(
            encoding="utf-8"
        )
        sheet = LauncherThemeManager().load_stylesheet(theme)
        assert sheet == _base_text(theme) + "\n" + addon
        # те самые правила, ради которых хвост существует
        for selector in ("QGroupBox", "QProgressBar", "QSpinBox", "QMenu"):
            assert selector in addon

    @pytest.mark.parametrize("manager_cls", ALL_MANAGERS)
    def test_unknown_theme_falls_back_to_default(self, qapp, manager_cls):
        manager = manager_cls()
        assert manager.load_stylesheet("нет такой темы") == manager.load_stylesheet(
            DEFAULT_THEME
        )

    @pytest.mark.parametrize("manager_cls", ALL_MANAGERS)
    def test_stylesheet_is_cached(self, qapp, manager_cls, monkeypatch):
        manager = manager_cls()
        first = manager.load_stylesheet("Nord")
        # если бы файл читался заново, пустая папка дала бы другой результат
        monkeypatch.setattr(themes_base, "THEMES_DIR", Path("/нет/такой/папки"))
        assert manager.load_stylesheet("Nord") == first

    @pytest.mark.parametrize("manager_cls", [LauncherThemeManager, PromptVaultThemeManager])
    def test_missing_theme_files_are_logged_not_raised(
        self, qapp, manager_cls, monkeypatch, tmp_path, caplog
    ):
        monkeypatch.setattr(themes_base, "THEMES_DIR", tmp_path / "empty")
        manager = manager_cls()
        with caplog.at_level(logging.ERROR):
            assert manager.load_stylesheet("Dark") == ""
        assert any("Не удалось прочитать" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------
# Настройки приложений (то, что нельзя менять без потери выбора пользователя)
# --------------------------------------------------------------------------


class TestPerAppSettings:
    @pytest.mark.parametrize(
        ("manager_cls", "org_app"),
        [
            (LauncherThemeManager, ("ComfyUILauncher", "ComfyUILauncher")),
            (PromptBuilderThemeManager, ("PromptConfigEditor", "PromptConfigEditor")),
            (PromptVaultThemeManager, ("PromptVault", "PromptVault")),
        ],
    )
    def test_qsettings_names_unchanged(
        self, qapp, clean_app_style, qsettings_factory, manager_cls, org_app
    ):
        assert (manager_cls.settings_org, manager_cls.settings_app) == org_app
        manager_cls().apply_theme("Nord")
        # выбор лежит именно там, где его искало приложение до R8
        assert qsettings_factory(*org_app).value("theme") == "Nord"

    def test_only_launcher_repolishes_widgets(self):
        """Поведение apply_theme у приложений до R8 не менялось: re-polish
        всех виджетов делал только лаунчер."""
        assert LauncherThemeManager.repolish_widgets is True
        assert PromptBuilderThemeManager.repolish_widgets is False
        assert PromptVaultThemeManager.repolish_widgets is False

    def test_launcher_logger_goes_to_launcher_log(self):
        """Имя должно оставаться «comfyui_launcher.*» — по нему сообщения
        попадают в launcher.log (см. setup_logging)."""
        assert LauncherThemeManager.log_name.startswith("comfyui_launcher.")

    @pytest.mark.parametrize("manager_cls", ALL_MANAGERS)
    def test_all_are_based_on_one_class(self, manager_cls):
        assert issubclass(manager_cls, BaseThemeManager)


# --------------------------------------------------------------------------
# Поведение общей базы
# --------------------------------------------------------------------------


@pytest.mark.parametrize("manager_cls", ALL_MANAGERS)
class TestBaseBehaviour:
    def test_available_themes_in_fixed_order(self, qapp, manager_cls):
        assert manager_cls().available_themes() == [
            "Dark",
            "Light",
            "Nord",
            "Catppuccin Mocha",
            "Dracula",
            "GitHub Dark",
        ]

    def test_circle_color(self, qapp, manager_cls):
        manager = manager_cls()
        assert manager.circle_color("Light") == "#1e1e1e"
        assert manager.circle_color("нет такой") == "#ffffff"

    def test_default_theme_when_nothing_saved(self, qapp, manager_cls):
        assert manager_cls().current_theme() == DEFAULT_THEME

    def test_apply_theme_sets_stylesheet_saves_and_shares(
        self, qapp, clean_app_style, manager_cls
    ):
        manager = manager_cls()
        applied: list[str] = []
        manager.theme_applied.connect(applied.append)

        manager.apply_theme("Dracula")

        assert qapp.styleSheet() == manager.load_stylesheet("Dracula")
        assert applied == ["Dracula"]
        assert shared_theme.read_shared_theme() == "Dracula"
        assert manager.current_theme() == "Dracula"

    def test_apply_invalid_theme_applies_default_everywhere(
        self, qapp, clean_app_style, manager_cls
    ):
        """Раньше невалидное имя сохранялось в QSettings и общий файл как
        есть (кроме лаунчера); теперь везде применяется и пишется тема по
        умолчанию."""
        manager = manager_cls()
        manager.apply_theme("не-тема")
        assert qapp.styleSheet() == manager.load_stylesheet(DEFAULT_THEME)
        assert shared_theme.read_shared_theme() == DEFAULT_THEME

    def test_apply_theme_without_application_is_noop(self, qapp, manager_cls, monkeypatch):
        manager = manager_cls()
        monkeypatch.setattr(
            "comfyui_studio.themes.base.QApplication.instance", staticmethod(lambda: None)
        )
        manager.apply_theme("Nord")  # не должно бросить
        assert shared_theme.read_shared_theme() is None  # и ничего не записано

    def test_apply_theme_with_explicit_app(self, qapp, clean_app_style, manager_cls):
        manager_cls().apply_theme("Light", qapp)
        assert qapp.styleSheet() != ""

    def test_shared_theme_wins_over_saved(self, qapp, clean_app_style, manager_cls):
        manager = manager_cls()
        manager.apply_theme("Nord")  # QSettings = Nord, общий = Nord
        shared_theme.write_shared_theme("Dracula")
        assert manager.current_theme() == "Dracula"

    def test_invalid_shared_theme_falls_back_to_saved(self, qapp, clean_app_style, manager_cls):
        manager = manager_cls()
        manager.apply_theme("Nord")
        shared_theme.write_shared_theme("не-тема")
        assert manager.current_theme() == "Nord"

    def test_invalid_saved_theme_falls_back_to_default(self, qapp, qsettings_factory, manager_cls):
        qsettings_factory(manager_cls.settings_org, manager_cls.settings_app).setValue(
            "theme", "мусор"
        )
        assert manager_cls().current_theme() == DEFAULT_THEME

    def test_theme_persists_across_instances(self, qapp, clean_app_style, manager_cls):
        manager_cls().apply_theme("GitHub Dark")
        # общий файл убираем: остаётся только QSettings
        Path(shared_theme.SHARED_THEME_PATH).unlink()
        assert manager_cls().current_theme() == "GitHub Dark"


@pytest.mark.parametrize("manager_cls", ALL_MANAGERS)
class TestExternalChange:
    def test_external_theme_is_applied_and_reported(self, qapp, clean_app_style, manager_cls):
        manager = manager_cls()
        manager.apply_theme("Dark")
        external: list[str] = []
        applied: list[str] = []
        manager.theme_changed_externally.connect(external.append)
        manager.theme_applied.connect(applied.append)

        # проверяем именно проводку watcher -> менеджер, без файловой системы
        manager._watcher.theme_changed.emit("Nord")

        assert external == ["Nord"]
        assert applied == ["Nord"]
        assert qapp.styleSheet() == manager.load_stylesheet("Nord")

    def test_same_theme_is_not_reapplied(self, qapp, clean_app_style, manager_cls):
        manager = manager_cls()
        manager.apply_theme("Nord")
        external: list[str] = []
        manager.theme_changed_externally.connect(external.append)
        manager._watcher.theme_changed.emit("Nord")
        assert external == []

    def test_unknown_external_theme_is_ignored(self, qapp, clean_app_style, manager_cls):
        manager = manager_cls()
        manager.apply_theme("Nord")
        external: list[str] = []
        manager.theme_changed_externally.connect(external.append)
        manager._watcher.theme_changed.emit("не-тема")
        assert external == []
        assert manager.current_theme() == "Nord"

    def test_own_write_is_not_taken_for_external_change(
        self, qapp, clean_app_style, manager_cls
    ):
        """apply_theme помечает тему как применённую в watcher'е: запись
        в общий файл самим приложением не должна вернуться «эхом»."""
        manager = manager_cls()
        external: list[str] = []
        manager.theme_changed_externally.connect(external.append)
        manager.apply_theme("Light")
        manager._watcher._on_changed("ignored")
        assert external == []


def test_apply_theme_with_live_widgets_does_not_break(qapp, clean_app_style):
    """Лаунчер при применении темы re-polish'ит все живые виджеты — на
    реальных виджетах это не должно падать."""
    from PySide6.QtWidgets import QGroupBox, QPushButton, QVBoxLayout, QWidget

    host = QWidget()
    layout = QVBoxLayout(host)
    layout.addWidget(QPushButton("кнопка"))
    layout.addWidget(QGroupBox("группа"))
    host.show()
    try:
        LauncherThemeManager().apply_theme("Light")
        LauncherThemeManager().apply_theme("Dark")
    finally:
        host.close()
