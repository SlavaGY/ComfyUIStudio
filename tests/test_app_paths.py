"""Тесты comfyui_studio/app_paths.py — переопределение папки данных.

Зачем это вообще нужно (живой случай 2026-10-03): на машине пользователя
не-администраторский `python.exe` не мог писать никуда в
`C:\\Users\\<пользователь>\\...` (ни `AppData\\Roaming`, ни `AppData\\Local`,
ни `Documents`), при том что `PowerShell`/`cmd` писали туда нормально, а
права на папки были обычные. Последствия для комплекта: молча не
сохранялся `config.json` (его запись глотает исключения), не писались логи,
а запуск падал трейсбеком на файле диагностики. Запись работала в рабочем
каталоге на другом диске, то есть данные достаточно было увести из профиля.

`COMFYUI_STUDIO_DATA_DIR` — единственный штатный способ это сделать,
поэтому проверяем и саму функцию, и то, что РЕАЛЬНЫЕ модули комплекта
берут пути через неё (иначе кто-то снова скопирует старую формулу с
`APPDATA` и переопределение перестанет работать наполовину).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from comfyui_studio import app_paths

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_default_root_is_appdata(monkeypatch):
    monkeypatch.delenv(app_paths.DATA_DIR_ENV, raising=False)
    monkeypatch.setenv("APPDATA", r"C:\Users\Test\AppData\Roaming")

    assert app_paths.data_override() == ""
    assert app_paths.data_root() == r"C:\Users\Test\AppData\Roaming"
    assert app_paths.studio_dir() == os.path.join(
        r"C:\Users\Test\AppData\Roaming", "ComfyUIStudio"
    )
    assert app_paths.launcher_dir() == os.path.join(
        r"C:\Users\Test\AppData\Roaming", "ComfyUILauncher"
    )


def test_override_moves_both_directories(monkeypatch):
    monkeypatch.setenv(app_paths.DATA_DIR_ENV, r"D:\ComfyUIStudioData")

    assert app_paths.data_root() == r"D:\ComfyUIStudioData"
    assert app_paths.studio_dir() == os.path.join(
        r"D:\ComfyUIStudioData", "ComfyUIStudio"
    )
    assert app_paths.launcher_dir() == os.path.join(
        r"D:\ComfyUIStudioData", "ComfyUILauncher"
    )


def test_blank_override_is_ignored(monkeypatch):
    """Пустая/пробельная переменная не должна уводить данные в никуда."""
    monkeypatch.setenv("APPDATA", r"C:\Users\Test\AppData\Roaming")
    monkeypatch.setenv(app_paths.DATA_DIR_ENV, "   ")

    assert app_paths.data_override() == ""
    assert app_paths.data_root() == r"C:\Users\Test\AppData\Roaming"


def test_real_modules_follow_override(tmp_path):
    """Все места, где раньше была своя копия `%APPDATA%\\ComfyUIStudio`,
    должны уехать за переменной.

    Проверяем в отдельном процессе: константы вида `SHARED_DIR`/`APP_DIR`
    вычисляются один раз при импорте своего модуля, поэтому подменить
    переменную в уже импортированном модуле недостаточно.

    Результат пишется в файл, а не читается из stdout: под песочницей DSH
    захват вывода дочернего процесса через pipe запрещён (EPERM), и тест
    падал бы по причине, не связанной с проверяемым поведением.
    """
    out_path = tmp_path / "paths.json"
    script = f"""
import json, pathlib

from comfyui_studio.launcher.core import constants
from comfyui_studio import shared_theme, shared_language, shared_promptgen
from comfyui_studio.remote import device_store, ssh_config_store
from comfyui_studio.imagine.backend import config_store
from comfyui_studio.promptvault import config as pv_config

data = {{
    "launcher": constants.APP_DIR,
    "theme": shared_theme.SHARED_DIR,
    "language": shared_language.SHARED_DIR,
    "promptgen": shared_promptgen.SHARED_DIR,
    "promptgen_logs": shared_promptgen.LOG_DIR,
    "devices": device_store.SHARED_DIR,
    "ssh": ssh_config_store.SHARED_DIR,
    "imagine": str(config_store.DATA_DIR),
    "promptvault": str(pv_config.APP_DATA_DIR),
}}
pathlib.Path(r"{out_path}").write_text(json.dumps(data), encoding="utf-8")
"""

    env = dict(os.environ)
    env[app_paths.DATA_DIR_ENV] = str(tmp_path)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONIOENCODING"] = "utf-8"

    subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        env=env,
        check=True,
    )

    paths = json.loads(out_path.read_text(encoding="utf-8"))
    for name, value in paths.items():
        assert value.startswith(str(tmp_path)), (
            f"{name} остался в профиле ({value}), хотя "
            f"{app_paths.DATA_DIR_ENV} указывает на {tmp_path}"
        )
