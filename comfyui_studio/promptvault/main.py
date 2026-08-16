import logging
import sys
import traceback

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from comfyui_studio.promptvault.config import ICON_PATH
from comfyui_studio.promptvault.core.logger import cleanup_old_logs, setup_logging
from comfyui_studio.promptvault.core.thumbnails import cleanup_thumbnail_cache
from comfyui_studio.promptvault.settings import AppSettings
from comfyui_studio.promptvault.ui.main_window import MainWindow

logger = logging.getLogger(__name__)


def _install_global_exception_hook() -> None:
    """Ловит любое необработанное исключение, всплывшее из обработчика
    события Qt (клик, таймер и т.п.) — без этого PySide6 в лучшем
    случае молча печатает трейсбек в stderr и продолжает работу в
    неопределённом состоянии, а в худшем роняет процесс без вообще
    какого-либо сообщения пользователю.

    Логирует полный трейсбек и показывает пользователю диалог с общим
    сообщением об ошибке (без деталей трейсбека на экране — они только
    в файле лога) вместо того, чтобы приложение бесследно падало.
    """

    def handle_exception(exc_type, exc_value, exc_traceback):

        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        logger.critical(
            "Необработанное исключение:\n%s",
            "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        )

        try:
            QMessageBox.critical(
                None,
                "PromptVault — unexpected error",
                "Произошла непредвиденная ошибка. Подробности записаны в "
                "лог приложения.\n\n"
                f"{exc_type.__name__}: {exc_value}"
            )
        except Exception:
            # если сам показ диалога упал (например, ещё нет
            # QApplication) — не даём обработчику исключений упасть
            # с собственным исключением
            pass

    sys.excepthook = handle_exception


def create_window(app: QApplication, *, install_exception_hook: bool = True) -> MainWindow:
    """Готовит логирование/автоочистку и возвращает главное окно
    PromptVault, не создавая свой QApplication и не запуская цикл
    событий -- нужно как при самостоятельном запуске (main() ниже),
    так и из монолитного ComfyUIStudio (см. корневой main.py), где
    QApplication уже создан заранее и общий на все три инструмента
    комплекта."""

    setup_logging()
    if install_exception_hook:
        _install_global_exception_hook()

    logger.info("Запуск PromptVault")

    # задача: иконка приложения в панели задач Windows после сборки —
    # ставим иконку на уровне QApplication, а не только на MainWindow
    # (см. MainWindow.__init__/self.setWindowIcon): это то, что Windows
    # использует для диалогов/дочерних окон и что переживает момент до
    # первой отрисовки MainWindow, а не только его самого. Сам путь до
    # icon.png теперь корректно резолвится и внутри сборки PyInstaller
    # (см. подробный комментарий у ICON_PATH в app/config.py).
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))
    else:
        logger.warning("Иконка приложения не найдена: %s", ICON_PATH)

    # автоочистка старых миниатюр/логов (задача 3.5) — не должна мешать
    # запуску приложения, даже если что-то пойдёт не так. Пороги
    # настраиваются пользователем через SettingsWindow (см.
    # app/settings.py) — читаются здесь напрямую из QSettings, т.к. на
    # этом этапе GalleryManager/MainWindow ещё не созданы.
    try:
        app_settings = AppSettings()

        cleanup_old_logs(
            max_age_days=app_settings.log_max_age_days(),
            max_total_bytes=app_settings.log_dir_max_mb() * 1024 * 1024,
        )
        cleanup_thumbnail_cache(
            max_age_days=app_settings.thumbnail_max_age_days(),
            max_total_bytes=app_settings.thumbnail_cache_max_mb() * 1024 * 1024,
        )
    except Exception:
        logger.exception("Автоочистка при старте завершилась с ошибкой")

    window = MainWindow()
    return window


def main():
    app = QApplication(sys.argv)
    window = create_window(app)
    window.show()

    exit_code = app.exec()

    logger.info("Завершение работы PromptVault (код %s)", exit_code)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
