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


def create_window(
    app: QApplication, *, install_exception_hook: bool = True, standalone: bool = True
) -> MainWindow:
    """Готовит логирование/автоочистку и возвращает главное окно
    PromptVault, не создавая свой QApplication и не запуская цикл
    событий -- нужно как при самостоятельном запуске (main() ниже),
    так и из монолитного ComfyUIStudio (см. корневой main.py), где
    QApplication уже создан заранее и общий на все три инструмента
    комплекта.

    standalone передаётся как есть в MainWindow (см. её docstring) --
    единственное, на что влияет здесь: скрывает Restart/Quit в
    SettingsWindow, когда False (см. MainWindow.show_settings())."""

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

    window = MainWindow(standalone=standalone)
    return window


def main():
    app = QApplication(sys.argv)
    window = create_window(app)
    window.show()

    exit_code = app.exec()

    logger.info("Завершение работы PromptVault (код %s)", exit_code)

    sys.exit(exit_code)


def create_settings_window(parent=None):
    """Собирает окно настроек PromptVault (см. ui/settings_window.py,
    класс SettingsWindow), БЕЗ поднятия его MainWindow целиком — не
    сканирует папку, не запускает FolderSync, не строит сетку миниатюр.
    Нужен для вызова из лаунчера напрямую (см. comfyui_studio/launcher/
    ui/settings/promptvault_page.py) — там раньше приходилось открывать
    весь PromptVault только ради его настроек, что и медленнее (полное
    открытие библиотеки), и по сути ни для чего в самом окне настроек
    не требовалось.

    Возможно с тех пор, как выяснилось, что параметр `toolbar` в
    SettingsWindow.__init__ был обязательным лишь формально — фактически
    внутри класса он нигде не читался (см. её докстринг). Реальные
    зависимости SettingsWindow оказались дешёвыми:

    - GenerationRepository() без аргумента открывает ЕДИНУЮ базу
      PromptVault (DB_PATH, не привязана к открытой папке) напрямую;
      она в режиме WAL (см. core/database.py) — второе, независимое
      подключение к тому же файлу безопасно открывать одновременно с
      уже открытым "настоящим" PromptVault, если он в этот момент тоже
      где-то работает в этом же процессе.
    - Конструктор GalleryManager ничего не сканирует и не грузит сам
      по себе (сканирование — только по явному open_folder(), которого
      здесь никто не вызывает).
    - Состояние переключателя семантического поиска и настройки
      производительности/хранения читаются/пишутся через QSettings
      (см. GalleryManager.set_semantic_search_enabled/AppSettings) —
      общие для процесса, а не привязанные к конкретному экземпляру
      GalleryManager, так что правки отсюда корректно видны и уже
      открытому "настоящему" PromptVault, если он есть.
    - ThemeManager/LocalizationManager — тот же паттерн, что и в
      MainWindow.__init__: свой экземпляр на окно, синхронизируются
      между собой через общий QSettings-бэкенд (см.
      comfyui_studio/shared_theme.py, shared_language.py).

    Один осознанный компромисс: смена горячих клавиш здесь корректно
    сохраняется (HotkeyManager пишет прямо в QSettings), но НЕ
    применяется live к уже открытому окну PromptVault, если оно в этот
    момент тоже работает — применение вживую и так было устроено только
    внутри одного и того же окна-владельца toolbar (см.
    MainWindow._on_hotkey_changed), а не глобально между окнами; новое
    значение подхватится при следующем открытии/перезапуске PromptVault.
    Это тот же самый нюанс, что уже был бы и раньше, будь PromptVault
    открыт в двух окнах одновременно — не новое ограничение, просто
    более заметное здесь.

    Автоочистка логов/миниатюр (см. create_window() выше) сюда
    сознательно не перенесена — если в этой сессии ни разу не
    открывался полный PromptVault, автоочистка в этой сессии просто не
    происходит; вреда в этом нет (она никак не влияет на корректность
    работы самих настроек), а тянуть сканирование директорий логов
    только ради окна настроек противоречило бы самой цели этой
    функции — быть лёгкой.

    setup_logging() всё же вызывается (безопасно — logging.basicConfig
    внутри неё не делает ничего повторно, если у корневого логгера уже
    есть хендлеры, см. core/logger.py) — иначе логи из GalleryManager/
    репозитория, открытых этим путём, некуда было бы писать, если до
    этого в сессии полный PromptVault ни разу не открывался.

    Вызывающий код отвечает за закрытие repository (соединения с БД)
    при закрытии возвращённого окна — см. атрибут
    window.standalone_repository и promptvault_page.py, где он
    подключён к destroyed-сигналу окна."""

    from comfyui_studio.promptvault.core.gallery_manager import GalleryManager
    from comfyui_studio.promptvault.core.repository import GenerationRepository
    from comfyui_studio.promptvault.i18n import LocalizationManager
    from comfyui_studio.promptvault.themes.theme_manager import ThemeManager
    from comfyui_studio.promptvault.ui.settings_window import SettingsWindow

    setup_logging()
    logger.info("Открытие окна настроек PromptVault (без полного запуска)")

    repository = GenerationRepository()
    gallery = GalleryManager(repository)
    theme_manager = ThemeManager()
    theme_manager.apply_theme(theme_manager.current_theme())
    localization_manager = LocalizationManager()
    localization_manager.restore_saved_language()

    window = SettingsWindow(
        gallery=gallery,
        theme_manager=theme_manager,
        localization_manager=localization_manager,
        standalone=False,
        parent=parent,
    )
    # см. докстринг выше -- вызывающий код закрывает БД при закрытии окна
    window.standalone_repository = repository

    return window


if __name__ == "__main__":
    main()
