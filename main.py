"""
ComfyUI Studio — монолитная точка входа
=========================================
Раньше три инструмента комплекта (ComfyUI Launcher, Character/Prompt
Builder Config Editor, PromptVault) были самостоятельными приложениями:
у каждого свой QApplication, свой процесс, а лаунчер открывал два других
через subprocess.Popen (см. launch_external_app() в
comfyui_studio/launcher/core/comfy_process.py).

Этот файл объединяет все три в ОДИН процесс с ОДНИМ QApplication:
у каждого инструмента по-прежнему своё отдельное окно (QMainWindow),
но окна живут в общем цикле событий одного и того же процесса, а не
трёх разных. Практическая причина: единая сборка в один exe (см.
ComfyUIStudio-core.spec / ComfyUIStudio-full.spec и build_exe.bat), без
разгона отдельных подпроцессов и без риска рассинхронизации версий
инструментов при обновлении одного из них.

Как это работает:
  - Все три инструмента комплекта (ComfyUI Launcher, Prompt Builder,
    PromptVault) теперь лежат под общим пакетом comfyui_studio/ —
    comfyui_studio/launcher, comfyui_studio/prompt_builder,
    comfyui_studio/promptvault (этапы 1—2 дорожной карты рефакторинга;
    PromptVault раньше был пакетом `app`, Prompt Builder раньше жил
    прямо в tools/prompt_builder — оба каталога под tools/ с этапа 2
    больше не импортируются, там остались только служебные файлы сборки
    отдельных standalone-exe, см. README). Импортируются одинаково:
    `from comfyui_studio.launcher... / .prompt_builder... / .promptvault...`
    — без разницы в стиле между тремя инструментами, которая раньше
    была тут (`app.xxx` у одного, `prompt_builder.xxx` у другого).
  - main.py каждого инструмента (comfyui_studio/launcher/ui/launcher_window.py,
    comfyui_studio/prompt_builder/main.py, comfyui_studio/promptvault/main.py)
    содержит функцию create_window(...), которая строит окно, не создавая
    свой QApplication и не запуская app.exec() — это и переиспользуется
    здесь.
  - Лаунчер остаётся "главным" окном комплекта (как и раньше — из него
    открываются два других инструмента). Раньше кнопки "Запустить" в
    разделе "Другие инструменты" запускали subprocess; теперь
    register_in_process_app() (comfyui_studio.launcher.integration.tool_registry)
    подставляет вместо подпроцесса фабрику, создающую окно ЭТОГО ЖЕ
    процесса (см. IN_PROCESS_WINDOW_FACTORIES там же) — снаружи это
    выглядит так же (отдельное окно, кнопка "Запустить"), но внутри
    это window.show(), а не новый процесс.
  - Тема и язык по-прежнему общие на весь комплект через
    comfyui_studio/shared_theme.py / comfyui_studio/shared_language.py
    — только теперь это буквально один и тот же объект в памяти для
    всех трёх инструментов вместо трёх процессов, следящих за одним
    файлом на диске через QFileSystemWatcher.

Запуск из исходников:  python main.py
Сборка exe:             см. build_exe.bat (одна сборка на весь комплект)
"""

import os
import sys


# Отключаем Windows Native Window Occlusion в Chromium/QtWebEngine —
# подтверждённая причина мигания встроенного интерфейса ComfyUI при
# панорамировании графа (см. журнал переписки stage4_4 → stage4_5:
# анти-фликер CSS-инъекция не помогла, а этот флаг убрал баг). Проблема
# в том, что Chromium периодически "думает", что окно WebEngineView
# перекрыто/неактивно из-за соседних нативных Qt-виджетов в том же
# топ-баре (BrowserPage.top_bar — адрес, ResourceBar с таймером,
# кнопки), и приостанавливает/пересобирает композитинг — это и даёт
# видимое моргание при интенсивной перерисовке (панорамирование графа).
# Ставится через os.environ (а не через переменную PowerShell/cmd,
# которую легко забыть выставить или выставить не в том синтаксисе —
# как раз и произошло при диагностике), чтобы работало из коробки при
# любом способе запуска (python main.py, собранный exe, IDE).
# ВАЖНО: должно стоять до создания QApplication/QWebEngineProfile —
# Chromium читает QTWEBENGINE_CHROMIUM_FLAGS только при старте процесса.
_EXTRA_CHROMIUM_FLAGS = "--disable-features=CalculateNativeWinOcclusion"
_existing_flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
if _EXTRA_CHROMIUM_FLAGS not in _existing_flags:
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
        f"{_existing_flags} {_EXTRA_CHROMIUM_FLAGS}".strip()
    )


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

    # Все три инструмента комплекта теперь под общим пространством имён
    # comfyui_studio (этап 1 — разбиение comfyui_launcher.py, этап 2 —
    # перенос prompt_builder/promptvault под comfyui_studio, см. дорожную
    # карту рефакторинга). comfyui_studio резолвится как обычный пакет от
    # корня проекта, уже присутствующего в sys.path при `python main.py`
    # — отдельный sys.path-хак (PROMPTVAULT_DIR/TOOLS_DIR/ROOT_DIR),
    # нужный только для старых пакетов app/prompt_builder под tools/, был
    # удалён на этапе 5 дорожной карты (cleanup).
    from comfyui_studio.launcher.integration.tool_registry import register_in_process_app
    from comfyui_studio.launcher.ui.launcher_window import create_window as create_launcher_window
    from comfyui_studio.prompt_builder.main import create_window as create_prompt_builder_window
    from comfyui_studio.promptvault.main import create_window as create_promptvault_window

    register_in_process_app(
        "prompt_builder",
        lambda: create_prompt_builder_window(),
    )
    register_in_process_app(
        "promptvault",
        # standalone=False -- PromptVault делит этот процесс/QApplication
        # с лаунчером (и, если открыт, Prompt Builder). Скрывает
        # Restart/Quit в его собственных настройках (см.
        # MainWindow(standalone=...) и SettingsWindow(standalone=...) в
        # comfyui_studio/promptvault/ui/) -- иначе self-restart PromptVault
        # через os.execv() заменил бы ВЕСЬ процесс (включая лаунчер и
        # управление ComfyUI) одним только PromptVault. Studio-wide
        # аналог -- см. AdvancedSettingsPage ("Application") в
        # comfyui_studio/launcher/ui/settings/advanced_page.py.
        lambda: create_promptvault_window(app, standalone=False),
    )

    launcher_window = create_launcher_window(app)
    launcher_window.show()

    exit_code = app.exec()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
