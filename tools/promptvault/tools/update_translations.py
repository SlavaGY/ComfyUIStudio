"""Обновление переводов интерфейса (задача: полный аудит строк UI под
self.tr()) — тонкая обёртка над pyside6-lupdate/pyside6-lrelease,
которые ставятся вместе с самим PySide6 через pip (Windows включая
Scripts/pyside6-lupdate.exe) — отдельно ставить не нужно.

Использование:

    python -m tools.update_translations update    # .py -> .ts (после
                                                    # добавления/правки
                                                    # self.tr(...) в
                                                    # comfyui_studio/promptvault/ui/)
    python -m tools.update_translations compile    # .ts -> .qm (после
                                                    # заполнения новых
                                                    # <translation> в .ts)
    python -m tools.update_translations check      # update без записи
                                                    # + падает, если
                                                    # найдены строки без
                                                    # перевода — см.
                                                    # .github/workflows/ci.yml

Рабочий процесс при добавлении новой self.tr("...") строки в код:
  1. python -m tools.update_translations update — добавит новую
     строку в .ts с type="unfinished" (см. lupdate merge-семантику:
     существующие переводы НЕ затираются, только новые/удалённые
     строки).
  2. Вручную (или через Qt Linguist) заполнить <translation> для
     новых записей в comfyui_studio/promptvault/resources/translations/promptvault_ru.ts.
  3. python -m tools.update_translations compile — пересобрать .qm.
  4. Закоммитить И .ts, И .qm — оба хранятся в репозитории (см.
     CONTRIBUTING.md, раздел "Локализация"): .ts — редактируемый
     исходник перевода, .qm — то, что реально грузит QTranslator в
     рантайме; без пересборки .qm правки в .ts на запущенное
     приложение не повлияют.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

UI_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "comfyui_studio" / "promptvault" / "ui"
)
TS_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "comfyui_studio" / "promptvault" / "resources" / "translations" / "promptvault_ru.ts"
)
QM_PATH = TS_PATH.with_suffix(".qm")


def _require_tool(name: str) -> str:

    path = shutil.which(name)

    if path is None:
        print(
            f"{name} не найден в PATH. Он ставится вместе с PySide6 через "
            f"pip (Scripts/{name}.exe на Windows, {name} в venv/bin на "
            f"Linux/macOS) — проверьте, что виртуальное окружение с "
            f"PySide6 активировано.",
            file=sys.stderr,
        )
        sys.exit(1)

    return path


def update() -> None:
    """Сканирует comfyui_studio/promptvault/ui/*.py и мёржит найденные self.tr(...) строки в
    .ts — существующие переводы сохраняются, новые строки добавляются
    как unfinished, строки для удалённого кода помечаются obsolete."""

    lupdate = _require_tool("pyside6-lupdate")

    ui_files = sorted(str(p) for p in UI_DIR.glob("*.py"))

    subprocess.run(
        [lupdate, *ui_files, "-ts", str(TS_PATH), "-extensions", "py"],
        check=True,
    )


def compile_qm() -> None:
    """.ts -> .qm — то, что реально грузит QTranslator в рантайме
    (см. comfyui_studio/promptvault/i18n.py). Без этого шага правки в .ts не видны в
    приложении."""

    lrelease = _require_tool("pyside6-lrelease")

    subprocess.run(
        [lrelease, str(TS_PATH), "-qm", str(QM_PATH)],
        check=True,
    )


def check() -> None:
    """Для CI (см. .github/workflows/ci.yml) — проверяет, что
    закоммиченный .ts уже содержит перевод для каждой self.tr(...)
    строки в comfyui_studio/promptvault/ui/, не изменяя сам файл. Падает с ненулевым кодом,
    если lupdate находит новые (ещё не в .ts) строки — сигнал, что
    кто-то добавил self.tr(...) в коде, но забыл прогнать `update` и
    заполнить перевод."""

    lupdate = _require_tool("pyside6-lupdate")

    ui_files = sorted(str(p) for p in UI_DIR.glob("*.py"))

    result = subprocess.run(
        [lupdate, *ui_files, "-ts", str(TS_PATH), "-extensions", "py"],
        check=True,
        capture_output=True,
        text=True,
    )

    output = result.stdout + result.stderr
    print(output, end="")

    # lupdate печатает "Found N source text(s) (M new and K already
    # existing)" — нас интересует M
    import re

    match = re.search(r"\((\d+) new", output)
    new_count = int(match.group(1)) if match else 0

    if new_count > 0:
        print(
            f"\n{new_count} строк(и) в comfyui_studio/promptvault/ui/ ещё не переведены "
            f"(нет в promptvault_ru.ts). Прогоните "
            f"`python -m tools.update_translations update`, заполните "
            f"переводы и закоммитьте обновлённый .ts + пересобранный .qm.",
            file=sys.stderr,
        )
        sys.exit(1)


def main() -> None:

    if len(sys.argv) != 2 or sys.argv[1] not in {"update", "compile", "check"}:
        print(__doc__)
        sys.exit(1)

    {"update": update, "compile": compile_qm, "check": check}[sys.argv[1]]()


if __name__ == "__main__":
    main()
