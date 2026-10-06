"""Тесты comfyui_studio/app_paths.py (этап R2).

Значения по умолчанию должны остаться теми же, что были до R2 (иначе
пользователи «потеряют» данные и спаренные телефоны), а переопределение
через переменные окружения — действовать на ВСЕ модули-потребители.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from comfyui_studio import app_paths

PACKAGE_DIR = Path(app_paths.__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent

_ENV_NAMES = (
    app_paths.STUDIO_DATA_DIR_ENV,
    app_paths.LAUNCHER_DATA_DIR_ENV,
    app_paths.IMAGINE_DATA_DIR_ENV,
)


@pytest.fixture
def clean_env(monkeypatch):
    """Без переопределений (корневой conftest задаёт их на всю сессию)."""
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


class TestDefaults:
    def test_appdata_based_defaults_are_unchanged(self, clean_env, tmp_path):
        clean_env.setenv("APPDATA", str(tmp_path))
        assert app_paths.studio_data_dir() == str(tmp_path / "ComfyUIStudio")
        assert app_paths.launcher_data_dir() == str(tmp_path / "ComfyUILauncher")
        assert app_paths.imagine_data_dir() == str(tmp_path / "ComfyUIStudio" / "imagine")

    def test_falls_back_to_home_without_appdata(self, clean_env):
        clean_env.delenv("APPDATA", raising=False)
        home = os.path.expanduser("~")
        assert app_paths.appdata_root() == home
        assert app_paths.studio_data_dir() == os.path.join(home, "ComfyUIStudio")
        assert app_paths.launcher_data_dir() == os.path.join(home, "ComfyUILauncher")

    def test_two_data_dirs_are_not_merged(self, clean_env, tmp_path):
        """Слияние ComfyUIStudio и ComfyUILauncher — отдельное решение
        (миграция данных), в R2 папки остаются двумя."""
        clean_env.setenv("APPDATA", str(tmp_path))
        assert app_paths.studio_data_dir() != app_paths.launcher_data_dir()


class TestOverrides:
    def test_studio_override(self, clean_env, tmp_path):
        clean_env.setenv(app_paths.STUDIO_DATA_DIR_ENV, str(tmp_path / "x"))
        assert app_paths.studio_data_dir() == str(tmp_path / "x")

    def test_studio_override_does_not_touch_launcher_dir(self, clean_env, tmp_path):
        clean_env.setenv("APPDATA", str(tmp_path / "ad"))
        clean_env.setenv(app_paths.STUDIO_DATA_DIR_ENV, str(tmp_path / "x"))
        assert app_paths.launcher_data_dir() == str(tmp_path / "ad" / "ComfyUILauncher")

    def test_launcher_override(self, clean_env, tmp_path):
        clean_env.setenv(app_paths.LAUNCHER_DATA_DIR_ENV, str(tmp_path / "l"))
        assert app_paths.launcher_data_dir() == str(tmp_path / "l")

    def test_imagine_follows_studio_override(self, clean_env, tmp_path):
        clean_env.setenv(app_paths.STUDIO_DATA_DIR_ENV, str(tmp_path / "x"))
        assert app_paths.imagine_data_dir() == str(tmp_path / "x" / "imagine")

    def test_imagine_own_override_wins(self, clean_env, tmp_path):
        clean_env.setenv(app_paths.STUDIO_DATA_DIR_ENV, str(tmp_path / "x"))
        clean_env.setenv(app_paths.IMAGINE_DATA_DIR_ENV, str(tmp_path / "img"))
        assert app_paths.imagine_data_dir() == str(tmp_path / "img")

    @pytest.mark.parametrize("name", _ENV_NAMES)
    def test_empty_value_means_not_set(self, clean_env, tmp_path, name):
        clean_env.setenv("APPDATA", str(tmp_path))
        clean_env.setenv(name, "")
        assert app_paths.studio_data_dir() == str(tmp_path / "ComfyUIStudio")
        assert app_paths.launcher_data_dir() == str(tmp_path / "ComfyUILauncher")
        assert app_paths.imagine_data_dir() == str(tmp_path / "ComfyUIStudio" / "imagine")

    def test_value_is_read_at_call_time(self, clean_env, tmp_path):
        clean_env.setenv(app_paths.STUDIO_DATA_DIR_ENV, str(tmp_path / "a"))
        first = app_paths.studio_data_dir()
        clean_env.setenv(app_paths.STUDIO_DATA_DIR_ENV, str(tmp_path / "b"))
        assert (first, app_paths.studio_data_dir()) == (str(tmp_path / "a"), str(tmp_path / "b"))


def _run_python(code: str, env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in _ENV_NAMES}
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


class TestEndToEnd:
    def test_every_consumer_follows_the_overrides(self, tmp_path):
        """Свежий процесс с переменными окружения: ВСЕ семь потребителей
        (общие файлы, Remote, Imagine, лаунчер) оказываются в заданных
        папках — ни один не остался на прежнем %APPDATA%."""
        studio, launcher = tmp_path / "studio", tmp_path / "launcher"
        result = _run_python(
            """
            import json
            from comfyui_studio import shared_theme, shared_language, shared_promptgen
            from comfyui_studio.remote import device_store, ssh_config_store
            from comfyui_studio.imagine.backend import config_store
            from comfyui_studio.launcher.core import constants
            print(json.dumps({
                "theme": shared_theme.SHARED_THEME_PATH,
                "language": shared_language.SHARED_LANGUAGE_PATH,
                "promptgen": shared_promptgen.SHARED_PROMPTGEN_PATH,
                "promptgen_logs": shared_promptgen.LOG_DIR,
                "devices": device_store.DEVICE_STORE_PATH,
                "ssh": ssh_config_store.SSH_CONFIG_STORE_PATH,
                "imagine": str(config_store.CONFIG_PATH),
                "launcher_config": constants.CONFIG_PATH,
                "launcher_log": constants.APP_LOG_PATH,
                "webengine": constants.WEBENGINE_PROFILE_DIR,
            }))
            """,
            {
                "COMFYUI_STUDIO_DATA_DIR": str(studio),
                "COMFYUI_LAUNCHER_DATA_DIR": str(launcher),
                "APPDATA": str(tmp_path / "must_not_be_used"),
            },
        )
        assert result.returncode == 0, result.stderr
        paths = json.loads(result.stdout.strip().splitlines()[-1])

        studio_keys = ("theme", "language", "promptgen", "promptgen_logs", "devices", "ssh")
        for key in studio_keys:
            assert studio in Path(paths[key]).parents or Path(paths[key]) == studio, (
                key,
                paths[key],
            )
        assert Path(paths["imagine"]).parent == studio / "imagine"
        for key in ("launcher_config", "launcher_log", "webengine"):
            assert Path(paths[key]).parent == launcher, (key, paths[key])
        assert "must_not_be_used" not in json.dumps(paths)

    def test_defaults_without_overrides_match_pre_r2_layout(self, tmp_path):
        """Без переменных окружения расположение файлов точно прежнее."""
        result = _run_python(
            """
            import json
            from comfyui_studio import shared_theme
            from comfyui_studio.remote import device_store
            from comfyui_studio.launcher.core import constants
            print(json.dumps([shared_theme.SHARED_THEME_PATH, device_store.DEVICE_STORE_PATH,
                              constants.CONFIG_PATH]))
            """,
            {"APPDATA": str(tmp_path)},
        )
        assert result.returncode == 0, result.stderr
        theme, devices, config = json.loads(result.stdout.strip().splitlines()[-1])
        assert Path(theme) == tmp_path / "ComfyUIStudio" / "theme.json"
        assert Path(devices) == tmp_path / "ComfyUIStudio" / "remote_devices.json"
        assert Path(config) == tmp_path / "ComfyUILauncher" / "config.json"

    def test_app_paths_does_not_import_qt(self):
        """Модуль импортируют процессы Remote и Imagine, где Qt нет."""
        result = _run_python(
            """
            import sys
            import comfyui_studio.app_paths
            print(any(name.startswith("PySide6") for name in sys.modules))
            """,
            {},
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "False"


class TestNoScatteredAppdata:
    """Охранный тест критерия R2: путь к %APPDATA% собирается только в
    app_paths.py. Единственное допустимое исключение — кэш CUDA-ядер
    драйвера NVIDIA (чужая папка, не данные приложения)."""

    _ALLOWED = {
        # (файл относительно comfyui_studio, подстрока допустимой строки)
        ("app_paths.py", None),
        ("imagine/backend/promptgen_diag.py", 'appdata = os.environ.get("APPDATA")'),
    }

    def test_environ_appdata_only_in_app_paths(self):
        pattern = re.compile(r"""environ(\.get\(|\[)\s*['"]APPDATA['"]""")
        offenders = []
        for path in PACKAGE_DIR.rglob("*.py"):
            rel = path.relative_to(PACKAGE_DIR).as_posix()
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#") or not pattern.search(line):
                    continue
                allowed = any(
                    rel == name and (marker is None or marker in stripped)
                    for name, marker in self._ALLOWED
                )
                if not allowed:
                    offenders.append(f"{rel}:{number}: {stripped}")
        assert offenders == []
