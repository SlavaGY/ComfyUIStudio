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
# Общий механизм: WA_DeleteOnClose в _open_in_process_window уничтожает
# C++-объект окна и освобождает то, что держал ОН, но module-level
# состояние инструмента (кеши, синглтоны и т.п.) не принадлежит окну и
# не было бы освобождено закрытием только самого окна -- для этого и
# нужен этот словарь. Сейчас ни один зарегистрированный инструмент
# такой callback не передаёт (register_in_process_app вызывается без
# on_close), но сам механизм остаётся на будущее.
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
