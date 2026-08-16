"""Точка входа для standalone-запуска PromptVault как пакета:

    python -m comfyui_studio.promptvault

Используется, когда лаунчер запущен САМ ПО СЕБЕ (не как часть
монолитного ComfyUIStudio, см. корневой main.py) и пользователь жмёт
"Запустить" у PromptVault — тогда comfyui_studio.launcher.core.
comfy_process.resolve_external_launch() запускает именно эту команду
отдельным процессом (см. EXTERNAL_APPS там же). До этапа 2 дорожной
карты рефакторинга (перенос исходников под общее пространство имён
comfyui_studio/, тогда пакет назывался `app`) команда была `python -m
app.main` — теперь единообразна с остальными инструментами комплекта:
`-m <пакет>`, без отдельного модуля main внутри команды.
"""

from comfyui_studio.promptvault.main import main

if __name__ == "__main__":
    main()
