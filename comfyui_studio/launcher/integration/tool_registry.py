"""
Реестр окон "монолитного" режима (ComfyUIStudio).

Вынесено из comfyui_launcher.py (этап 1 дорожной карты). Когда лаунчер
запущен как часть общего однопроцессного приложения (см. корневой
main.py), остальные инструменты комплекта открываются как окна ЭТОГО ЖЕ
процесса, а не отдельные подпроцессы -- корневой main.py регистрирует
здесь фабрику окна для каждого app.subdir через register_in_process_app()
ДО того, как показывается это окно лаунчера. Если фабрика для данного
subdir не зарегистрирована (лаунчер запущен сам по себе), поведение не
меняется: core.comfy_process.launch_external_app по-прежнему пробует
отдельный процесс/exe.
"""

IN_PROCESS_WINDOW_FACTORIES = {}


def register_in_process_app(subdir, factory):
    """factory: сallable без аргументов, возвращающий готовое (но ещё не
    показанное) QWidget/QMainWindow -- см. create_window() в
    tools/prompt_builder/main.py и tools/promptvault/app/main.py."""
    IN_PROCESS_WINDOW_FACTORIES[subdir] = factory



