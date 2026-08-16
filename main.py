"""
ComfyUI Studio — монолитная точка входа
=========================================
Раньше три инструмента комплекта (ComfyUI Launcher, Character/Prompt
Builder Config Editor, PromptVault) были самостоятельными приложениями:
у каждого свой QApplication, свой процесс, а лаунчер открывал два других
через subprocess.Popen (см. launch_external_app() в comfyui_launcher.py).

Этот файл объединяет все три в ОДИН процесс с ОДНИМ QApplication:
у каждого инструмента по-прежнему своё отдельное окно (QMainWindow),
но окна живут в общем цикле событий одного и того же процесса, а не
трёх разных. Практическая причина: единая сборка в один exe (см.
build/ComfyUIStudio.spec и build_exe.bat), без разгона отдельных
подпроцессов и без риска рассинхронизации версий инструментов при
обновлении одного из них.

Как это работает:
  - PromptVault (tools/promptvault/app) и Prompt Builder (tools/prompt_builder)
    — оба обычные Python-пакеты (app и prompt_builder соответственно, см. их
    __init__.py) с внутренними импортами вида `from app.xxx import ...` /
    `from prompt_builder.xxx import ...`. На sys.path добавляются папки-
    родители пакетов (tools/promptvault и tools/), а не сами пакеты — это
    даёт PyInstaller статически проследить весь граф импортов каждого
    инструмента и скомпилировать его в PYZ вместе с остальной сборкой, без
    сырых .py-исходников, лежащих отдельно в _internal.
  - main.py каждого инструмента (comfyui_launcher.py, tools/prompt_builder/
    main.py, tools/promptvault/app/main.py) содержит функцию
    create_window(...), которая строит окно, не создавая свой
    QApplication и не запуская app.exec() — это и переиспользуется
    здесь.
  - Лаунчер остаётся "главным" окном комплекта (как и раньше — из него
    открываются два других инструмента). Раньше кнопки "Запустить" в
    разделе "Другие инструменты" запускали subprocess; теперь
    comfyui_launcher.register_in_process_app() подставляет вместо
    подпроцесса фабрику, создающую окно ЭТОГО ЖЕ процесса (см.
    IN_PROCESS_WINDOW_FACTORIES в comfyui_launcher.py) — снаружи это
    выглядит так же (отдельное окно, кнопка "Запустить"), но внутри
    это window.show(), а не новый процесс.
  - Тема и язык по-прежнему общие на весь комплект через shared_theme.py
    / shared_language.py — только теперь это буквально один и тот же
    объект в памяти для всех трёх инструментов вместо трёх процессов,
    следящих за одним файлом на диске через QFileSystemWatcher.

Запуск из исходников:  python main.py
Сборка exe:             см. build_exe.bat (одна сборка на весь комплект)
"""

import os
import sys
from pathlib import Path


ROOT_DIR = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
TOOLS_DIR = ROOT_DIR / "tools"
PROMPTVAULT_DIR = TOOLS_DIR / "promptvault"

# tools/prompt_builder — обычный пакет `prompt_builder` (см. его
# __init__.py), поэтому на sys.path нужен TOOLS_DIR (его родитель), а не
# сама папка инструмента — ровно так же, как PROMPTVAULT_DIR добавляется
# не как "tools/promptvault", а именно как папка, ИЗ которой резолвится
# пакет `app`. Раньше PROMPT_BUILDER_DIR добавлялась в sys.path напрямую
# и main.py инструмента подгружался через importlib.util.spec_from_file_location
# по пути на диске — из-за этого PyInstaller не мог статически проследить
# его импорты и приходилось класть исходники .py как есть в datas
# (см. историю ComfyUIStudio.spec) — они распаковывались в
# _internal/tools/prompt_builder рядом со скомпилированной остальной
# сборкой. Обычный `import prompt_builder.main`, как у PromptVault
# (`from app.main import ...`), даёт статическому анализатору
# PyInstaller увидеть весь граф импортов инструмента и скомпилировать
# его в PYZ вместе со всем остальным — без сырых исходников в сборке.
for _p in (PROMPTVAULT_DIR, TOOLS_DIR, ROOT_DIR):
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)


def main():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    # см. комментарий у того же вызова в исходном comfyui_launcher.py —
    # нужно ДО создания QApplication, общий на весь процесс.
    if hasattr(Qt, "AA_ShareOpenGLContexts"):
        QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)

    app = QApplication(sys.argv)
    app.setApplicationName("ComfyUI Studio")
    # Как и в исходном лаунчере: закрытие ОДНОГО окна (например,
    # PromptVault) не должно завершать весь процесс — комплект живёт,
    # пока не закрыт лаунчер или не выбран "Выход" в его трее.
    app.setQuitOnLastWindowClosed(False)

    import comfyui_launcher
    from prompt_builder.main import create_window as create_prompt_builder_window
    from app.main import create_window as create_promptvault_window

    comfyui_launcher.register_in_process_app(
        "prompt_builder",
        lambda: create_prompt_builder_window(),
    )
    comfyui_launcher.register_in_process_app(
        "promptvault",
        lambda: create_promptvault_window(app),
    )

    launcher_window = comfyui_launcher.create_window(app)
    launcher_window.show()

    exit_code = app.exec()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
