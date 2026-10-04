# Baseline R0 — контракты, которые рефакторинг не должен менять

Составлено на этапе R0 (раунд 2 рефакторинга) по коду из архива
`ComfyUIStudio-08a1c61d…`. Это не пожелания, а описание того, **как оно
работает сейчас**; каждый пункт подкреплён тестом из `tests/` (указан в
скобках). Если после этапа R3–R7 тест из списка приходится править —
изменилось поведение, а не только структура кода.

## 1. Словарь `ResourceMonitor.stats_updated` (контракт для R6)

Сигнал `Signal(dict)` испускается **ровно один раз за каждый `_poll()`**
(раз в 2 с, `RESOURCE_POLL_INTERVAL_MS = 2000`). Набор ключей:

| Ключ | Тип | Когда присутствует |
|---|---|---|
| `cpu_percent` | float | psutil установлен и чтение удалось |
| `ram_percent`, `ram_used_gb`, `ram_total_gb` | float | то же |
| `gpu_available` | bool | **всегда** (`False`, если NVML недоступен или чтение упало) |
| `gpu_name` (str), `gpu_util` (int %), `gpu_temp` (int °C), `gpu_mem_used_gb`, `gpu_mem_total_gb` (float) | — | только при `gpu_available == True` |
| `queue_running`, `queue_pending` | int | порт ComfyUI известен **и** `/queue` ответил |
| `queue_completed_session` | int | вместе с `queue_*` — **даже если `/history` не ответил** |
| `queue_eta_seconds` | float \| None | вместе с `queue_*`; `0.0` — очередь пуста, `None` — посчитать нельзя («оценка…»), иначе секунды |

Если порт неизвестен (ComfyUI не запущен) — ключей `queue_*` нет, а всё
состояние сессии/ETA сбрасывается (`_session_*`, `_current_progress`,
`_progress_for_id`, `_avg_sec_per_step`, `_stall_polls`, `_logged_*`).
Если ComfyUI запущен, но `/queue` не ответил — `queue_*` нет, но состояние
сессии **не** сбрасывается.

Потребители словаря: `MainWindow._on_stats_updated` → `tray.update_stats`,
`browser_page.update_stats`, `settings_page.resource_bar.update_stats`;
`format_stats_tooltip` (подсказка трея, обрезается до 120 символов).

Тесты: `tests/launcher/test_resource_monitor_poll.py`.

Прочие зафиксированные правила `_poll`/`feed_log_line`:

- «Готово за сессию» — по разности множеств id: id из `/history`, которых
  не было на старте сессии и которых **нет** в `running_ids`; повторный
  опрос не удваивает счётчик.
- Смена единственного running-задания сбрасывает `_current_progress`
  (иначе у нового задания «висят» цифры предыдущего → ложные «< 1 с»).
- `_current_progress` относится к заданию только если running ровно одно.
- Строки tqdm разбираются регуляркой `_TQDM_PROGRESS_RE` (`it/s` и
  `s/it`), ANSI-последовательности вырезаются, `total <= 0` и `rate <= 0`
  игнорируются.

## 2. `config.json` (контракт для R7)

Путь — `CONFIG_PATH` в `launcher/core/constants.py` (сейчас
`%APPDATA%\ComfyUILauncher\config.json`).

**Ключи верхнего уровня.** В `DEFAULT_CONFIG` — 10:
`root_path`, `script`, `port` (8188), `disable_auto_launch` (True),
`sync_comfy_theme` (False), `interface` (`"comfyui"` | `"imagine"`),
`imagine` {`port` 7860, `dev_mode` False}, `env_vars` {}, `log_level`
(`"INFO"`), `remote` {`enabled` False, `port` 7861, `host`
`"127.0.0.1"`, `fcm_service_account_path` None}.

**Плюс ключ, которого нет в `DEFAULT_CONFIG`:** `launch_args` —
`{arg_id: {"enabled": bool, "value": str}}`, его пишет
`ComfyUISettingsPage.collect()`, читает `build_extra_launch_args`. При
R7 это такая же секция, как остальные.

**Поведение чтения/записи (как есть):**

1. Слияние **поверхностное**: `DEFAULT_CONFIG.copy()` + `update(data)`.
   Если в файле есть секция `remote`, недостающие её ключи из значений по
   умолчанию **не** подставляются — весь код читает их через
   `.get(key, default)`.
2. Неизвестные ключи верхнего уровня при чтении сохраняются.
3. Битый JSON → значения по умолчанию (исключение логируется, не
   пробрасывается). Ошибка записи в `save_config` тоже только логируется.
4. Запись: UTF-8, `ensure_ascii=False`, `indent=2`, **не атомарная**
   (в отличие от `json_store.save_json` у Prompt Builder).

**Латентные проблемы, найденные при R0 (не лечим до R7):**

- *Алиасинг вложенных словарей.* Из‑за поверхностного `.copy()`
  `cfg["remote"]` — тот же объект, что `DEFAULT_CONFIG["remote"]`. Правка
  вложенной секции «на месте» испортила бы значения по умолчанию.
  Сейчас код так не делает (страницы собирают секции заново через
  `collect()`), поэтому проблема не проявляется. Тест
  `test_load_config_shares_nested_dicts_with_defaults_known_quirk` — его
  нужно **осознанно перевернуть** в R7b.
- *Два независимых снимка конфига.* `MainWindow.cfg` и
  `AppSettingsDialog.cfg` — разные словари. Автосохранение
  (`_auto_save`) делает `cfg.update(...collect())`, т. е. **целиком
  заменяет** секции `imagine`/`remote`/`env_vars`/`launch_args` данными
  со страницы — ключи секции, о которых страница не знает, пропадут.
  Окно при этом свежих значений не видит — отсюда обход
  `fresh_cfg = load_config()` в `MainWindow._start_remote` (баг от
  2026-09-06). R7a (`ConfigStore`) убирает причину.

Тесты: `tests/launcher/test_config_load_save.py`,
`tests/launcher/test_main_window_flow.py::test_start_remote_reads_fresh_config_*`.

## 3. Цепочка запуска и Remote в `MainWindow` (контракт для R3–R5)

Зафиксировано в тестах (R0: `test_main_window_flow.py`, 38 тестов). **После R4/R5**
цепочка запуска живёт в `launcher/ui/launch_controller.py` (тесты —
`tests/launcher/test_launch_controller.py`, ожидания перенесены без
изменений), Remote — в `remote_controller.py`; в `test_main_window_flow.py`
остались проверки того, что за окном (делегирование, страницы, браузер, тема):

- `_on_launch`: `prepare_launch_script` → `ComfyProcess(root, script,
  log_bridge, env_overrides=…)` → `start()` → прогресс → `launch_watcher.
  start(port, process)`; при `OSError` — статус в настройках и **без**
  запуска; палитра ComfyUI синхронизируется только при
  `sync_comfy_theme`.
- `_on_server_ready`: `interface == "imagine"` → `_start_imagine()`, иначе
  встроенный браузер на порт ComfyUI.
- Ошибка старта Imagine (`RuntimeError`) → откат **обоих** процессов
  (Imagine и ComfyUI), статус «Не удалось запустить Imagine: …».
- `_on_launch_cancelled` / `_stop_and_show_settings` обнуляют
  `imagine_process`, но объект `comfy_process` остаётся (его
  `is_running()` уже `False`); `_on_server_failed` не обнуляет ни один.
- Remote: опрос готовности — раз в 300 мс, **20 попыток** (~6 с), затем
  «не поднялся вовремя»; преждевременный выход процесса → сообщение с
  кодом выхода и `remote_process = None`; LAN-URL показывается только при
  `host != "127.0.0.1"`.
- Pairing / список устройств / отзыв — **синхронные** вызовы
  `call_local_api` из GUI-потока; при первой же ошибке отзыв прерывается
  и список не обновляется. (**Сделано в R4:** блок живёт в `launcher/ui/remote_controller.py`,
  вызовы идут в рабочем потоке — это единственное намеренное изменение
  поведения; тесты — `tests/launcher/test_remote_controller.py`.)

## 4. Prompt Builder (контракт для R9)

- `logic.py` (чистая логика, без Qt): валидация тегов, разбор/сборка
  `name:strength`, `CharacterEntry` (строка ↔ словарь), миграция старого
  формата LoRA (`lora`/`lora_strength` → `loras: [...]`).
  Тесты: `tests/prompt_builder/test_logic.py` (34).
- `json_store.py`: UTF-8, кириллица без экранирования, `indent=2`, `\n` в
  конце, атомарная запись через временный файл + `os.replace`, резервная
  копия `*.bak-YYYYMMDD-HHMMSS` перед записью с ротацией до
  `get_backup_keep()` (0 → не оставлять даже только что созданную).
  Тесты: `tests/prompt_builder/test_json_store.py` (13).

## 5. Что R0 намеренно НЕ покрывает

- Внешний вид окон, трей, тема, WebEngine.
- `promptbuilder_tab.py` (Qt-редактор) — перед R9 понадобятся тесты на
  операции над деревом; они появятся вместе с выносом в `tree_ops.py`.
- WebSocket-канал `ResourceMonitor` — уже покрыт
  `test_system_monitor_ws.py`.
- Remote-слой (`comfyui_studio/remote`) — отдельный набор в `tests/remote`.

## 6. Особенности запуска тестов (найдены при первом прогоне на Windows)

- **Один пакет `tests`.** Каталоги `tests/` и `tools/promptvault/tests/`
  нельзя делать пакетами одновременно: оба получают имя `tests`, и в
  общем прогоне `pytest` второй не находит подмодулей
  (`No module named 'tests.imagine'`). Поэтому в
  `tools/promptvault/tests/` **нет** `__init__.py` — не возвращайте его.
  (Этап R11.5 — перенос тестов PromptVault в `tests/promptvault` —
  уберёт саму причину.)
- **Тесты Remote не должны слать настоящие push.** Для всего
  `tests/remote` включена автоматическая подмена `fcm.send_generation_push`
  и пути к хранилищу устройств (`tests/remote/conftest.py`, fixture
  `sent_pushes`). Новый тест, который проходит через
  `GenerationWatcher` или `fcm`, ничего дополнительно делать не должен —
  но проверять, что в push ушло, можно через `sent_pushes`.
- **`tests/imagine/test_promptgen.py` на Windows пропускается целиком**
  (`skipif(os.name == "nt")` — поддельный llama-server это shebang-
  скрипт): 68 тестов. До R10 их нужно один раз прогнать в WSL/Linux.
- **Пути из Qt.** `QUrl.toLocalFile()` всегда отдаёт `/`, даже на
  Windows; в тестах пути из Qt сравнивайте через `Path(...)`, а не как
  строки с `str(path)`.
- **Часы Windows.** До Python 3.13 `time.monotonic()` на Windows имеет
  шаг ~15,6 мс; тест, которому нужна положительная разница времени между
  двумя событиями, должен явно развести их (`qtbot.wait(50)`).

## R7: конфигурация

Источник правды — `launcher/core/config_store.py::ConfigStore`
(`MainWindow.config`). `MainWindow.cfg` удалён: окно описывает текущую
сессию ComfyUI через `launch_controller.cfg` (снимок на момент «Запустить»).
Формат `config.json` и результат `load_config()` не менялись
(`tests/launcher/test_config_load_save.py`). Тесты —
`tests/launcher/test_config_store.py`.
