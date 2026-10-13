"""
ComfyUI Studio -- точка входа из исходников (`python main.py`) и для сборки.

Вся логика запуска -- в comfyui_studio/app.py (там же подробное описание
монолита: один процесс, один QApplication, режимы подпроцесса Imagine /
Remote / Imagine Pony). Этот файл оставлен тонкой обёрткой: на него
указывают ComfyUIStudio.spec (Analysis), автозапуск и перезапуск из
исходников (launcher/core/autostart.py, launcher/ui/launcher_window.py).
Сборка exe -- build_exe.bat.
"""

from comfyui_studio.app import run

if __name__ == "__main__":
    run()
