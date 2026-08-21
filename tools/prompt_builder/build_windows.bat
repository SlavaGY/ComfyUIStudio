@echo off
REM Сборка PromptConfigEditor.exe (один файл, без консоли), standalone,
REM в обход единого build_exe.bat в корне репозитория (тот собирает
REM весь комплект ComfyUIStudio разом, см. "Сборка в один exe" в
REM README.md).
REM
REM tools/prompt_builder/ -- ЛЕГАСИ-папка (см. дорожную карту
REM рефакторинга, этап 2): исходники Prompt Builder перенесены в
REM comfyui_studio/prompt_builder/ под общее пространство имён
REM комплекта, здесь остались только служебные файлы сборки
REM (requirements.txt, build_windows.bat, build.spec, README.md).
REM Запускать из tools/prompt_builder/, но зависимости и исходники
REM берутся из корня репозитория (ROOT_DIR ниже), а не отсюда.

setlocal

cd /d "%~dp0"
set "ROOT_DIR=%~dp0..\.."

if not exist "%ROOT_DIR%\comfyui_studio\prompt_builder\main.py" (
    echo Не вижу comfyui_studio\prompt_builder\main.py в корне репозитория
    echo ^(%ROOT_DIR%^) -- запускайте build_windows.bat из
    echo tools\prompt_builder\ внутри полной раскладки ComfyUIStudio, не
    echo из отдельно скопированной папки.
    exit /b 1
)

REM Зависимости ставятся из корневого pyproject.toml -- этот
REM standalone-инструмент не имеет своего отдельного набора
REM зависимостей с этапа 2 (перенос под comfyui_studio/ namespace), он
REM использует тот же venv-набор, что и весь комплект (PySide6 и т.п.,
REM Prompt Builder не тянет опциональную группу `promptvault`).
pip install "%ROOT_DIR%" || exit /b 1
pip install pyinstaller

REM Опции вроде --paths здесь бесполезны -- при сборке ИЗ .spec-файла
REM PyInstaller игнорирует большинство CLI-флагов, т.к. Analysis()
REM внутри build.spec уже сам всё определяет (в т.ч. pathex=[ROOT_DIR],
REM нужный, чтобы найти пакет `comfyui_studio`, лежащий двумя уровнями
REM выше tools\prompt_builder\ -- см. комментарии в build.spec).
pyinstaller build.spec --noconfirm

echo.
echo Готово: dist\PromptConfigEditor\PromptConfigEditor.exe
pause
