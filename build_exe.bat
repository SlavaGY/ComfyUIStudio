@echo off
setlocal enabledelayedexpansion

:: ==============================================================
:: build_exe.bat -- сборка ВСЕГО комплекта ComfyUI Studio ОДНИМ exe
:: (Windows). Реализовано по образцу tools\promptvault\build.bat --
:: тот же подход (собственный venv для сборки, one-folder, --collect-all
:: для тяжёлых ML-зависимостей PromptVault, упаковка в .zip в конце),
:: но теперь ОДИН запуск PyInstaller на весь монолит: ComfyUI Launcher +
:: Character/Prompt Config Editor + PromptVault -- один процесс, одно
:: окно на инструмент (см. main.py в корне репозитория).
::
:: Кладите этот файл в корень репозитория (рядом с main.py,
:: comfyui_launcher.py, tools\) и запускайте из него.
::
:: one-folder, а не --onefile -- по той же причине, что и у PromptVault:
:: PySide6 + QtWebEngine (нужен ComfyUI Launcher) + torch +
:: sentence-transformers/transformers (нужны PromptVault) в сумме дают
:: сотни МБ-больше гигабайта; --onefile распаковывал бы всё это заново
:: во временную папку при КАЖДОМ запуске.
::
:: Результат: dist\ComfyUIStudio\ComfyUIStudio.exe и вся папка рядом.
:: Распространять нужно ВСЮ папку dist\ComfyUIStudio целиком (скрипт сам
:: упаковывает её в dist\ComfyUIStudio-win64.zip) -- не только .exe.
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
if not exist "tools\prompt_builder\main.py" (
    echo Не вижу tools\prompt_builder\main.py -- проверьте, что архив распакован целиком.
    exit /b 1
)
if not exist "tools\promptvault\app\main.py" (
    echo Не вижу tools\promptvault\app\main.py -- проверьте, что архив распакован целиком.
    exit /b 1
)

echo.
echo === [2/6] Виртуальное окружение ===

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
if exist "assets\icon.ico" (
    set "ICON_ARG=--icon=assets\icon.ico"
) else (
    echo   assets\icon.ico не найден -- соберу без иконки .exe.
)

echo.
echo === [5/6] Сборка PyInstaller ===

if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "ComfyUIStudio.spec" del /q "ComfyUIStudio.spec"

:: --add-data "путь;путь" -- назначение (после ";") ЗЕРКАЛИТ исходный
:: относительный путь неспроста: и comfyui_launcher.py, и
:: tools\prompt_builder\theme_manager.py (см. resource_base()), и
:: tools\promptvault\app\config.py находят свои файлы через
:: Path(__file__)/sys._MEIPASS с тем же относительным путём, что и
:: рядом с исходниками -- сплющить назначение в корень бандла нельзя,
:: иначе эти вычисления перестанут находить themes/assets/resources
:: рядом с собой (см. комментарии в самих этих файлах).
::
:: --add-data "tools\prompt_builder\assets;..." / "tools\prompt_builder\themes;..." --
:: tools\prompt_builder теперь обычный Python-пакет `prompt_builder` (см.
:: tools\prompt_builder\__init__.py и внутренние импорты вида
:: `from prompt_builder.xxx import ...`), подключается из корневого main.py
:: обычным `from prompt_builder.main import create_window` -- PyInstaller
:: видит его в графе импортов и сам собирает весь .py-код в .pyz, поэтому
:: сырые исходники в datas больше не нужны (раньше здесь было
:: --add-data "tools\prompt_builder;tools\prompt_builder" целиком, включая
:: .py -- это было нужно только пока main.py подгружал инструмент
:: динамически через _load_source_module()). Нужны только его
:: НЕ-питоновские файлы данных (темы/иконка) -- по тому же принципу, что
:: и app\resources/app\themes у PromptVault ниже.
::
:: --collect-all на sentence_transformers/transformers/tokenizers --
:: как и в build.bat PromptVault: сами веса модели эмбеддинга НЕ
:: бандлятся (грузятся с HuggingFace Hub при первом запуске), это
:: только служебные data-файлы/сабмодули самих библиотек.
pyinstaller ^
    --name ComfyUIStudio ^
    --noconfirm ^
    --clean ^
    --windowed ^
    %ICON_ARG% ^
    --paths "tools" ^
    --paths "tools\promptvault" ^
    --add-data "assets;assets" ^
    --add-data "themes;themes" ^
    --add-data "tools\prompt_builder\assets;tools\prompt_builder\assets" ^
    --add-data "tools\prompt_builder\themes;tools\prompt_builder\themes" ^
    --add-data "tools\promptvault\app\resources;app\resources" ^
    --add-data "tools\promptvault\app\themes;app\themes" ^
    --collect-all sentence_transformers ^
    --collect-all transformers ^
    --collect-all tokenizers ^
    main.py

if errorlevel 1 (
    echo.
    echo Сборка упала -- см. вывод PyInstaller выше.
    echo Частая причина с этим стеком: PyInstaller не нашёл какой-то
    echo субмодуль/data-файл torch или sentence-transformers -- ищите в
    echo выводе "ModuleNotFoundError"/"No module named" при первом запуске
    echo собранного .exe и добавляйте недостающее через
    echo --hidden-import=^<имя^> или --collect-all=^<пакет^> выше.
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
echo   comfyui_launcher.py и main.py в корне).
echo ===============================================================

endlocal
