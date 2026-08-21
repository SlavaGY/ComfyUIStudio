@echo off
setlocal enabledelayedexpansion

:: ==============================================================
:: build.bat -- сборка PromptVault standalone-exe (Windows), в обход
:: единого build_exe.bat в корне репозитория (тот собирает весь
:: комплект ComfyUIStudio разом, см. "Сборка в один exe" в README.md).
::
:: tools/promptvault/ -- ЛЕГАСИ-папка (см. дорожную карту рефакторинга,
:: этап 2): исходники PromptVault отсюда перенесены в
:: comfyui_studio/promptvault/ под общее пространство имён комплекта,
:: здесь остались только служебные файлы сборки (requirements.txt,
:: build.bat, PromptVault.spec, TODO.md) -- этот скрипт запускается ИЗ
:: tools/promptvault/, но собирает исходники двумя уровнями выше
:: (ROOT_DIR), а не рядом с собой -- отсюда все пути ниже.
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
:: Результат: dist\PromptVault\PromptVault.exe и вся папка рядом с ним
:: (внутри tools\promptvault\, там же откуда запущен скрипт).
:: Распространять нужно ВСЮ папку dist\PromptVault целиком (в конце
:: скрипт сам упаковывает её в dist\PromptVault-<версия>-win64.zip
:: через встроенный в Windows Compress-Archive) -- не только .exe.
:: ==============================================================

cd /d "%~dp0"
set "ROOT_DIR=%~dp0..\.."

echo.
echo === [1/6] Проверка Python ===

python --version >nul 2>&1
if errorlevel 1 (
    echo Python не найден в PATH. Установите Python 3.11+ с python.org
    echo ^(галочка "Add python.exe to PATH" при установке^) и повторите.
    exit /b 1
)

if not exist "%ROOT_DIR%\comfyui_studio\promptvault\main.py" (
    echo Не вижу comfyui_studio\promptvault\main.py в корне репозитория
    echo ^(%ROOT_DIR%^) -- запускайте build.bat из tools\promptvault\ внутри
    echo полной раскладки ComfyUIStudio, не из отдельно скопированной папки.
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

:: Зависимости ставятся из корневого pyproject.toml (группа
:: `promptvault` -- torch/sentence-transformers, см. его комментарии),
:: а не из requirements.txt: этот standalone-инструмент не имеет своего
:: отдельного набора зависимостей с этапа 2 (перенос под comfyui_studio/
:: namespace) -- он использует тот же venv-набор, что и весь комплект.
python -m pip install --upgrade pip >nul
pip install "%ROOT_DIR%[promptvault]" || exit /b 1
pip install --upgrade pyinstaller || exit /b 1

echo.
echo === [4/6] Иконка приложения ^(.ico^) ===

set "ICON_ARG="
if exist "%ROOT_DIR%\comfyui_studio\promptvault\resources\icon.ico" (
    set "ICON_ARG=--icon=%ROOT_DIR%\comfyui_studio\promptvault\resources\icon.ico"
) else if exist "%ROOT_DIR%\comfyui_studio\promptvault\resources\icon.png" (
    :: PyInstaller на Windows умеет только .ico -- конвертируем
    :: имеющийся .png один раз через Pillow, best-effort: если не
    :: получится (нет сети на pip install, сломанный venv и т.п.),
    :: просто соберём без иконки в .exe, а не роняем всю сборку
    pip install --quiet pillow >nul 2>&1
    python -c "from PIL import Image; Image.open(r'%ROOT_DIR%\comfyui_studio\promptvault\resources\icon.png').convert('RGBA').save(r'%ROOT_DIR%\comfyui_studio\promptvault\resources\icon.ico', sizes=[(16,16),(32,32),(48,48),(256,256)])" 2>nul
    if exist "%ROOT_DIR%\comfyui_studio\promptvault\resources\icon.ico" (
        set "ICON_ARG=--icon=%ROOT_DIR%\comfyui_studio\promptvault\resources\icon.ico"
    ) else (
        echo   Не удалось сконвертировать icon.png в .ico -- соберу без иконки.
    )
) else (
    echo   comfyui_studio\promptvault\resources\icon.png не найден -- соберу без иконки.
)

echo.
echo === [5/6] Сборка PyInstaller ===

if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "PromptVault.spec" del /q "PromptVault.spec"

:: --paths добавляет корень репозитория в sys.path сборки -- иначе
:: PyInstaller, запущенный из tools\promptvault\, не найдёт пакет
:: `comfyui_studio` (лежит двумя уровнями выше, не под tools\).
::
:: --add-data "...comfyui_studio\promptvault\resources;comfyui_studio\promptvault\resources":
:: назначение (после ";") ЗЕРКАЛИТ пакетный путь неспроста --
:: comfyui_studio/promptvault/config.py и .../themes/theme_manager.py
:: находят свои файлы через Path(__file__).resolve().parent /
:: "resources" (см. ICON_PATH/TRANSLATIONS_DIR/THEMES_DIR в config.py),
:: а PyInstaller в one-folder режиме подставляет __file__ замороженных
:: модулей как путь ВНУТРИ папки со сборкой (_internal\... в
:: PyInstaller 6+), с тем же относительным пакетным путём
:: comfyui_studio\promptvault\... -- если сплющить назначение в
:: корень, эти Path(__file__)-вычисления перестанут находить
:: resources/themes рядом с собой. Та же раскладка datas уже
:: используется корневым ComfyUIStudio-full.spec -- см. его комментарии.
::
:: --collect-all на sentence_transformers/transformers/tokenizers:
:: сами веса модели эмбеддинга (~1.3 ГБ e5-large-v2 по умолчанию, см.
:: TODO.md) НЕ бандлятся -- они грузятся с HuggingFace Hub и кэшируются
:: в домашней папке пользователя при первой синхронизации папки с
:: включённым семантическим поиском (см.
:: comfyui_studio/promptvault/core/embedding.py), как и при обычном
:: запуске из исходников. --collect-all здесь только подтягивает
:: служебные data-файлы/сабмодули самих библиотек, которые PyInstaller
:: не всегда находит статическим анализом импортов (эти импорты в
:: embedding.py лежат внутри функций, а не на верхнем уровне модуля --
:: см. docstring embedding.py).
pyinstaller ^
    --name PromptVault ^
    --noconfirm ^
    --clean ^
    --windowed ^
    --paths "%ROOT_DIR%" ^
    %ICON_ARG% ^
    --add-data "%ROOT_DIR%\comfyui_studio\promptvault\resources;comfyui_studio\promptvault\resources" ^
    --add-data "%ROOT_DIR%\comfyui_studio\promptvault\themes;comfyui_studio\promptvault\themes" ^
    --collect-all sentence_transformers ^
    --collect-all transformers ^
    --collect-all tokenizers ^
    "%ROOT_DIR%\comfyui_studio\promptvault\main.py"

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

pushd "%ROOT_DIR%"
for /f "delims=" %%v in ('python -c "from comfyui_studio.promptvault.config import APP_VERSION; print(APP_VERSION)"') do set "PV_VERSION=%%v"
popd
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
echo.
echo   Это standalone-сборка ОДНОГО PromptVault -- если нужен весь
echo   комплект ComfyUIStudio (Launcher + Prompt Builder + PromptVault)
echo   одним exe, используйте build_exe.bat в корне репозитория.
echo ===============================================================

endlocal
