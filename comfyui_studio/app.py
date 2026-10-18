"""Точка входа ComfyUI Studio с одним QApplication и окнами инструментов.

Фабрики create_window строят окна Launcher, Prompt Builder и PromptVault
без собственных циклов событий. register_in_process_app позволяет лаунчеру
открывать эти окна в общем процессе. Тема и язык синхронизируются через
общие модули комплекта.

Запуск: python main.py, python -m comfyui_studio или comfyui-studio.
Упаковка: ComfyUIStudio.spec и build_exe.bat. Корневой main.py служит
обёрткой, в том числе для режимов подпроцессов собранного приложения."""

import os
import sys

# Chromium может ошибочно считать WebEngineView перекрытым соседними
# нативными Qt-виджетами и приостанавливать композитинг. Отключение
# Native Window Occlusion предотвращает мерцание при панорамировании.
# Флаг задаётся до QApplication/QWebEngineProfile: Chromium читает
# QTWEBENGINE_CHROMIUM_FLAGS при запуске процесса.
_EXTRA_CHROMIUM_FLAGS = "--disable-features=CalculateNativeWinOcclusion"
_existing_flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
if _EXTRA_CHROMIUM_FLAGS not in _existing_flags:
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
        f"{_existing_flags} {_EXTRA_CHROMIUM_FLAGS}".strip()
    )


def main():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from comfyui_studio.mem_diagnostics import log_memory

    log_memory("старт процесса, до QApplication")

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

    log_memory("QApplication создан")

    # Окна инструментов создаются фабриками в общем QApplication.
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

    log_memory("окно лаунчера показано (перед app.exec())")

    exit_code = app.exec()
    sys.exit(exit_code)


def dispatch_subprocess_mode():
    """Режим подпроцесса по скрытому флагу sys.argv[1] (Imagine / Remote /
    Imagine Pony). Если флаг совпал -- запускает соответствующий __main__
    и завершает процесс через sys.exit(0); при обычном запуске ничего не
    делает. Вызывать нужно ДО Qt-импортов и создания QApplication (см.
    комментарии ниже, перенесены из корневого main.py без изменений).
    """
    # Диспетчеризация в режим подпроцесса -- для Imagine (см.
    # comfyui_studio/imagine/, launcher/core/imagine_process.py): из
    # исходников лаунчер спавнит `python -m comfyui_studio.imagine`
    # (отдельный процесс системного python, эта проверка не сработает),
    # а из собранного exe -- сам себя же со скрытым IMAGINE_CLI_FLAG,
    # ловится здесь до QApplication и до любых Qt-импортов, максимально
    # рано -- Imagine's backend не нуждается в Qt вообще, тянуть его в
    # подпроцесс было бы лишней памятью поверх PySide6. У Imagine есть
    # свои аргументы командной строки (--host/--port/--comfy-host/
    # --comfy-port/--dev) -- они передаются дальше как есть,
    # __main__.main() сам их разбирает. Тот же принцип, что решает
    # multiprocessing.freeze_support() для обычного stdlib-модуля
    # multiprocessing во frozen-сборках.
    if len(sys.argv) > 1:
        from comfyui_studio.imagine.__main__ import IMAGINE_CLI_FLAG
        if sys.argv[1] == IMAGINE_CLI_FLAG:
            from comfyui_studio.imagine.__main__ import main as imagine_main
            imagine_main(sys.argv[2:])
            sys.exit(0)

    # Собранный exe запускает Remote через скрытый флаг. Диспетчеризация
    # выполняется до QApplication и Qt-импортов, аргументы передаются
    # в remote.__main__.main без изменения.
    if len(sys.argv) > 1:
        from comfyui_studio.remote.__main__ import REMOTE_CLI_FLAG
        if sys.argv[1] == REMOTE_CLI_FLAG:
            from comfyui_studio.remote.__main__ import main as remote_main
            remote_main(sys.argv[2:])
            sys.exit(0)

    # Imagine Pony (comfyui_studio/imagine_pony/, launcher/core/
    # imagine_pony_process.py) -- тот же приём self-exec со скрытым флагом;
    # свои аргументы (--host/--port/--comfy-host/--comfy-port) разбирает
    # сам __main__.main().
    if len(sys.argv) > 1:
        from comfyui_studio.imagine_pony.__main__ import IMAGINE_PONY_CLI_FLAG
        if sys.argv[1] == IMAGINE_PONY_CLI_FLAG:
            from comfyui_studio.imagine_pony.__main__ import main as imagine_pony_main
            imagine_pony_main(sys.argv[2:])
            sys.exit(0)


def run():
    """Точка входа Studio: сначала режимы подпроцесса, затем обычный запуск."""
    dispatch_subprocess_mode()
    main()
