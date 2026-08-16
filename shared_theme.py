"""
shared_theme.py
================
Общее хранилище выбранной темы оформления для всех приложений комплекта
(ComfyUI Launcher, Character/Prompt Builder Config Editor, PromptVault).

Раньше каждое приложение хранило тему только в своих собственных
QSettings (отдельный раздел реестра на приложение), поэтому смена темы
в одном инструменте никак не влияла на остальные. Этот модуль добавляет
общий файл %APPDATA%\\ComfyUIStudio\\theme.json (вне зависимости от того,
через какое приложение тема была выбрана), который читается при старте
и перезаписывается при каждом переключении темы — так все три
приложения сходятся к одной и той же теме.

Кроме того, SharedThemeWatcher следит за этим файлом через
QFileSystemWatcher, так что смена темы применяется СРАЗУ во всех уже
запущенных приложениях комплекта, а не только при следующем их запуске.

Модуль сознательно не зависит от Qt в своей "файловой" части (read/write
работают из любого кода), а Qt-зависимая часть (SharedThemeWatcher)
изолирована и просто не создаётся, если PySide6 недоступен.
"""
import json
import os

SHARED_DIR = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")), "ComfyUIStudio"
)
SHARED_THEME_PATH = os.path.join(SHARED_DIR, "theme.json")


def read_shared_theme():
    """Возвращает имя темы из общего файла, либо None, если файла нет,
    он повреждён или недоступен."""
    try:
        with open(SHARED_THEME_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        name = data.get("theme")
        return name if isinstance(name, str) and name else None
    except Exception:
        return None


def write_shared_theme(theme_name):
    """Сохраняет имя темы в общий файл, чтобы её подхватили остальные
    приложения комплекта. Ошибки записи (например, нет прав на APPDATA)
    намеренно проглатываются — синхронизация темы не должна ронять
    приложение или мешать её локальному применению."""
    try:
        os.makedirs(SHARED_DIR, exist_ok=True)
        with open(SHARED_THEME_PATH, "w", encoding="utf-8") as f:
            json.dump({"theme": theme_name}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


try:
    from PySide6.QtCore import QFileSystemWatcher, QObject, Signal
except Exception:  # pragma: no cover - модуль остаётся полезен и без Qt
    QFileSystemWatcher = None
    QObject = object
    Signal = None


if QFileSystemWatcher is not None:

    class SharedThemeWatcher(QObject):
        """Следит за общим файлом темы и уведомляет (сигналом
        theme_changed), когда тема была изменена ДРУГИМ процессом
        комплекта, пока текущее приложение уже открыто — живая
        синхронизация темы между всеми запущенными окнами, а не только
        "на старте" каждого приложения.

        QFileSystemWatcher умеет терять слежение за конкретным файлом,
        если тот был пересоздан (некоторые редакторы и ФС так делают при
        записи) — поэтому вдобавок следим за папкой и переустанавливаем
        слежение за файлом при необходимости.
        """

        theme_changed = Signal(str)

        def __init__(self, parent=None):
            super().__init__(parent)
            self._watcher = QFileSystemWatcher(self)
            self._last_theme = read_shared_theme()
            try:
                os.makedirs(SHARED_DIR, exist_ok=True)
                self._watcher.addPath(SHARED_DIR)
                if os.path.isfile(SHARED_THEME_PATH):
                    self._watcher.addPath(SHARED_THEME_PATH)
            except Exception:
                pass
            self._watcher.directoryChanged.connect(self._on_changed)
            self._watcher.fileChanged.connect(self._on_changed)

        def _on_changed(self, _path):
            try:
                if (
                    os.path.isfile(SHARED_THEME_PATH)
                    and SHARED_THEME_PATH not in self._watcher.files()
                ):
                    self._watcher.addPath(SHARED_THEME_PATH)
            except Exception:
                pass

            theme = read_shared_theme()
            if theme and theme != self._last_theme:
                self._last_theme = theme
                self.theme_changed.emit(theme)

        def mark_applied(self, theme_name):
            """Отметить тему как уже применённую этим же процессом —
            вызывается ThemeManager'ом сразу после apply_theme(), чтобы
            собственная запись в общий файл не была принята за внешнее
            изменение и не привела к повторному, избыточному применению."""
            self._last_theme = theme_name
