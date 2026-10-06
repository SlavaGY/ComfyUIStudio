# R10 — Imagine: разбиение `promptgen.py`

Архив — **наложение** на корень репозитория (после R2/R8/R9). Удалять ничего
не нужно, команды git не нужны.

## Что изменилось

`comfyui_studio/imagine/backend/promptgen.py` — 1622 → 658 строк. Он остался
публичным входом: `PromptGenerator`, singleton `generator`, реэкспорт всего,
что раньше определялось в файле (`promptgen.<имя>` в тестах и в `main.py`
работает как раньше). Остальное — рядом, в шести новых модулях:

| Модуль | Что внутри | Строк |
|---|---|---|
| `promptgen_base.py` | константы, `PromptGenError`, `_Cancelled`, мелкие помощники; ничего не импортирует из пакета | 63 |
| `promptgen_deps.py` | необязательные `shared_promptgen` / `promptgen_history` | 25 |
| `promptgen_diag.py` | логирование и `promptgen.log`, VRAM/RAM, разбор вывода llama-server, `_LoadProbe`, чтение логов | 418 |
| `promptgen_job.py` | `Job`, `clean_output`, подсчёт токенов, запись для истории | 221 |
| `promptgen_process.py` | запуск и остановка llama-server, Job Object, реестр процессов, `sweep_stale` | 407 |
| `promptgen_stream.py` | `POST /v1/chat/completions` со стримингом SSE | 140 |

Зависимости идут строго вверх: `base` ← `deps` ← `diag` ← `process` ← `stream`
← `promptgen`; `job` независим. Цикл с фасадом проверяется тестом.

- Тела функций перенесены механически (генератором по AST), без правок.
  Из методов `PromptGenerator` стали функциями без `self` (состояние они не
  использовали): `_inspect_llama_log`, `_log_stats`, `_token_counts`,
  `_stream_completion`, `_lost_connection_message` и расчёт записи истории
  из `_save_history`. Прежние методы остались тонкими делегатами.
- **Намеренно остались в `promptgen.py`:** `_FIRST_REPORT_S`, `_REPORT_EVERY_S`,
  `gpu_memory` (как имя), `generator`. Тесты подменяют их через `pg.<имя>`,
  а подмена работает только для кода, читающего имя из этого модуля.
- **Не реэкспортируется:** состояние логирования (`_file_handler*`,
  `_console_handler`) и `_job_handle`. Копия имени расходилась бы с
  настоящим значением после первой перепривязки; в тестах они не используются.
- Поведение отмены не менялось: `_Cancelled` — тот же класс, ловится
  отдельной веткой раньше общего `except`, и это закреплено тестом.
- `tests/shared/test_app_paths.py`: охранный тест R2 теперь разрешает
  `os.environ.get("APPDATA")` в `promptgen_diag.py` вместо `promptgen.py`
  (туда переехал `_compute_cache_dir`). Строка в докстринге `app_paths.py`
  обновлена так же.

## `except Exception`: было 27, стало 21

Сужено (6): чтение реестра процессов → `(OSError, ValueError)`; регистрация
процесса → `(psutil.Error, OSError)`; запуск `taskkill` →
`(OSError, SubprocessError)`; ожидание кода выхода → `OSError`; закрытие файла
лога → `OSError`; разбор тела HTTP-ошибки llama-server →
`(OSError, ValueError, AttributeError, HTTPException)`. Ещё один `pass` в
`finally` задачи заменён на запись в лог уровня debug (ловит по-прежнему всё).

Оставлено как граница подсистемы (21):
- `promptgen_diag.py` (12): замеры VRAM/RAM/процессов и разбор логов. Сбой
  диагностики не должен ломать генерацию. Здесь возможны `AttributeError`
  (например, `psutil.disk_io_counters()` возвращает `None`) и ошибки
  драйвера, поэтому точный список не выводится из чтения кода.
- `promptgen.py` (5): верх рабочего потока `_run`, уборка при старте,
  вызов внешнего `pre_start()` (выгрузка моделей ComfyUI), запись истории,
  диагностика после остановки.
- `promptgen_process.py` (4): отчёт `_kill_tree` по psutil, `_stop_process`,
  уборка в `sweep_stale`, Job Object.

Правило `BLE001` в `ruff` и строгий mypy для новых модулей **не включал**:
в песочнице нет ни `ruff`, ни `mypy`, а включение вслепую может уронить CI.
Если захотите — `promptgen_base`, `promptgen_job`, `promptgen_stream` самые
подходящие кандидаты; `BLE001` потребует `per-file-ignores` на все
остальные файлы проекта (по правилу R11.2 плана).

## Тесты

- `tests/imagine/test_promptgen.py` — **без правок**.
- Новый `tests/imagine/test_promptgen_modules.py` (83 случая с параметризацией, без Qt и
  сети, запускается и на Windows): все исходные имена остаются на фасаде и
  это те же объекты; слои без циклов; Qt не импортируется; сужения `except`
  (битый реестр, мёртвый pid, HTTP-ошибка с мусором и не-объектом в теле).
  Один тест (`taskkill`) только для Windows и на Linux пропускается.

## Что я проверял и чего нет

Проверял в песочнице на Linux (там `test_promptgen.py` не пропускается
целиком): **66 из 68 тестов файла проходят** — до и после R10 один и тот
же набор. Запускал самодельной заглушкой вместо pytest (самого pytest в
песочнице нет). Два теста, `test_http_endpoints` и
`test_http_start_passes_image_name_to_history`, там не выполняются: нужен
`fastapi`. Их выполнение остаётся за вами.

Не проверял: настоящий `pytest`, Windows, `build_exe.bat`, реальный
llama-server, `taskkill`-ветку.

## Что проверить вам

1. WSL/Linux: `pytest tests/imagine/test_promptgen.py` — **68 passed**,
   включая два HTTP-теста. Это то самое условие R0 для R10.
2. Windows: `pytest tests/imagine tests/shared/test_app_paths.py` — новые
   тесты зелёные, `test_promptgen.py` пропущен, как раньше.
3. Imagine руками: сгенерировать промпт, нажать «Отмена» на этапе загрузки
   модели, а потом на этапе генерации; после остановки не должно остаться
   процессов `llama-server`.
4. Просмотреть `promptgen.log`: имя логгера `imagine.promptgen` и формат
   строк прежние.
5. Сборки `build_exe.bat core` / `full` и отдельная сборка Imagine: новые
   модули подключаются статическими импортами из `promptgen.py`.
