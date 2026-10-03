@echo off
setlocal enabledelayedexpansion

:: ==============================================================
:: build_exe.bat -- сборка ВСЕГО комплекта ComfyUI Studio ОДНИМ exe
:: (Windows). Один процесс, одно окно на инструмент (см. main.py в
:: корне репозитория).
::
:: Раньше здесь было два профиля сборки (core/full, в зависимости от
:: того, включён ли семантический поиск в PromptVault) -- семантический
:: поиск с тех пор полностью удалён из исходников, так что деление на
:: профили больше не нужно: один .spec (ComfyUIStudio.spec), один venv,
:: один результат сборки, как было до этапа 3 дорожной карты
:: рефакторинга ("Влияние на сборку").
::
:: Ставит зависимости через pyproject.toml (`pip install .[imagine]`).
:: Датасы (--add-data) и hiddenimports/collect_all заданы внутри самого
:: .spec-файла -- этот батник только вызывает pyinstaller.
::
:: one-folder, а не --onefile -- PySide6 + QtWebEngine (нужен ComfyUI
:: Launcher) в сумме дают заметный объём; --onefile распаковывал бы всё
:: это заново во временную папку при КАЖДОМ запуске.
::
:: Результат: dist\ComfyUIStudio\ComfyUIStudio.exe и вся папка рядом.
:: Распространять нужно ВСЮ папку dist\ComfyUIStudio целиком (скрипт
:: сам упаковывает её в dist\ComfyUIStudio-win64.zip) -- не только .exe.
::
:: Imagine (comfyui_studio/imagine/ -- вариант запуска ComfyUI, см.
:: "Интерфейс" в настройках ComfyUI) — ЧАСТЬ ТОГО ЖЕ ComfyUIStudio.exe,
:: отдельного exe/спека для него нет: собранный exe запускает сам себя
:: подпроцессом со скрытым флагом (см. main.py и launcher/core/
:: imagine_process.py). Зависимости Imagine (fastapi/uvicorn/pydantic/
:: python-multipart, extras 'imagine' в pyproject.toml) ставятся ниже
:: автоматически -- отдельно накатывать их после сборки не нужно.
:: ==============================================================

cd /d "%~dp0"

echo.
echo === [1/6] Проверка Python ===

python --version >nul 2>&1
if errorlevel 1 (
    echo Python не найден в PATH. Установите Python 3.11+ с python.org
    echo ^(галочка "Add python.exe to PATH" при установке^) и повторите.
    exit /b 1
)

if not exist "main.py" (
    echo Не вижу main.py -- запускайте build_exe.bat из корня репозитория ComfyUIStudio.
    exit /b 1
)
:: Пути ниже -- comfyui_studio/*, а не tools/* (актуально с этапа 2
:: дорожной карты рефакторинга, переносившего prompt_builder/promptvault
:: под общее пространство имён; tools/ пока физически ещё лежит в
:: репозитории как исходный код ДО переноса, но main.py его больше не
:: импортирует -- см. комментарии в main.py, уборка tools/ запланирована
:: на этап 5).
if not exist "comfyui_studio\prompt_builder\main.py" (
    echo Не вижу comfyui_studio\prompt_builder\main.py -- проверьте, что архив распакован целиком.
    exit /b 1
)
if not exist "comfyui_studio\promptvault\main.py" (
    echo Не вижу comfyui_studio\promptvault\main.py -- проверьте, что архив распакован целиком.
    exit /b 1
)

echo.
echo === [2/6] Виртуальное окружение ===

set "VENV_DIR=.venv-build"

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Создаю %VENV_DIR%...
    python -m venv "%VENV_DIR%" || exit /b 1
)

call "%VENV_DIR%\Scripts\activate.bat" || exit /b 1

echo.
echo === [3/6] Зависимости ^(pyproject.toml^) ===

python -m pip install --upgrade pip >nul
pip install .[imagine] || exit /b 1
pip install --upgrade pyinstaller || exit /b 1

echo.
echo === [4/6] Иконка приложения ^(.ico^) ===

if not exist "assets\icon.ico" (
    echo   assets\icon.ico не найден -- сборка .spec ожидает его по
    echo   этому пути, см. icon=['assets/icon.ico'] в ComfyUIStudio.spec.
    exit /b 1
)

echo.
echo === [5/6] Сборка PyInstaller ===

if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"

:: Датасы (--add-data) и --collect-all заданы внутри самого .spec-файла
:: (ComfyUIStudio.spec) -- см. его комментарии про то, откуда
:: comfyui_studio/prompt_builder/* и comfyui_studio/promptvault/*
:: находят свои файлы данных внутри _MEIPASS.
pyinstaller --noconfirm --clean "ComfyUIStudio.spec"

if errorlevel 1 (
    echo.
    echo Сборка упала -- см. вывод PyInstaller выше.
    echo Частая причина: PyInstaller не нашёл какой-то субмодуль/data-файл --
    echo ищите в выводе "ModuleNotFoundError"/"No module named" при первом
    echo запуске собранного .exe и добавляйте недостающее в hiddenimports
    echo ComfyUIStudio.spec.
    exit /b 1
)

echo.
echo === [6/6] Упаковка в .zip ===

set "ZIP_NAME=ComfyUIStudio-win64.zip"

if exist "dist\%ZIP_NAME%" del /q "dist\%ZIP_NAME%"

powershell -NoProfile -Command ^
    "Compress-Archive -Path 'dist\ComfyUIStudio\*' -DestinationPath 'dist\%ZIP_NAME%' -Force"

echo.
echo ===============================================================
echo Готово:
echo   dist\ComfyUIStudio\ComfyUIStudio.exe   ^(запуск для проверки на месте^)
echo   dist\%ZIP_NAME%   ^(для распространения -- распаковать целиком^)
echo.
echo   Один процесс, одно окно на инструмент: ComfyUI Launcher -- это
echo   главное окно, кнопки "Запустить" на странице "Другие инструменты"
echo   открывают Character/Prompt Config Editor и PromptVault окнами
echo   ЭТОГО ЖЕ процесса (см. register_in_process_app() в
echo   comfyui_studio/launcher/integration/tool_registry.py и main.py в
echo   корне).
echo ===============================================================

endlocal
