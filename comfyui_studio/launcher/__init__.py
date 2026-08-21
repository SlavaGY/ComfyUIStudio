"""ComfyUI Launcher — запуск, мониторинг и встроенный браузер ComfyUI."""

# MainWindow/create_window/main тянут за собой .ui.launcher_window ->
# .ui.browser_page -> PySide6.QtWebEngineCore -- то есть весь Qt +
# QtWebEngine стек. Ни один реальный вызывающий код (main.py,
# comfyui_studio/prompt_builder/__main__.py, mem_diagnostics.py) не
# использует ЭТОТ re-export -- все они импортируют
# comfyui_studio.launcher.ui.launcher_window /
# comfyui_studio.launcher.core.* / comfyui_studio.launcher.integration.*
# напрямую (см. соответствующие модули). Раньше эта строка была
# обычным eager `from .ui.launcher_window import ...` -- из-за чего
# ЛЮБОЙ импорт из comfyui_studio.launcher.core (в т.ч. чистых,
# Qt-независимых модулей вроде core.comfy_api) сначала выполнял
# __init__.py родительского пакета и тянул QtWebEngineCore, даже если
# сам импортируемый код в нём вообще не нуждался -- см. этап 6
# дорожной карты, tests/launcher/test_comfy_api.py: тесты не
# запускались без полного QtWebEngine (даже "PySide6-Essentials" без
# QtWebEngineCore ломал сам факт `import
# comfyui_studio.launcher.core.comfy_api`), хотя сам comfy_api.py --
# чистый urllib/json, ни одного импорта PySide6.
#
# Ленивый __getattr__ (PEP 562) сохраняет прежний публичный API пакета
# (`from comfyui_studio.launcher import MainWindow` по-прежнему
# работает), но откладывает тяжёлый импорт до первого реального
# обращения к этим именам, а не до любого импорта из-под пакета.
__all__ = ["MainWindow", "create_window", "main"]


def __getattr__(name):
    if name in __all__:
        from .ui.launcher_window import MainWindow, create_window, main

        return {"MainWindow": MainWindow, "create_window": create_window, "main": main}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
