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
    # НОВОЕ (imagine-app в комплекте): чем открывать ComfyUI после
    # запуска -- "comfyui" (как раньше, сырой интерфейс ComfyUI) или
    # "imagine" (карточный генератор поверх ComfyUI, см.
    # comfyui_studio/imagine/ и launcher/core/imagine_process.py). См.
    # раздел "Интерфейс" в ui/settings/comfyui_page.py.
    "interface": "comfyui",
    "imagine": {
        "port": 7860,
        # Дев-режим Imagine (вкладки "Стили"/"Категории"/"Всегда
        # LoRA"/"Подключение" редактирования каталога) -- сознательно
        # НЕТ переключателя внутри самого веб-интерфейса Imagine (см.
        # его README/backend/main.py: require_dev_mode()), только
        # здесь, в настройках лаунчера, откуда Imagine и запускается.
        "dev_mode": False,
    },
    # НОВОЕ (этап 4 дорожной карты, "Единое дерево настроек" ->
    # ComfyUI -> Environment): переменные окружения, добавляемые/
    # переопределяемые поверх os.environ для процесса ComfyUI -- см.
    # ComfyUISettingsPage (ui/settings/comfyui_page.py, раздел
    # "Environment") и ComfyProcess.start() (core/comfy_process.py).
    "env_vars": {},
    # уровень логирования КОНСОЛЬНОГО хендлера (см. core/logging_setup.py,
    # set_console_log_level) -- файловый хендлер лаунчера всегда пишет
    # DEBUG независимо от этой настройки, меняется только то, что видно
    # в консоли/выводе процесса. См. ui/settings/advanced_page.py.
    "log_level": "INFO",
    # НОВОЕ (Remote, этап 1 дорожной карты ComfyUIStudio_Remote_Roadmap.md,
    # см. comfyui_studio/remote/, launcher/core/remote_process.py,
    # ui/settings/remote_page.py): удалённый доступ к Studio с телефона
    # в той же локальной сети -- полностью независимый переключатель от
    # "interface" (ComfyUI/Imagine) выше, см. §0.1 дорожной карты.
    # "host" сознательно НЕ хранится здесь -- этап 1 всегда поднимает
    # Remote на 127.0.0.1 (только curl/Postman с того же ПК, см. §1.5);
    # реальный LAN-адрес ("0.0.0.0") появится в UI на этапе 8 ("LAN
    # Release"), когда сценарий "мобильный браузер без приложения"
    # станет частью критерия готовности, а не только внутренним curl.
    "remote": {
        "enabled": False,
        "port": 7861,
        # "127.0.0.1" -- только с этого ПК (по умолчанию, безопасно "из
        # коробки"); "0.0.0.0" -- слушать все сетевые интерфейсы, чтобы
        # телефон в той же LAN мог достучаться (см. чекбокс "Разрешить
        # доступ по локальной сети" в ui/settings/remote_page.py). Это,
        # по сути, кусочек этапа 8 дорожной карты ("LAN Release"),
        # реализованный раньше срока -- понадобился уже на этапе 4 при
        # живом тестировании reverse-proxy с реального телефона.
        "host": "127.0.0.1",
        # Путь к JSON-файлу сервис-аккаунта Firebase -- §Этап 6.5
        # дорожной карты ("Push-уведомления"). None, пока пользователь
        # не выбрал файл в настройках -- см. comfyui_studio/remote/fcm.py,
        # который тихо ничего не отправляет, если это поле пусто (push --
        # опциональная функция, отсутствие Firebase-проекта не должно
        # ничего ломать).
        "fcm_service_account_path": None,
    },
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
