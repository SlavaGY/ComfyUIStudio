"""
generate_icon.py
=================
Раньше рисовал иконку Prompt Builder программно (скруглённый квадрат с
тремя блоками). С 2026-09-24 иконка приходит из общего файла-
первоисточника вместе с иконками Studio и PromptVault — см.
assets/branding/generate_icons.py в корне репозитория и его докстринг.

Этот файл оставлен как redirect/подсказка на случай, если кто-то (или
старый пайплайн сборки) запустит его по привычке из старого пути —
чтобы не перезаписать новую иконку прежним программным рисунком молча.

Существует в двух одинаковых копиях (comfyui_studio/prompt_builder/ и
comfyui_studio/prompt_builder/assets/, см. историю рефакторинга) на
разной глубине от корня репозитория, поэтому путь к настоящему
генератору ищется подъёмом вверх по дереву, а не фиксированным числом
parents[N] — с двумя копиями на разной глубине число уровней вверх
разное, и фиксированное N было бы верным только для одной из них.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RELATIVE_GENERATOR = Path("assets") / "branding" / "generate_icons.py"


def find_generator() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / RELATIVE_GENERATOR
        if candidate.is_file():
            return candidate
    return None


def main() -> None:
    generator = find_generator()
    if generator is None:
        print(f"Не нашёл {RELATIVE_GENERATOR} ни в одном из родительских каталогов {__file__}.")
        sys.exit(1)
    print(f"Иконки теперь генерируются из {generator} (общий скрипт для Studio/Prompt Builder/PromptVault).")
    subprocess.run([sys.executable, str(generator)], check=True, cwd=generator.parents[2])


if __name__ == "__main__":
    main()
