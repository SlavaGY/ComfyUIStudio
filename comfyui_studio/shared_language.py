"""
shared_language.py
===================
Общее хранилище выбранного языка интерфейса для всех приложений
комплекта (ComfyUI Launcher, Character/Prompt Builder Config Editor,
PromptVault) — по образцу shared_theme.py, но для языка вместо темы.

Хранится в %APPDATA%\\ComfyUIStudio\\language.json. Как и с темой,
SharedLanguageWatcher следит за этим файлом через QFileSystemWatcher,
так что смена языка в любом из трёх приложений применяется сразу и в
остальных, уже открытых, а не только при следующем их запуске.
"""
import json
import os

SHARED_DIR = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")), "ComfyUIStudio"
)
SHARED_LANGUAGE_PATH = os.path.join(SHARED_DIR, "language.json")


def read_shared_language():
    """Возвращает код языка ("ru"/"en") из общего файла, либо None,
    если файла нет, он повреждён или недоступен."""
    try:
        with open(SHARED_LANGUAGE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        code = data.get("language")
        return code if isinstance(code, str) and code else None
    except Exception:
        return None


def write_shared_language(language_code):
    """Сохраняет код языка в общий файл, чтобы его подхватили остальные
    приложения комплекта. Ошибки записи намеренно проглатываются — как
    и в shared_theme.py, синхронизация не должна ронять приложение."""
    try:
        os.makedirs(SHARED_DIR, exist_ok=True)
        with open(SHARED_LANGUAGE_PATH, "w", encoding="utf-8") as f:
            json.dump({"language": language_code}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


try:
    from PySide6.QtCore import QFileSystemWatcher, QObject, Signal
except Exception:  # pragma: no cover - модуль остаётся полезен и без Qt
    QFileSystemWatcher = None
    QObject = object
    Signal = None


if QFileSystemWatcher is not None:

    class SharedLanguageWatcher(QObject):
        """Живая синхронизация языка между уже запущенными приложениями
        комплекта — см. подробный комментарий у SharedThemeWatcher в
        shared_theme.py, устроено полностью аналогично."""

        language_changed = Signal(str)

        def __init__(self, parent=None):
            super().__init__(parent)
            self._watcher = QFileSystemWatcher(self)
            self._last_language = read_shared_language()
            try:
                os.makedirs(SHARED_DIR, exist_ok=True)
                self._watcher.addPath(SHARED_DIR)
                if os.path.isfile(SHARED_LANGUAGE_PATH):
                    self._watcher.addPath(SHARED_LANGUAGE_PATH)
            except Exception:
                pass
            self._watcher.directoryChanged.connect(self._on_changed)
            self._watcher.fileChanged.connect(self._on_changed)

        def _on_changed(self, _path):
            try:
                if (
                    os.path.isfile(SHARED_LANGUAGE_PATH)
                    and SHARED_LANGUAGE_PATH not in self._watcher.files()
                ):
                    self._watcher.addPath(SHARED_LANGUAGE_PATH)
            except Exception:
                pass

            language = read_shared_language()
            if language and language != self._last_language:
                self._last_language = language
                self.language_changed.emit(language)

        def mark_applied(self, language_code):
            """См. SharedThemeWatcher.mark_applied — тот же приём против
            эха собственной записи."""
            self._last_language = language_code
