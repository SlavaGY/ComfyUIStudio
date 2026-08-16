"""
Пути и константы конфигурации лаунчера.

Вынесено из comfyui_launcher.py при разбиении god-object'а на модули
(этап 1 дорожной карты). Это самый нижний, "бесзависимый" модуль пакета
launcher.core — остальные core-/ui-/integration-модули опираются на него,
сам он ни от чего внутри пакета не зависит.

ВАЖНО (изменено по сравнению с исходным comfyui_launcher.py, где всё это
было в одном файле и app_base_dir() резолвился относительно самого
comfyui_launcher.py, лежавшего в корне проекта): после переноса в
comfyui_studio/launcher/core/ вычисление корня проекта через __file__
самого этого модуля указывало бы на core/, а не на корень репозитория,
где реально лежат assets/ и tools/. app_base_dir() ниже поднимается на
4 уровня вверх (core -> launcher -> comfyui_studio -> корень проекта),
чтобы поведение осталось прежним.
"""

import os
import sys


def app_base_dir():
    """Папка, где лежит сам лаунчер (корень проекта / папка с exe): рядом
    с ней ожидается tools/ с остальными приложениями комплекта и assets/
    с иконкой. Это НЕ resource_path()/_MEIPASS -- та временная папка
    распаковки PyInstaller существует только пока процесс жив и не
    содержит соседних приложений; тут же нужна папка, где реально на
    диске лежит exe (или исходники при запуске из .py)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    # comfyui_studio/launcher/core/constants.py -> подняться на 4 уровня,
    # чтобы получить корень репозитория (см. пояснение в докстринге модуля).
    return os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )


APP_NAME = "ComfyUI Launcher"
APP_DIR = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")), "ComfyUILauncher"
)
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
WEBENGINE_PROFILE_DIR = os.path.join(APP_DIR, "webengine_profile")
LAUNCH_SCRIPT_TMP = os.path.join(APP_DIR, "_launch_current.bat")
COMFY_LOG_PATH = os.path.join(APP_DIR, "comfyui_last_run.log")
APP_LOG_PATH = os.path.join(APP_DIR, "launcher.log")

DEFAULT_CONFIG = {
    "root_path": "",
    "script": "",
    "port": 8188,
    "disable_auto_launch": True,
    "sync_comfy_theme": False,
}

TOOLS_DIR = os.path.join(app_base_dir(), "tools")

# Корень проекта (папка с exe / с main.py и comfyui_studio/) -- нужен
# отдельным именем, а не только через app_base_dir(), для запуска
# инструментов комплекта как пакетов (`python -m comfyui_studio.<tool>`,
# см. core/comfy_process.py): такой запуск резолвится ОТ КОРНЯ ПРОЕКТА
# (там лежит comfyui_studio/), а не от TOOLS_DIR -- начиная с этапа 2
# дорожной карты (перенос prompt_builder/promptvault под comfyui_studio/)
# исходники инструментов физически там, а не в tools/<subdir>/.
PROJECT_ROOT = app_base_dir()
