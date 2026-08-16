"""
lora_combo.py (Qt)
Выпадающий список файлов LoRA, найденных в папке, которую пользователь
указывает один раз через меню "Файл -> Указать папку с файлами LoRA..."
(см. main.py) — хранится в QSettings, общая на весь редактор (и для
"Привязанная LoRA" в CharactersTab, и для LoraTableEditor в
PromptBuilderTab). Список пересканирует папку заново при каждом
разворачивании — если файлы добавили/удалили, пока редактор был открыт,
не нужно ничего перезапускать.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QComboBox

# Расширения файлов, которые считаем LoRA — самые распространённые
# форматы весов, с которыми работает ComfyUI (LoraLoader и т.п.).
LORA_FILE_EXTENSIONS = {".safetensors", ".pt", ".ckpt", ".bin"}

_SETTINGS_KEY = "lora_folder"


def _settings():
    # Локальный импорт, чтобы не тянуть QSettings туда, где этот модуль
    # импортируют только ради констант/функций без Qt-приложения.
    from PySide6.QtCore import QSettings
    # Явно те же org/app, что и у ThemeManager/MainWindow/LocalizationManager
    # (см. QSettings("PromptConfigEditor", "PromptConfigEditor") в main.py,
    # theme_manager.py, pb_i18n.py) — один файл настроек на весь инструмент.
    # Раньше здесь был голый QSettings(), который резолвится по
    # ГЛОБАЛЬНОМУ QApplication.applicationName()/organizationName() — при
    # автономном запуске (см. main() ниже в этом же файле) это то же самое
    # "PromptConfigEditor"/"PromptConfigEditor", поэтому совпадение было
    # незаметным; но при запуске внутри монолитного ComfyUIStudio имя
    # приложения — "ComfyUI Studio" (без organizationName вообще, см.
    # корневой main.py), и голый QSettings() тихо уезжал в другую, пустую
    # область настроек — папка LoRA сохранялась не туда, откуда её потом
    # читал выпадающий список.
    return QSettings("PromptConfigEditor", "PromptConfigEditor")


def get_lora_folder() -> str:
    return str(_settings().value(_SETTINGS_KEY, "", type=str) or "")


def set_lora_folder(path: str):
    _settings().setValue(_SETTINGS_KEY, path)


def scan_lora_files(folder: str) -> list[str]:
    """Рекурсивно ищет файлы LoRA в folder, возвращает отсортированный
    список путей относительно folder (с расширением, разделитель '/'
    независимо от ОС). Тихо возвращает [] на отсутствующую/недоступную
    папку — вызывающий код (LoraFileCombo) сам решает, показывать ли
    предупреждение."""
    if not folder:
        return []
    root = Path(folder)
    if not root.is_dir():
        return []
    results = []
    try:
        for p in root.rglob("*"):
            if p.is_file() and p.suffix.lower() in LORA_FILE_EXTENSIONS:
                results.append(p.relative_to(root).as_posix())
    except OSError:
        pass
    return sorted(results, key=str.lower)


class LoraFileCombo(QComboBox):
    """Редактируемый выпадающий список файлов LoRA.

    Два момента, из-за которых это не просто QComboBox(editable=True):
    - Список пересканируется заново при каждом showPopup() (см.
      refresh_items()), плюс заполняется один раз сразу при создании,
      чтобы виджет не выглядел пустым текстовым полем до первого клика.
    - У editable QComboBox есть скрытый нюанс: сам виджет почти целиком
      занят своим внутренним QLineEdit (полем ввода) — клик мышью
      попадает СНАЧАЛА в это внутреннее поле, а не в сам QComboBox, и
      переопределение QComboBox.mousePressEvent для него просто не
      срабатывает (кроме узкой ~20px стрелки справа, где ComboBox
      действительно получает клик напрямую). Поэтому клик по полю
      только ставит туда курсор, а колесо мыши при этом молча листает
      пункты — внешне это и выглядит как "не выпадающий список, а
      поле, которое можно только прокручивать". Чтобы клик по всему
      полю тоже открывал попап (как у обычного, не editable,
      комбобокса), здесь ставится event filter на внутренний QLineEdit
      (см. eventFilter ниже) — ввод текста с клавиатуры при этом
      по-прежнему работает как обычно.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        if self.lineEdit() is not None:
            self.lineEdit().installEventFilter(self)
        self.refresh_items()

    def eventFilter(self, obj, event):
        if obj is self.lineEdit() and event.type() == QEvent.MouseButtonPress:
            self.showPopup()
            # событие не "съедаем" (не возвращаем True) — клик всё
            # равно ставит курсор в поле и даёт ему фокус, чтобы можно
            # было вручную ввести значение, которого нет в
            # отсканированной папке, без дополнительных кликов.
        return super().eventFilter(obj, event)

    def showPopup(self):
        self.refresh_items()
        super().showPopup()

    def refresh_items(self):
        current = self.currentText()
        self.blockSignals(True)
        QComboBox.clear(self)  # список пунктов, а не текст — см. clear() ниже
        self.addItems(scan_lora_files(get_lora_folder()))
        self.setCurrentText(current)
        self.blockSignals(False)

    # -- совместимость с QLineEdit, чтобы быть подстановкой на его месте --
    def text(self) -> str:
        return self.currentText()

    def setText(self, value: str):
        if value and self.findText(value) < 0:
            self.addItem(value)
        self.setCurrentText(value or "")

    def clear(self):
        """У QComboBox.clear() значение "очистить список пунктов"; здесь,
        по аналогии с QLineEdit (на месте которого этот виджет
        используется), clear() означает "очистить текст поля". Чтобы
        очистить сам список пунктов — используется refresh_items()
        выше, который вызывает QComboBox.clear(self) напрямую."""
        self.setCurrentText("")

    def setPlaceholderText(self, text: str):
        if self.lineEdit() is not None:
            self.lineEdit().setPlaceholderText(text)
