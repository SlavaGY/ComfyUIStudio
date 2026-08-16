@echo off
setlocal enabledelayedexpansion

:: ==============================================================
:: build_exe.bat -- сборка ВСЕГО комплекта ComfyUI Studio ОДНИМ exe
:: (Windows). Один процесс, одно окно на инструмент (см. main.py в
:: корне репозитория).
::
:: Этап 3 дорожной карты рефакторинга ("Влияние на сборку") добавил
:: ДВА профиля сборки вместо одного:
::   build_exe.bat core   -- Launcher + Prompt Builder + PromptVault
::                            БЕЗ семантического поиска (без torch/
::                            sentence-transformers/transformers) --
::                            заметно меньше и быстрее собирается
::   build_exe.bat full   -- то же самое + семантический поиск (как
::                            было раньше, единственный вариант)
:: Без аргумента -- собирается full (совместимость с прежним
:: поведением скрипта).
::
:: Ставит зависимости через pyproject.toml (`pip install .` для core,
:: `pip install .[promptvault]` для full) -- ОБА .spec-профиля
:: (ComfyUIStudio-core.spec / ComfyUIStudio-full.spec) сами по себе
:: одинаковы в части datas, разница только в наборе зависимостей venv
:: и excludes самого core.spec (страховка, см. его комментарии). Раньше
:: здесь были прямые pyinstaller-флаги (--add-data/--collect-all) --
:: теперь они вынесены в сами .spec-файлы, этот батник только выбирает
:: нужный.
::
:: one-folder, а не --onefile -- при full-профиле PySide6 + QtWebEngine
:: (нужен ComfyUI Launcher) + torch + sentence-transformers/transformers
:: (нужны PromptVault) в сумме дают сотни МБ-больше гигабайта;
:: --onefile распаковывал бы всё это заново во временную папку при
:: КАЖДОМ запуске. core-профиль легче, но one-folder оставлен и для
:: него -- ради единообразия и потому, что QtWebEngine (тяжёлый сам по
:: себе) нужен в обоих профилях.
::
:: Результат: dist\ComfyUIStudio\ComfyUIStudio.exe и вся папка рядом.
:: Распространять нужно ВСЮ папку dist\ComfyUIStudio целиком (скрипт
:: сам упаковывает её в dist\ComfyUIStudio-win64-<профиль>.zip) -- не
:: только .exe.
:: ==============================================================

cd /d "%~dp0"

set "PROFILE=%~1"
if "%PROFILE%"=="" set "PROFILE=full"

if /i not "%PROFILE%"=="core" if /i not "%PROFILE%"=="full" (
    echo Неизвестный профиль сборки: %PROFILE%
    echo Использование: build_exe.bat [core^|full]
    exit /b 1
)

echo.
echo === Профиль сборки: %PROFILE% ===

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

set "VENV_DIR=.venv-build-%PROFILE%"

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Создаю %VENV_DIR%...
    python -m venv "%VENV_DIR%" || exit /b 1
)

call "%VENV_DIR%\Scripts\activate.bat" || exit /b 1

echo.
echo === [3/6] Зависимости ^(pyproject.toml, профиль: %PROFILE%^) ===

python -m pip install --upgrade pip >nul

if /i "%PROFILE%"=="core" (
    :: БЕЗ [promptvault] -- venv физически не увидит torch/
    :: sentence-transformers/transformers, поэтому даже если бы
    :: excludes в ComfyUIStudio-core.spec где-то не сработал, собрать
    :: их всё равно было бы не из чего (см. комментарии в самом .spec)
    pip install . || exit /b 1
) else (
    pip install .[promptvault] || exit /b 1
)

pip install --upgrade pyinstaller || exit /b 1

echo.
echo === [4/6] Иконка приложения ^(.ico^) ===

if not exist "assets\icon.ico" (
    echo   assets\icon.ico не найден -- сборка .spec ожидает его по
    echo   этому пути, см. icon=['assets/icon.ico'] в .spec-файлах.
    exit /b 1
)

echo.
echo === [5/6] Сборка PyInstaller ^(профиль: %PROFILE%^) ===

if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"

:: Датасы (--add-data) и, для full, --collect-all на
:: sentence_transformers/transformers/tokenizers теперь заданы внутри
:: самих .spec-файлов (ComfyUIStudio-core.spec / ComfyUIStudio-full.spec)
:: -- см. их комментарии про то, откуда comfyui_studio/prompt_builder/*
:: и comfyui_studio/promptvault/* находят свои файлы данных внутри
:: _MEIPASS. Сами веса модели эмбеддинга (~1.3 ГБ) НЕ бандлятся ни в
:: одном профиле -- грузятся с HuggingFace Hub при первом запуске
:: (только в full, где семантический поиск вообще доступен).
pyinstaller --noconfirm --clean "ComfyUIStudio-%PROFILE%.spec"

if errorlevel 1 (
    echo.
    echo Сборка упала -- см. вывод PyInstaller выше.
    if /i "%PROFILE%"=="full" (
        echo Частая причина с этим стеком: PyInstaller не нашёл какой-то
        echo субмодуль/data-файл torch или sentence-transformers -- ищите в
        echo выводе "ModuleNotFoundError"/"No module named" при первом запуске
        echo собранного .exe и добавляйте недостающее в hiddenimports
        echo ComfyUIStudio-full.spec.
    )
    exit /b 1
)

echo.
echo === [6/6] Упаковка в .zip ===

set "ZIP_NAME=ComfyUIStudio-win64-%PROFILE%.zip"

if exist "dist\%ZIP_NAME%" del /q "dist\%ZIP_NAME%"

powershell -NoProfile -Command ^
    "Compress-Archive -Path 'dist\ComfyUIStudio\*' -DestinationPath 'dist\%ZIP_NAME%' -Force"

echo.
echo ===============================================================
echo Готово ^(профиль: %PROFILE%^):
echo   dist\ComfyUIStudio\ComfyUIStudio.exe   ^(запуск для проверки на месте^)
echo   dist\%ZIP_NAME%   ^(для распространения -- распаковать целиком^)
echo.
if /i "%PROFILE%"=="core" (
    echo   Это core-сборка: семантический ^(по смыслу^) поиск в PromptVault
    echo   недоступен -- чекбокс "Enable semantic search" в его настройках
    echo   задизейблен с пояснением. Обычный текстовый поиск, фильтры,
    echo   галерея работают как обычно. Соберите full-профиль
    echo   ^(build_exe.bat full^), если нужен семантический поиск.
)
echo.
echo   Один процесс, одно окно на инструмент: ComfyUI Launcher -- это
echo   главное окно, кнопки "Запустить" на странице "Другие инструменты"
echo   открывают Character/Prompt Config Editor и PromptVault окнами
echo   ЭТОГО ЖЕ процесса (см. register_in_process_app() в
echo   comfyui_studio/launcher/integration/tool_registry.py и main.py в
echo   корне).
echo ===============================================================

endlocal
