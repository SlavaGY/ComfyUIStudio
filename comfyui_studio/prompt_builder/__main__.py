"""Точка входа для standalone-запуска Prompt Builder как пакета:

    python -m comfyui_studio.prompt_builder

Используется, когда лаунчер запущен САМ ПО СЕБЕ (не как часть
монолитного ComfyUIStudio, см. корневой main.py) и пользователь жмёт
"Запустить" у Prompt Builder — тогда comfyui_studio.launcher.core.
comfy_process.resolve_external_launch() запускает именно эту команду
отдельным процессом (см. EXTERNAL_APPS там же). До этапа 2 дорожной
карты рефакторинга (перенос исходников под общее пространство имён
comfyui_studio/) это был `python main.py`, запускавшийся с рабочей
папкой tools/prompt_builder/ — заменён на единообразный со всеми
инструментами комплекта запуск через `-m`.
"""

from comfyui_studio.prompt_builder.main import main

if __name__ == "__main__":
    main()
