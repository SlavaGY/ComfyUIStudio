# R2 — единый модуль путей `app_paths.py`

Архив — **наложение** на корень репозитория (после R8). Удалять ничего не
нужно.

## Что было не так

`os.environ.get("APPDATA", os.path.expanduser("~"))` стоял в 8 местах, и
тесты при этом писали в настоящие папки данных: после прогона `pytest`
в `%APPDATA%` появлялись/менялись `prompt_generator.json`,
`promptgen_history.db`, `imagine\uploads\` и `ComfyUILauncher\launcher.log`
(проверено на реальном прогоне с подменённым домашним каталогом).

## Что изменилось

- **`comfyui_studio/app_paths.py`** — единственное место, где собирается
  путь к данным. Только стандартная библиотека (его импортируют и Remote,
  и Imagine, где Qt нет). Функции: `studio_data_dir()`,
  `launcher_data_dir()`, `imagine_data_dir()`, `appdata_root()`.
- Через него теперь считают пути 7 модулей: `shared_theme`,
  `shared_language`, `shared_promptgen`, `remote/device_store`,
  `remote/ssh_config_store`, `imagine/backend/config_store`,
  `launcher/core/constants`.
- **Расположение файлов не изменилось**: `%APPDATA%\ComfyUIStudio` и
  `%APPDATA%\ComfyUILauncher` — те же две папки, данные и спаренные
  телефоны не затронуты. Слияние папок **не делалось** (это миграция
  `config.json`, профиля WebEngine и `remote_devices.json`).
- Модули по-прежнему хранят пути в своих константах (`SHARED_DIR`,
  `DEVICE_STORE_PATH`, `CONFIG_PATH`…), поэтому все существующие тесты с
  `monkeypatch.setattr(..., "SHARED_DIR", ...)` работают без изменений.

## Переопределение через окружение

| Переменная | Что заменяет |
|---|---|
| `COMFYUI_STUDIO_DATA_DIR` | `studio_data_dir()` (тема, язык, генератор, Remote, `imagine\`) |
| `COMFYUI_LAUNCHER_DATA_DIR` | `launcher_data_dir()` (`config.json`, профиль WebEngine, `launcher.log`) |
| `IMAGINE_DATA_DIR` | `imagine_data_dir()` (как и раньше) |

- Пустое значение = «не задано».
- Задавать нужно **до запуска** приложения (пути запоминаются при импорте).
- Подпроцессы Remote и Imagine наследуют окружение лаунчера, поэтому
  переопределение действует на весь комплект сразу (проверено по коду
  запуска процессов).
- **`COMFYUI_LAUNCHER_DATA_DIR` — моё добавление** к согласованному:
  вы одобрили переменную только для `studio_data_dir()`, но без второй
  нельзя изолировать в тестах `ComfyUILauncher\` (лог лаунчера
  оставался в настоящей папке). Если она не нужна, удаляется строкой в
  `app_paths.py` и `conftest.py`.

## Исключение из «одного места»

`imagine/backend/promptgen.py` читает `%APPDATA%\NVIDIA\ComputeCache` —
это кэш CUDA-ядер **драйвера NVIDIA**, а не данные приложения. Оставлено
как есть с комментарием; охранный тест разрешает ровно эту строку.

## Тесты

- Новые: `tests/shared/test_app_paths.py` (16): значения по умолчанию
  (прежняя раскладка), переопределения, пустое значение, сквозная проверка
  в отдельном процессе (все потребители следуют переменным), отсутствие Qt
  в импорте, охранный тест «`environ["APPDATA"]` только в `app_paths.py`».
- Корневой `conftest.py` теперь на всю сессию направляет обе папки данных
  во временную (через переменные выше, до первого импорта проекта) и
  удаляет её в конце. Ни один тест не может записать в настоящие данные,
  даже забыв про `monkeypatch`. После полного прогона в подменённом
  домашнем каталоге нет ни `ComfyUIStudio`, ни `ComfyUILauncher`.
- Существующие тесты не менялись.
- Строгий mypy включён и для `app_paths` (чисто).
- Ожидаемый результат: Linux — 1057 passed (было 1041). Windows — около
  **989 passed, 68 skipped** (оценка, не гарантия).

## Не затронуто (сознательно)

- `~/.promptvault` (БД и превью PromptVault) лежит вне APPDATA и мимо
  `app_paths`; тесты PromptVault пишут туда по-прежнему. Если нужно —
  отдельным небольшим шагом.
- `APP_NAME = "ComfyUI Launcher"` оставлен: он попадает в
  `setApplicationName`, а значит, в ключи QSettings (размеры окон).
- Остальные QSettings (кроме менеджеров тем/языка) в тестах не
  изолированы.

## Проверка у вас

1. `pytest` — зелёный; после него `%APPDATA%\ComfyUIStudio` и
   `ComfyUILauncher` не меняются (можно сравнить время изменения файлов).
2. Запуск лаунчера: настройки, тема, язык, спаренные телефоны на месте.
3. По желанию: `set COMFYUI_STUDIO_DATA_DIR=D:\test_data` и запуск —
   все файлы комплекта появляются там, включая `remote_devices.json`.
