"""Тесты для резолва путей ресурсов (иконка/переводы/темы) внутри
сборки PyInstaller (задача: иконка приложения в панели задач Windows
после сборки — см. подробный комментарий у ICON_PATH в app/config.py).

Модули app/config.py и app/themes/theme_manager.py вычисляют эти пути
на уровне модуля (при импорте), поэтому здесь используется
importlib.reload с заранее подставленными sys.frozen/sys._MEIPASS —
единственный способ проверить обе ветки (обычный запуск и "как будто
внутри сборки") без реальной сборки .exe.
"""

import importlib
import sys


def _reload(module):

    return importlib.reload(module)


class TestNonFrozenPaths:
    """Без sys.frozen/_MEIPASS поведение должно остаться прежним —
    относительно __file__ соответствующего модуля."""

    def test_config_paths_relative_to_app_package(self):

        import app.config as config

        _reload(config)

        assert config.ICON_PATH == config._APP_DIR / "resources" / "icon.png"
        assert config.TRANSLATIONS_DIR == config._APP_DIR / "resources" / "translations"
        assert config._APP_DIR.name == "app"

    def test_themes_dir_relative_to_themes_package(self):

        import app.themes.theme_manager as theme_manager

        _reload(theme_manager)

        assert theme_manager.THEMES_DIR.name == "themes"


class TestFrozenPaths:
    """С sys.frozen=True и sys._MEIPASS выставленными (как это делает
    бутлоадер PyInstaller) пути должны уйти под _MEIPASS/app/..., а не
    вычисляться через __file__ (который внутри сборки ненадёжен —
    см. комментарий в app/config.py)."""

    def test_config_paths_use_meipass(self, tmp_path, monkeypatch):

        fake_meipass = tmp_path / "_internal"
        fake_meipass.mkdir()

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(fake_meipass), raising=False)

        import app.config as config

        try:
            _reload(config)

            assert config._APP_DIR == fake_meipass / "app"
            assert config.ICON_PATH == fake_meipass / "app" / "resources" / "icon.png"
            assert (
                config.TRANSLATIONS_DIR
                == fake_meipass / "app" / "resources" / "translations"
            )
        finally:
            # переигрываем модуль обратно на нормальные пути ДО того,
            # как monkeypatch снимет sys.frozen/_MEIPASS -- иначе
            # следующие тесты в этом процессе унаследуют пути,
            # посчитанные под fake_meipass (module-level константы,
            # реimport в другом тесте их не пересчитает сам по себе)
            monkeypatch.undo()
            _reload(config)

    def test_themes_dir_uses_meipass(self, tmp_path, monkeypatch):

        fake_meipass = tmp_path / "_internal"
        fake_meipass.mkdir()

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(fake_meipass), raising=False)

        import app.themes.theme_manager as theme_manager

        try:
            _reload(theme_manager)

            assert theme_manager.THEMES_DIR == fake_meipass / "app" / "themes"
        finally:
            monkeypatch.undo()
            _reload(theme_manager)
