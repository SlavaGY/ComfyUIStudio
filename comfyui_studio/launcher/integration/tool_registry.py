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

# Необязательный callback без аргументов на subdir -- вызывается ПОСЛЕ
# того, как соответствующее окно уничтожено (см. _on_child_window_destroyed
# в comfyui_studio/launcher/ui/settings_page.py), для освобождения
# module-level состояния, которое переживает уничтожение самого окна.
#
# Понадобился из-за PromptVault: WA_DeleteOnClose в
# _open_in_process_window уничтожает C++-объект окна и освобождает то,
# что держал ОН, но загруженная модель эмбеддингов (torch/
# sentence-transformers, до ~1.3 ГБ в зависимости от выбранной модели)
# кешируется в module-level `_model` в comfyui_studio/promptvault/
# core/embedding.py -- она не принадлежит окну и не была бы освобождена
# закрытием только самого окна. См. embedding.unload_model() и то, как
# main.py передаёт его сюда через on_close при регистрации PromptVault.
ON_CLOSE_CALLBACKS = {}


def register_in_process_app(subdir, factory, on_close=None):
    """factory: сallable без аргументов, возвращающий готовое (но ещё не
    показанное) QWidget/QMainWindow -- см. create_window() в
    comfyui_studio/prompt_builder/main.py и
    comfyui_studio/promptvault/main.py.

    on_close: необязательный callable без аргументов, вызывается после
    того, как окно этого инструмента было закрыто и уничтожено (см.
    ON_CLOSE_CALLBACKS выше) -- для освобождения module-level кешей,
    которые переживают уничтожение самого окна и поэтому не считаются
    Qt-объектом (WA_DeleteOnClose их не затронет).
    """
    IN_PROCESS_WINDOW_FACTORIES[subdir] = factory
    if on_close is not None:
        ON_CLOSE_CALLBACKS[subdir] = on_close
