"""Общие fixtures для ВСЕХ тестов репозитория (tests/ и tools/promptvault/tests/).

Зачем. Менеджеры тем и языка (comfyui_studio/themes/base.py,
comfyui_studio/i18n_base.py) при каждом apply_*() пишут:

* общие файлы комплекта ``%APPDATA%\\ComfyUIStudio\\theme.json`` и
  ``language.json`` (shared_theme.py / shared_language.py);
* выбор в QSettings приложения — а на Windows QSettings(org, app) это
  РЕЕСТР (HKCU\\Software\\<org>\\<app>).

Без изоляции каждый прогон pytest перезаписывал бы тему и язык
настоящего лаунчера пользователя (например, тест PromptVault в конце
ставит язык "en", и лаунчер при следующем запуске открывался бы
английским). Поэтому на время каждого теста и общие файлы, и QSettings
менеджеров тем/языка перенаправляются во временную папку.

Про QSettings: конструктор QSettings(org, app) ВСЕГДА берёт NativeFormat —
QSettings.setDefaultFormat() на него не действует, а QSettings.setPath()
для реестра Windows игнорируется. Поэтому единственный надёжный способ —
подменить саму фабрику QSettings в модулях базовых менеджеров на
INI-файл во временной папке. Фабрика доступна тестам как fixture
``qsettings_factory`` — ей же нужно читать то, что записал менеджер.

Остальные QSettings приложения здесь НЕ перенаправляются (только
менеджеры тем и языка); ``~/.promptvault`` (собственная папка PromptVault
вне APPDATA) тоже не затрагивается.

Папки данных (этап R2). Все файлы под ``%APPDATA%\\ComfyUIStudio`` и
``%APPDATA%\\ComfyUILauncher`` (настройки генератора промптов, история,
устройства Remote, config.json и лог лаунчера, данные Imagine…)
перенаправляются на ВСЮ сессию во временную папку через переменные
окружения ``COMFYUI_STUDIO_DATA_DIR`` / ``COMFYUI_LAUNCHER_DATA_DIR``
(см. comfyui_studio/app_paths.py). Делается на уровне модуля conftest,
то есть до первого импорта comfyui_studio: модули запоминают пути в
константах при импорте. Поэтому ни один тест, даже забывший сделать
monkeypatch, не может записать в настоящие данные пользователя. Тесты,
которым нужна своя чистая папка, по-прежнему подменяют константы модулей.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile

import pytest

_SESSION_DATA_ROOT = tempfile.mkdtemp(prefix="comfyui_studio_tests_")
os.environ["COMFYUI_STUDIO_DATA_DIR"] = os.path.join(_SESSION_DATA_ROOT, "ComfyUIStudio")
os.environ["COMFYUI_LAUNCHER_DATA_DIR"] = os.path.join(_SESSION_DATA_ROOT, "ComfyUILauncher")
# если у разработчика задан свой каталог Imagine — тесты его не трогают
os.environ.pop("IMAGINE_DATA_DIR", None)
atexit.register(shutil.rmtree, _SESSION_DATA_ROOT, ignore_errors=True)


@pytest.fixture(autouse=True)
def _isolate_shared_theme_language_and_qsettings(tmp_path, monkeypatch):
    from comfyui_studio import shared_language, shared_theme

    shared_dir = tmp_path / "shared_appdata"
    shared_dir.mkdir()
    monkeypatch.setattr(shared_theme, "SHARED_DIR", str(shared_dir))
    monkeypatch.setattr(shared_theme, "SHARED_THEME_PATH", str(shared_dir / "theme.json"))
    monkeypatch.setattr(shared_language, "SHARED_DIR", str(shared_dir))
    monkeypatch.setattr(shared_language, "SHARED_LANGUAGE_PATH", str(shared_dir / "language.json"))

    factory = _make_qsettings_factory(tmp_path / "qsettings")
    monkeypatch.setattr("comfyui_studio.themes.base.QSettings", factory)
    monkeypatch.setattr("comfyui_studio.i18n_base.QSettings", factory)
    return factory


@pytest.fixture
def qsettings_factory(_isolate_shared_theme_language_and_qsettings):
    """Та же фабрика QSettings, что подставлена менеджерам: вызов
    ``qsettings_factory(org, app)`` открывает ровно то хранилище, куда
    пишет менеджер этого приложения."""
    return _isolate_shared_theme_language_and_qsettings


def _make_qsettings_factory(directory):
    from PySide6.QtCore import QSettings

    directory.mkdir(parents=True, exist_ok=True)

    def factory(organization, application=""):
        # INI в обычном файле — одинаково на всех платформах и не трогает реестр
        return QSettings(
            str(directory / f"{organization}__{application}.ini"), QSettings.Format.IniFormat
        )

    return factory
