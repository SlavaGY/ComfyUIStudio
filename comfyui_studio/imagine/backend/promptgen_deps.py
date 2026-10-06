"""
promptgen_deps.py
Необязательные зависимости генератора промптов от остального ComfyUIStudio.

Вынесено из promptgen.py на этапе R10 без изменения логики. Если Imagine
запущен как самостоятельный инструмент (без остального комплекта), обе
переменные равны None -- генератор сообщает available=False, и кнопка в
интерфейсе не показывается.
"""

from __future__ import annotations

try:
    # Доступно только внутри ComfyUIStudio. Если Imagine запущен как
    # самостоятельный инструмент (без остального комплекта), генератор
    # просто сообщает available=False, и кнопка в интерфейсе не
    # показывается -- как и с shared_theme/shared_language в main.py.
    from comfyui_studio import shared_promptgen
except ImportError:  # pragma: no cover - Imagine запущен отдельно от Studio
    shared_promptgen = None

try:
    from comfyui_studio import promptgen_history
except ImportError:  # pragma: no cover - то же самое: история есть только внутри Studio
    promptgen_history = None
