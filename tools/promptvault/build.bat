@echo off
setlocal enabledelayedexpansion

:: ==============================================================
:: build.bat -- сборка PromptVault в распространяемую папку (Windows)
::
:: Кладите этот файл в корень репозитория (рядом с app\, requirements.txt,
:: pyproject.toml) и запускайте из него.
::
:: Собирает через PyInstaller в РЕЖИМЕ "one-folder" (по умолчанию у
:: PyInstaller, без --onefile). Одним .exe не пакуем сознательно:
:: PySide6 + torch + sentence-transformers/transformers в сумме дают
:: сотни МБ (а с CUDA-сборкой torch -- больше гигабайта); --onefile
:: распаковывал бы всё это заново во временную папку при КАЖДОМ
:: запуске -- заметно медленнее, чем прямой запуск .exe, и особенно
:: болезненно с учётом того, что кнопка "Restart" в самом приложении
:: перезапускает процесс целиком через os.execv (см. TODO.md) -- то
:: есть именно тот сценарий, где такая распаковка происходила бы
:: раз за разом. one-folder распаковывается один раз (при сборке),
:: дальше .exe запускается напрямую из уже разложенных файлов.
::
:: Результат: dist\PromptVault\PromptVault.exe и вся папка рядом с ним.
:: Распространять нужно ВСЮ папку dist\PromptVault целиком (в конце
:: скрипт сам упаковывает её в dist\PromptVault-<версия>-win64.zip
:: через встроенный в Windows Compress-Archive) -- не только .exe.
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

if not exist "app\main.py" (
    echo Не вижу app\main.py -- запускайте build.bat из корня репозитория PromptVault.
    exit /b 1
)

echo.
echo === [2/6] Виртуальное окружение ===

:: используем отдельный venv для сборки (.venv-build), а не .venv для
:: разработки -- чтобы pyinstaller и его сборочные артефакты не лезли
:: в окружение, которым пользуется CONTRIBUTING.md/pytest
if not exist ".venv-build\Scripts\python.exe" (
    echo Создаю .venv-build...
    python -m venv .venv-build || exit /b 1
)

call ".venv-build\Scripts\activate.bat" || exit /b 1

echo.
echo === [3/6] Зависимости ===

python -m pip install --upgrade pip >nul
pip install -r requirements.txt || exit /b 1
pip install --upgrade pyinstaller || exit /b 1

echo.
echo === [4/6] Иконка приложения ^(.ico^) ===

set "ICON_ARG="
if exist "app\resources\icon.ico" (
    set "ICON_ARG=--icon=app\resources\icon.ico"
) else if exist "app\resources\icon.png" (
    :: PyInstaller на Windows умеет только .ico -- конвертируем
    :: имеющийся .png один раз через Pillow, best-effort: если не
    :: получится (нет сети на pip install, сломанный venv и т.п.),
    :: просто соберём без иконки в .exe, а не роняем всю сборку
    pip install --quiet pillow >nul 2>&1
    python -c "from PIL import Image; Image.open('app/resources/icon.png').convert('RGBA').save('app/resources/icon.ico', sizes=[(16,16),(32,32),(48,48),(256,256)])" 2>nul
    if exist "app\resources\icon.ico" (
        set "ICON_ARG=--icon=app\resources\icon.ico"
    ) else (
        echo   Не удалось сконвертировать icon.png в .ico -- соберу без иконки.
    )
) else (
    echo   app\resources\icon.png не найден -- соберу без иконки.
)

echo.
echo === [5/6] Сборка PyInstaller ===

if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "PromptVault.spec" del /q "PromptVault.spec"

:: --add-data "app\resources;app\resources" / "app\themes;app\themes":
:: назначение (после ";") ЗЕРКАЛИТ исходный путь пакета неспроста --
:: app/config.py и app/themes/theme_manager.py находят свои файлы через
:: Path(__file__).resolve().parent / "..." (ICON_PATH/TRANSLATIONS_DIR/
:: THEMES_DIR), а PyInstaller в one-folder режиме подставляет __file__
:: замороженных модулей как путь ВНУТРИ папки со сборкой (_internal\...
:: в PyInstaller 6+), с тем же относительным пакетным путём app\... --
:: если сплющить назначение в корень, эти Path(__file__)-вычисления
:: перестанут находить resources/themes рядом с собой.
::
:: --collect-all на sentence_transformers/transformers/tokenizers:
:: сами веса модели эмбеддинга (~1.3 ГБ e5-large-v2 по умолчанию, см.
:: TODO.md) НЕ бандлятся -- они грузятся с HuggingFace Hub и кэшируются
:: в домашней папке пользователя при первой синхронизации папки с
:: включённым семантическим поиском (см. app/core/embedding.py), как
:: и при обычном запуске из исходников. --collect-all здесь только
:: подтягивает служебные data-файлы/сабмодули самих библиотек, которые
:: PyInstaller не всегда находит статическим анализом импортов (эти
:: импорты в app/core/embedding.py лежат внутри функций, а не на
:: верхнем уровне модуля -- см. docstring embedding.py).
pyinstaller ^
    --name PromptVault ^
    --noconfirm ^
    --clean ^
    --windowed ^
    %ICON_ARG% ^
    --add-data "app\resources;app\resources" ^
    --add-data "app\themes;app\themes" ^
    --collect-all sentence_transformers ^
    --collect-all transformers ^
    --collect-all tokenizers ^
    app\main.py

if errorlevel 1 (
    echo.
    echo Сборка упала -- см. вывод PyInstaller выше.
    echo Частая причина с этим стеком: PyInstaller не нашёл какой-то
    echo субмодуль/data-файл torch или sentence-transformers -- ищите в
    echo выводе "ModuleNotFoundError"/"No module named" при первом запуске
    echo собранного .exe и добавляйте недостающее через ^^^^
    echo --hidden-import=^<имя^> или --collect-all=^<пакет^> выше.
    exit /b 1
)

echo.
echo === [6/6] Упаковка в .zip ===

for /f "delims=" %%v in ('python -c "from app.config import APP_VERSION; print(APP_VERSION)"') do set "PV_VERSION=%%v"
if not defined PV_VERSION set "PV_VERSION=dev"

set "ZIP_NAME=PromptVault-%PV_VERSION%-win64.zip"

if exist "dist\%ZIP_NAME%" del /q "dist\%ZIP_NAME%"

powershell -NoProfile -Command ^
    "Compress-Archive -Path 'dist\PromptVault\*' -DestinationPath 'dist\%ZIP_NAME%' -Force"

echo.
echo ===============================================================
echo Готово:
echo   dist\PromptVault\PromptVault.exe   ^(запуск для проверки на месте^)
echo   dist\%ZIP_NAME%   ^(для распространения -- распаковать целиком^)
echo ===============================================================

endlocal
