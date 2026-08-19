# ComfyUI Studio — дорожная карта архитектурного рефакторинга

**Репозиторий:** github.com/SlavaGY/ComfyUIStudio
**Дата составления:** 2026-08-16 (обновлено: добавлен этап 0, этап cleanup, HTTP/WebSocket разделены на два этапа)
**Обновлено:** 2026-08-17 — этапы 0–4 выполнены (см. отметки «✅ Выполнено» у каждого этапа), дерево ниже приведено в соответствие с фактическим состоянием репозитория после этапа 4.
**Обновлено:** 2026-08-19 — задокументирован пост-этап-4 фикс мигания встроенного интерфейса ComfyUI (Native Window Occlusion + UPX ломал Qt6-бинарники), см. конец раздела "4. Единое дерево настроек".
**Обновлено:** 2026-08-19 — этап 5 (cleanup) выполнен: удалён sys.path-хак в корневом `main.py`, удалены дубли `shared_theme.py`/`shared_language.py` в `prompt_builder/`/`promptvault/` (импортёры переведены на `comfyui_studio.shared_theme`/`shared_language`), подтверждено отсутствие старых `from app.` импортов и что все 32 файла тестов уже на `comfyui_studio.promptvault`, `tools/promptvault/pyproject.toml` слит в корневой `pyproject.toml` (dev-группа + pytest/coverage/ruff/mypy) и удалён, README.md и CONTRIBUTING.md/.pre-commit-config.yaml приведены в соответствие с текущей раскладкой. Не выполнено в рамках этапа 5: слияние `requirements.txt` с `pyproject.toml` (не было в explicit-чек-листе), реальная пересборка PyInstaller-профилей (нет Windows/Qt-окружения в песочнице — нужно проверить руками), и standalone-сборки `tools/prompt_builder/build_windows.bat`/`tools/promptvault/build.bat` — обнаружены сломанными (ссылаются на дореформенную раскладку `app\main.py`), но их починка не входила в объём этапа 5 и задокументирована как открытое ограничение в README.

**Текущее состояние (по факту из репозитория, после этапа 4):**

```
ComfyUIStudio/
├── main.py                        единая точка входа (один QApplication),
│                                   sys.path-хак (PROMPTVAULT_DIR/TOOLS_DIR/
│                                   ROOT_DIR) ещё не удалён — мёртвый код,
│                                   см. этап 5
├── pyproject.toml                 dependencies + optional-dependencies.
│                                   promptvault (torch, sentence-transformers);
│                                   numpy оставлен обязательной зависимостью —
│                                   см. этап 3
├── requirements.txt                полный комплект, как и раньше — пока не
│                                   вычищен/не объединён с pyproject.toml
│                                   (см. этап 5)
├── ComfyUIStudio-core.spec        без torch/sentence-transformers/
│                                   transformers/tokenizers (этап 3)
├── ComfyUIStudio-full.spec        полный комплект (этап 3)
├── build_exe.bat                  build_exe.bat [core|full] (этап 3)
├── README.md                      частично обновлён под этап 3 (установка,
│                                   сборка); остальное не тронуто
├── assets/                        icon.ico/icon.png лаунчера
├── comfyui_studio/                общее пространство имён (этап 2)
│   ├── __init__.py                 НОВОЕ (этап 4): __version__ — используется
│   │                                на странице General → Updates
│   ├── i18n.py, shared_language.py, shared_theme.py
│   ├── themes/                    6 .qss тем + theme_manager.py
│   ├── launcher/                  бывший comfyui_launcher.py (этап 1)
│   │   ├── core/                  comfy_process (+env_overrides, этап 4),
│   │   │                          comfy_api, config, system_monitor,
│   │   │                          logging_setup (+set_console_log_level,
│   │   │                          этап 4), autostart.py — НОВОЕ (этап 4,
│   │   │                          автозапуск через реестр Windows)
│   │   ├── ui/
│   │   │   ├── launcher_window.py
│   │   │   ├── settings_page.py    ПЕРЕРАБОТАН (этап 4): теперь только
│   │   │   │                       домашний экран (запуск/лог/статус/
│   │   │   │                       другие инструменты) + кнопка
│   │   │   │                       "Настройки...", открывающая
│   │   │   │                       AppSettingsDialog немодально
│   │   │   │                       (show/raise, не exec — иначе окно
│   │   │   │                       PromptVault пряталось за диалогом);
│   │   │   │                       заодно окна других инструментов
│   │   │   │                       теперь реально закрываются
│   │   │   │                       (WA_DeleteOnClose + gc.collect())
│   │   │   ├── settings/            НОВОЕ (этап 4): единое дерево настроек
│   │   │   │   ├── app_settings_dialog.py   QTreeWidget + QStackedWidget
│   │   │   │   ├── general_page.py          Language/Theme/Startup/Updates
│   │   │   │   ├── comfyui_page.py          Installation/Port/Script/
│   │   │   │   │                            Arguments/Environment (бывший
│   │   │   │   │                            LaunchArgsDialog — удалён как
│   │   │   │   │                            отдельный класс)
│   │   │   │   ├── prompt_builder_page.py   заготовка (пока пусто)
│   │   │   │   ├── promptvault_page.py      Database (новое) + кнопка-мост
│   │   │   │   │                            в существующее окно настроек
│   │   │   │   │                            PromptVault — см. этап 4,
│   │   │   │   │                            "По факту реализации"
│   │   │   │   ├── advanced_page.py         Logging level/диагностика/сброс
│   │   │   │   │                            + Application (Studio-wide
│   │   │   │   │                            Restart/Quit, см. ниже)
│   │   │   └── widgets/            browser_page, tray, resource_bar,
│   │   │                          log_panel, launch_watcher
│   │   └── integration/           tool_registry, comfy_theme
│   ├── prompt_builder/            бывший tools/prompt_builder (этап 2);
│   │                              main.py — тулбар (Сохранить всё/
│   │                              Открыть файл) вместо меню Файл/
│   │                              Справка; pb_settings.py — НОВОЕ
│   │                              (расширение этапа 4): папка
│   │                              расширения, число бэкапов; свои
│   │                              дубли shared_theme.py/
│   │                              shared_language.py/pb_i18n.py пока не
│   │                              вычищены — см. этап 5
│   └── promptvault/               бывший tools/promptvault/app (этап 2)
│       ├── core/                  embedding.py — is_available()/
│       │                          gpu_available() доведены до UI на этапе 3
│       ├── ui/                    settings_window.py — этап 3: чекбокс
│       │                          семантического поиска дизейблится, если
│       │                          зависимости не установлены; +параметр
│       │                          standalone (этап 4) — скрывает
│       │                          Restart/Quit при работе внутри Studio;
│       │                          main_window.py тоже +standalone;
│       │                          доступ из лаунчера — promptvault_page.py
│       ├── resources/, themes/
│       └── (свои дубли shared_theme.py/shared_language.py — см. этап 5)
└── tools/                         ЛЕГАСИ, физически ещё в репозитории,
    ├── prompt_builder/            но main.py их больше не импортирует
    └── promptvault/               (актуальный код — под comfyui_studio/
        └── tests/                 выше); tests/ уже на comfyui_studio.
                                    promptvault (см. этап 2) и
                                    pyproject.toml с dev/pytest/ruff/mypy
                                    пока живут здесь — слияние с корневым
                                    запланировано на этап 5
```

Документ фиксирует согласованные направления доработки и порядок их выполнения. Это план, а не патч: рефактор такого масштаба на живом Qt-коде безопаснее делать пошагово, с проверкой каждого шага, чем одним большим diff'ом.

---

## 0. Зафиксировать текущее поведение (baseline) — ✅ Выполнено

Первый этап — не про рефактор, а про то, чтобы было с чем сверяться после каждого следующего шага.

- **Чек-лист ручного smoke-теста** до любых правок: запуск ComfyUI из лаунчера, открытие Prompt Builder и PromptVault из лаунчера (in-process, через `register_in_process_app`), синхронизация темы между всеми тремя окнами (включая live-sync через `QFileSystemWatcher`), переключение языка, сохранение/загрузка настроек через `SettingsPage`/`LaunchArgsDialog`, работа LoRA-дропдауна, действия из `TrayIcon`. Этот же чек-лист повторяется после этапов 1, 2 и 5 — три точки, где риск сломать что-то незаметно самый высокий.
- **Тестовое покрытие лаунчера и Prompt Builder.** У PromptVault уже 31 тестовый файл (`tools/promptvault/tests/`), у `comfyui_launcher.py` и `tools/prompt_builder/` — судя по структуре репозитория, тестов нет вообще. Прежде чем резать 2657-строчный файл на 15 модулей, стоит зафиксировать поведение хотя бы чистых функций без Qt-зависимостей: `fetch_queue_status`, `fetch_history_ids`, `count_steps_in_prompt`, `is_port_open`, `build_extra_launch_args`, `validate_portable_root`, `guess_default_script` — это чистая логика, которую легко покрыть unit-тестами до переноса, и тесты продолжат работать после (только поменяются пути импорта).
- **Референсная сборка.** Собрать текущий `build_exe.bat`/`ComfyUIStudio.spec` как есть, зафиксировать размер `.exe` и факт «работает» — это база для сравнения после этапа 3 (опциональные зависимости) и этапа 5 (cleanup/packaging).
- **Снимок графа импортов.** Список всех `import`/`from` внутри `comfyui_launcher.py` и `tools/prompt_builder/` — черновая карта того, что должно остаться рабочим после этапа 1.

---

## 1. Разбиение `comfyui_launcher.py` — ✅ Выполнено

### Целевая структура

```
comfyui_studio/launcher/
├── core/
│   ├── comfy_process.py     ComfyProcess, ProcessLogBridge, _LogReaderThread,
│   │                        launch_external_app, _log_external_app_exit,
│   │                        ExternalApp, resolve_external_launch
│   ├── comfy_api.py         fetch_queue_status, fetch_history_ids,
│   │                        count_steps_in_prompt, is_port_open
│   │                        (в этапе 6 этот модуль станет ComfyAPIClient)
│   ├── config.py            load_config, save_config, find_run_scripts,
│   │                        validate_portable_root, guess_default_script,
│   │                        build_extra_launch_args, prepare_launch_script
│   ├── system_monitor.py    ResourceMonitor, format_eta_seconds,
│   │                        format_stats_tooltip, level_color
│   └── logging_setup.py     setup_logging, resource_path, app_base_dir
├── ui/
│   ├── launcher_window.py   MainWindow, create_window, main
│   ├── settings_page.py     SettingsPage, LaunchArgsDialog
│   ├── browser_page.py      BrowserPage, RestrictedWebPage
│   ├── tray.py              TrayIcon
│   └── widgets/
│       ├── resource_bar.py  ResourceBar
│       ├── log_panel.py     LogPanel
│       └── launch_watcher.py LaunchWatcher
└── integration/
    ├── tool_registry.py     register_in_process_app + реестр фабрик
    └── comfy_theme.py       sync_comfyui_color_palette
```

### Порядок выполнения (важен из-за внутренних зависимостей)

1. **`core/logging_setup.py`** — не зависит ни от чего внутри файла, выносится первым, минимальный риск.
2. **`core/config.py`** — зависит только от stdlib + `logging_setup`.
3. **`core/comfy_api.py`** — независим (чистые HTTP-функции), но именно этот модуль будет расширяться в этапе 6, поэтому имеет смысл вынести его сразу после конфига, до UI.
4. **`core/comfy_process.py`** — зависит от `comfy_api` (для проверки готовности порта) и `config` (для аргументов запуска).
5. **`core/system_monitor.py`** — зависит от `psutil`/`nvidia-ml-py`, независим от остальных core-модулей.
6. **`ui/widgets/*`** — `ResourceBar` зависит от `system_monitor`, `LogPanel` от `comfy_process` (через `ProcessLogBridge`), `LaunchWatcher` от `config`.
7. **`ui/browser_page.py`**, **`ui/tray.py`**, **`ui/settings_page.py`** — зависят от `config` и виджетов из шага 6.
8. **`ui/launcher_window.py`** — собирает всё вместе, выносится последним.
9. **`integration/tool_registry.py`** и **`integration/comfy_theme.py`** — по сути независимы, можно вынести в любой момент после шага 1, но логично — последними, так как `register_in_process_app` дергается из `launcher_window.py`.

**Проверка на каждом шаге:** после выноса каждого модуля — `python main.py` должен по-прежнему запускаться без ImportError, а не «разберём всё, потом соберём». Прогонять чек-лист из этапа 0 после шагов 8 и 9.

---

## 2. Единое пространство имён для трёх инструментов — ✅ Выполнено

### Целевая структура

```
comfyui_studio/
├── __init__.py
├── launcher/            бывший comfyui_launcher.py (см. этап 1) + i18n.py,
│   │                    shared_theme.py, shared_language.py, themes/
│   └── __init__.py
├── prompt_builder/      бывший tools/prompt_builder, без изменений в логике —
│   └── __init__.py      меняется только путь пакета
├── promptvault/         бывший tools/promptvault/app → promptvault
│   └── __init__.py
└── main.py              точка входа комплекта
```

### Что решает

Сейчас `main.py` вручную добавляет в `sys.path` папки `PROMPTVAULT_DIR`, `TOOLS_DIR`, `ROOT_DIR`, чтобы `from app.main import create_window` и `from prompt_builder.main import create_window` резолвились — именно ради того, чтобы PyInstaller мог статически проследить граф импортов (это подробно описано в комментариях самого `main.py`). После переноса под общий namespace-пакет `comfyui_studio` все три инструмента импортируются одинаково:

```python
from comfyui_studio.launcher import create_window as create_launcher_window
from comfyui_studio.prompt_builder import create_window as create_prompt_builder_window
from comfyui_studio.promptvault import create_window as create_promptvault_window
```

Сам `sys.path`-хак не удаляется на этом этапе — он становится мёртвым кодом, который явно вычищается на этапе 5 (см. ниже), а не тихо остаётся рядом с новой структурой.

### Нюанс с PromptVault — ✅ сделано, шире плана

Пакет `app` (`tools/promptvault/app/`) переименован и перенесён в `comfyui_studio/promptvault/` — директории `app/` в `tools/promptvault/` больше не существует. По факту find-and-replace задел и тесты: все файлы в `tools/promptvault/tests/` уже импортируют `from comfyui_studio.promptvault....`, а не `from app....`, как было в исходном плане (там это отдельно откладывалось на этап 5). Сами тесты физически остаются в `tools/promptvault/tests/` (рядом со своим `pyproject.toml`, где настроен `pythonpath = [".", "../.."]`, чтобы импорт резолвился и оттуда) — перенос самой папки тестов под `comfyui_studio/` (если понадобится) и слияние `tools/promptvault/pyproject.toml` с корневым `pyproject.toml` (этап 3) — по-прежнему на этапе 5.

### Общие модули (`i18n.py`, `shared_theme.py`, `shared_language.py`, `themes/`) — ✅ подняты на уровень пакета, дубли пока не вычищены (по плану)

Общие копии подняты на уровень пакета, как и планировалось:

```
comfyui_studio/
├── shared_theme.py
├── shared_language.py
├── i18n.py
└── themes/
```

Дублирующиеся копии по-прежнему физически лежат в `comfyui_studio/prompt_builder/` (`shared_language.py`, `shared_theme.py`, `pb_i18n.py`) и в `comfyui_studio/promptvault/` (`shared_language.py`, `shared_theme.py`) — как и было запланировано, они помечены как подлежащие удалению, а само удаление и замена на импорт из `comfyui_studio.shared_theme`/`comfyui_studio.shared_language` по-прежнему запланированы на этап 5, вместе с остальной уборкой (это не забыли — так и было задумано, см. план этапа 5).

---

## 3. Опциональные зависимости — ✅ Выполнено

*(может выполняться параллельно с этапом 4 — оба independent после этапа 2)*

> **По факту реализации — одно отступление от плана ниже:** `numpy` в
> итоге остался в обязательных зависимостях, а не в группе
> `promptvault`. Причина обнаружилась только при реализации:
> `comfyui_studio/promptvault/core/embedding.py` импортирует `numpy`
> безусловно на уровне модуля (не лениво внутри функций, как
> `torch`/`sentence_transformers`), а `GalleryManager` безусловно
> импортирует `embedding` — то есть PromptVault физически не откроется
> без `numpy` уже сейчас, независимо от того, в какую группу его
> положить в `pyproject.toml`. Сделать сам `numpy` честно опциональным —
> это отдельная переработка (вынос байтовой (де)сериализации
> эмбеддингов из `embedding.py` в чистый Python/array), не входящая в
> объём этого этапа; сам `numpy` лёгкий и не создаёт той проблемы
> (гигабайты в exe, конфликт версий), ради которой затевался этот этап,
> поэтому решено было не блокировать этап 3 ради этого и оставить как
> есть, зафиксировав здесь как осознанное решение, а не забытый пункт.

### Целевая структура

```
pyproject.toml
├── [project] dependencies = ["PySide6==6.11.1", "psutil>=5.9", "nvidia-ml-py>=12.0", "numpy>=1.26"]
└── [project.optional-dependencies]
    └── promptvault = ["torch>=2.4", "sentence-transformers>=3.0"]
```

Установка:

- `pip install .` — только Launcher + Prompt Builder (PromptVault запускается, но без семантического поиска);
- `pip install .[promptvault]` — полный комплект.

### Runtime-защита — фактическая реализация

При ревизии кода перед этим этапом выяснилось, что защита на уровне
`comfyui_studio/promptvault/core/embedding.py` уже была устроена
заметно аккуратнее, чем черновой набросок выше: `torch` и
`sentence_transformers` там и так импортировались лениво, внутри
функций (`get_model`, `gpu_available`, `_pick_device`,
`_load_and_verify_model`), а не на уровне модуля — то есть
`SEMANTIC_SEARCH_AVAILABLE`-флаг из наброска был бы шагом назад по
сравнению с уже существующими `is_available()` (динамическая проверка,
а не разовый флаг при импорте) и `_torch_version_compatible()`
(проверка версии через `importlib.metadata`, без реального импорта
`torch`, — именно тот механизм, который уже обрабатывал сценарий
«версия 2.1.2 при требовании >=2.4» из этого пункта). Флаг-константу
поэтому решили не добавлять и не трогать сам `embedding.py`.

Не хватало только связи этого механизма с UI — это и было сделано:

- `GalleryManager.semantic_search_available()` — тонкая обёртка над
  `embedding.is_available()`.
- `SettingsWindow._apply_semantic_search_availability()` — дизейблит
  чекбокс «Enable semantic search», выбор модели, выбор устройства и
  кнопку пересчёта эмбеддингов, если зависимость не установлена, и
  подменяет текст подсказки на «Semantic search needs optional
  dependencies... `pip install .[promptvault]`». Раньше чекбокс можно
  было включить без установленного `sentence-transformers` без всякого
  видимого эффекта — теперь недоступность видна сразу в настройках, а
  не только как «почему-то не находит по смыслу» в процессе работы.

### Влияние на сборку — фактическая реализация

Два профиля `build_exe.bat` / `.spec` созданы:

- `ComfyUIStudio-core.spec` — без `torch`/`sentence-transformers`, явный `excludes` на torch/sentence_transformers/transformers/tokenizers в `Analysis()` (PyInstaller иногда затягивает их транзитивно через PromptVault, даже если код их не импортирует на этом пути);
- `ComfyUIStudio-full.spec` — полный сборочный профиль с `torch`/`sentence-transformers`/`transformers`/`tokenizers`.

**Незапланированная находка по пути:** оба файла пришлось не просто
скопировать/переименовать из старого `ComfyUIStudio.spec`, а
пересобрать заново — тот всё ещё ссылался на пути `tools\prompt_builder\
assets`/`tools\promptvault\app\resources`, которых с этапа 2 физически
не существует (сам код, например `comfyui_studio/promptvault/config.py`,
уже ищет ресурсы по новым путям `_MEIPASS/comfyui_studio/promptvault/...`
— со старым `.spec` сборка была бы попросту сломана, а не просто
неоптимальна). Заодно пересобран и `build_exe.bat` — принимает аргумент
`core`/`full`, ставит зависимости через `pyproject.toml` вместо
`requirements.txt` (`pip install .` / `pip install .[promptvault]`), и
проверяет существование `comfyui_studio\prompt_builder\main.py` /
`comfyui_studio\promptvault\main.py` вместо путей `tools\...`. Старый
корневой `ComfyUIStudio.spec` удалён — заменён этими двумя файлами.

Полная проверка, что оба профиля реально собираются и запускаются
именно на Windows (у нас была возможность только проверить синтаксис
`.spec`/`pyproject.toml` и корректность `find_packages()`, не саму
сборку PyInstaller), по-прежнему запланирована на этапе 5 (packaging),
после того как `tools/` и `sys.path`-хак будут вычищены.

---

## 4. Единое дерево настроек — ✅ Выполнено

*(может выполняться параллельно с этапом 3 — оба independent после этапа 2)*

### Целевая структура `SettingsPage`

```
ComfyUI Studio Settings
├── General
│   ├── Language              ← уже было (shared_language.py)
│   ├── Theme                 ← уже было (shared_theme.py, theme_manager.py)
│   ├── Startup                ← новое: автозапуск при старте Windows
│   └── Updates                 ← новое: проверка версии (заготовка, не приоритет)
├── ComfyUI
│   ├── Installation           ← уже было (validate_portable_root, find_run_scripts)
│   ├── Port                    ← уже было, было в LaunchArgsDialog — перенесено сюда
│   ├── Startup script          ← уже было (guess_default_script)
│   ├── Arguments               ← уже было — весь бывший LaunchArgsDialog
│   │                             (--lowvram/--listen/--reserve-vram и т.д.)
│   └── Environment              ← новое: переменные окружения процесса ComfyUI
├── Prompt Builder
│   └── (пусто на сегодня — точка расширения под будущие настройки инструмента)
├── PromptVault
│   ├── Database                ← новое: путь к SQLite, backup
│   └── Search & Performance      ← кнопка-мост в уже существующее окно настроек
│                                   PromptVault (см. "По факту реализации" ниже —
│                                   не задублировано заново)
└── Advanced
    └── Logging level, диагностика, сброс настроек к дефолту
```

### По факту реализации

**Архитектурное решение, не описанное в черновике выше:** `SettingsPage`
не превратилась в дерево сама по себе — она осталась "домашним" экраном
лаунчера (запуск/лог/статус/другие инструменты), а всё дерево настроек
выделено в отдельный модальный `AppSettingsDialog`
(`ui/settings/app_settings_dialog.py`), открываемый с домашнего экрана
кнопкой «Настройки...». Так разведены по смыслу два раньше слипшихся
назначения одного и того же экрана — "запустить ComfyUI прямо сейчас"
и "настроить, как именно" — без этого пришлось бы либо держать
запуск/лог внутри дерева настроек (странно), либо на каждый чих
пересобирать домашний экран целиком.

Реализовано в `comfyui_studio/launcher/ui/settings/`:
`general_page.py`, `comfyui_page.py`, `prompt_builder_page.py`,
`promptvault_page.py`, `advanced_page.py`, `app_settings_dialog.py`
(контейнер `QTreeWidget`+`QStackedWidget`). Бывший `LaunchArgsDialog` как
отдельный класс удалён — его содержимое (весь `LAUNCH_ARG_DEFS`) стало
разделом "Arguments" `ComfyUISettingsPage`, без изменения самой логики
сборки аргументов (`build_extra_launch_args` в `core/config.py` не
тронута).

Startup — `core/autostart.py`, автозапуск через
`HKEY_CURRENT_USER\...\Run` (без прав администратора), поддержан только
на Windows — `autostart.is_supported()` на других ОС просто прячет
чекбокс за пояснением. Environment — новое поле `cfg["env_vars"]`,
реально доводится до процесса: `ComfyProcess.start()`
(`core/comfy_process.py`) накладывает эти переменные поверх
`os.environ` при запуске. Advanced/Logging — `core/logging_setup.py`
получил `set_console_log_level()`, применяется один раз при старте
(`MainWindow.__init__`) и живьём из настроек; файловый лог остаётся
всегда `DEBUG` независимо от этой настройки.

**Отступление от плана по разделу PromptVault:** Search и Performance
НЕ продублированы внутри дерева лаунчера. При реализации выяснилось,
что `comfyui_studio/promptvault/ui/settings_window.py` (класс
`SettingsWindow`) уже полностью реализует оба раздела (toggle
семантического поиска — см. этап 3; размер страницы ленивой загрузки;
автоочистка миниатюр/логов — `settings.py`, класс `AppSettings`), и
конструируется с обязательным параметром `toolbar: Toolbar`,
принадлежащим уже открытому `MainWindow` PromptVault. Реализовать те же
разделы ещё раз внутри `PromptVaultSettingsPage` значило бы либо
дублировать несколько сотен строк рабочего кода в двух местах (риск
рассинхронизации), либо отдельно рефакторить сам `SettingsWindow`
PromptVault на переиспользуемые куски — это уже не объём этапа 4.
Вместо этого `PromptVaultSettingsPage` даёт: (1) новый раздел
"Database" — путь к SQLite (единая на весь PromptVault база, не
привязанная к открытой в моменте папке — `DB_PATH`, всегда
`~/.promptvault/promptvault.db`), кнопки "Открыть папку" и "Сделать
резервную копию" (реальное копирование файла); (2) кнопку "Open
PromptVault settings...", которая открывает/поднимает окно PromptVault
этого же процесса и сразу вызывает его настоящий `show_settings()` —
то есть Search/Performance доступны из единого дерева в один клик, без
дублирования. Кнопка дизейблена с пояснением, если лаунчер запущен не
как часть монолитной сборки (`IN_PROCESS_WINDOW_FACTORIES` пуст).

Заодно (не было отдельным пунктом плана, но напрашивалось при
переносе): окна остальных инструментов комплекта («Другие инструменты»
на домашнем экране, и то же самое окно PromptVault, которое теперь
открывает и кнопка настроек) стали закрываться по-настоящему —
`Qt.WA_DeleteOnClose` + `destroyed`-сигнал вычищают их из
`self._child_windows` и вызывают `gc.collect()`. Раньше запись в этом
кэше не удалялась никогда, и память, которую держало открытое окно (для
PromptVault — загруженные модели torch/transformers, если включён
семантический поиск), не освобождалась до закрытия всего приложения,
даже если пользователь закрыл только это окно крестиком.

### Технически

`SettingsPage` был одним плоским `QWidget`. `QTreeWidget` слева +
`QStackedWidget` справа — как и планировалось; каждая страница — свой
класс в `ui/settings/`. Легло поверх разбиения из этапа 1
(`ui/settings_page.py` уже был отдельным модулём — здесь он не "делится
дальше на подпакет", как думалось в черновике плана, а сокращается до
домашнего экрана, а подпакет `ui/settings/` появляется рядом как новая,
отдельная сущность).

### Доработки по итогам ревью (после первой версии этапа 4)

Три замечания по факту использования, все исправлены в рамках того же
этапа 4 (не откладывались на этап 5):

1. **Не хватало английского перевода.** Весь новый `ui/settings/*`
   первой версии ошибочно передавал в `_tr()` уже английский текст —
   конвенция же во всём остальном приложении (см. `TRANSLATIONS` в
   `comfyui_studio/i18n.py`) обратная: исходные строки на русском, а
   словарь `ru -> en` применяется вручную через `loc.tr()` (это не
   Qt-механизм `tr()`/`.ts`/`.qm`). При русском языке интерфейса (в
   словаре нет обратного `en -> ru`) это означало, что все новые
   страницы настроек показывались бы по-английски вне зависимости от
   выбранного языка — включая саму кнопку «Настройки...» на домашнем
   экране, у которой к тому же отсутствовала запись в словаре даже для
   направления ru -> en. Все шесть файлов `ui/settings/*.py` и
   `ui/settings_page.py` переписаны на русские исходные строки; там,
   где формулировка уже существовала в `TRANSLATIONS` (перенесённые из
   бывшего `LaunchArgsDialog` поля — путь/скрипт/порт/чекбоксы/тема
   ComfyUI), использованы те же исходные строки, чтобы не плодить два
   перевода одного смысла. Проверено программно: каждый литеральный
   вызов `_tr("...")` во всех файлах `ui/settings/` сверен с ключами
   `TRANSLATIONS["en"]` — пропущенных не осталось.

2. **Окно PromptVault открывалось "за" окном настроек лаунчера.**
   Причина — `AppSettingsDialog` был модальным (`exec()`), а модальный
   диалог в Qt блокирует остальные окна приложения; окно PromptVault,
   открываемое кнопкой "Открыть настройки PromptVault..." прямо из
   этого диалога, оказывалось заблокировано и визуально пряталось за
   модальным диалогом — довзаимодействовать с ним можно было, только
   закрыв диалог настроек лаунчера. Исправлено: `AppSettingsDialog`
   теперь открывается через `show()`/`raise_()`/`activateWindow()` (см.
   `SettingsPage._open_settings_dialog`), как и собственное окно
   настроек PromptVault (тоже немодальное) — оба могут быть открыты
   одновременно без блокировки. Дополнительно: сам диалог настроек
   лаунчера теперь закрывает себя непосредственно перед передачей
   управления окну PromptVault (см. `AppSettingsDialog.
   _wrap_open_promptvault_settings`), чтобы не оставлять два дерева
   настроек висящими одно поверх другого без необходимости.

3. **Кнопки Restart/Quit в настройках PromptVault были не просто
   лишними, а опасными в монолитной сборке.** При ревизии обнаружилось,
   что `PromptVault.MainWindow.restart_application()` перезапускает
   процесс через `os.execv(python, ["-m",
   "comfyui_studio.promptvault.main"])` — в отдельном, самостоятельном
   запуске PromptVault это корректно, но в монолитной сборке (один
   процесс/`QApplication` на лаунчер + PromptVault + Prompt Builder) это
   означало бы **заменить весь процесс** (включая лаунчер и управление
   ComfyUI) одним только PromptVault. Кнопка "Quit" была мягче (просто
   закрывала окно PromptVault), но с тем же названием вводила в
   заблуждение. Исправлено: `MainWindow`
   (`comfyui_studio/promptvault/ui/main_window.py`) и `SettingsWindow`
   (`.../ui/settings_window.py`) получили параметр `standalone: bool =
   True` — при `standalone=False` (передаётся из корневого `main.py`
   при регистрации PromptVault через `register_in_process_app`) блок
   "Application" (Restart/Quit) в его настройках вообще не строится, а
   `closeEvent` дополнительно игнорирует `_pending_restart` как защиту
   от дурака. Взамен — полноценный Studio-wide аналог: раздел Advanced
   → "Приложение" в едином дереве настроек лаунчера
   (`ui/settings/advanced_page.py`) с кнопками "🔄 Перезапустить ComfyUI
   Studio" / "⏻ Закрыть ComfyUI Studio", которые закрывают/перезапускают
   **весь** процесс целиком (лаунчер + окна остальных инструментов +
   корректная остановка ComfyUI, если запущен) — см.
   `MainWindow.quit_studio()`/`restart_studio()` в
   `launcher/ui/launcher_window.py`. При самостоятельном запуске
   PromptVault (`standalone=True`, значение по умолчанию — не сломано)
   поведение кнопок в его собственных настройках не изменилось.

### Ещё три исправления (по логу реального запуска)

4. **`TypeError: changed() only accepts 0 argument(s), 1 given!`** —
   печаталось в консоль дважды при каждом старте (без падения — Qt сам
   перехватывает и печатает исключения, всплывшие внутри слотов, не
   пробрасывая их дальше). Причина: восемь мест в `ComfyUISettingsPage`
   (`ui/settings/comfyui_page.py`) подключали сигналы с аргументом —
   `textChanged(str)`, `valueChanged(int)`, `stateChanged(int)`,
   `itemChanged(QTableWidgetItem)` — НАПРЯМУЮ к `self.changed.emit`, а
   `changed` объявлен как `Signal()` без аргументов; PySide вызывает
   `emit()` с тем же числом аргументов, что прислал источник, отсюда и
   ошибка. Два повторения при каждом старте — `script_combo.
   currentIndexChanged(int)`, срабатывающий дважды при заполнении
   комбобокса в `_refresh_scripts()` (вызывается в конце `__init__`
   этой страницы). Исправлено: все восемь подключены к новому
   промежуточному слоту `_on_field_changed(self, *_args)`, который
   отбрасывает аргумент источника и сам вызывает `self.changed.emit()`
   без аргументов.

5. **`RuntimeError: libshiboken: Internal C++ object (SettingsWindow)
   already deleted`** — вылезало на экране (диалог с необработанным
   исключением, без падения всего процесса — см. `_install_global_
   exception_hook` в `promptvault/main.py`) при смене языка ПОСЛЕ того,
   как окно PromptVault уже было закрыто. Причина —
   `comfyui_studio/promptvault/ui/settings_window.py` подписывался на
   `localization_manager.language_changed_externally` голой
   lambda-функцией (`lambda _code: self.retranslate_ui()`), а не
   bound-методом. PySide автоматически отключает соединение, когда
   C++-объект ПОЛУЧАТЕЛЯ уничтожен, но делает это, только распознав
   получателя как bound-метод QObject'а — у lambda такого
   распознаваемого получателя нет, и соединение остаётся висеть даже
   после уничтожения `self`. `localization_manager` — общий,
   более долгоживущий объект, поэтому именно эта комбинация опасна: до
   этапа 4 окна никогда не удалялись по-настоящему (см. пункт 3 выше —
   `WA_DeleteOnClose`/`gc.collect()`), поэтому баг ни разу не проявлялся
   раньше, хотя сам код с lambda существовал и до этого этапа.
   Исправлено: подписка переведена на bound-метод
   `self._on_language_changed_externally`. Заодно проверены все
   остальные `.connect(lambda ...)` по всему проекту — риск тот же
   только там, где приёмник (объект, на который замкнута lambda) может
   быть уничтожен раньше источника сигнала; во всех остальных найденных
   местах источник сигнала — дочерний виджет того же самого объекта
   (кнопка/комбобокс внутри той же страницы), они уничтожаются
   одновременно с ним, и этот сценарий им не грозит.

6. **Домашний экран лаунчера (лог, статус, кнопки запуска) не менял
   язык без перезапуска**, хотя дерево настроек и окна остальных
   инструментов обновлялись сразу же. Причина —
   `MainWindow._retranslate_secondary_ui` (`launcher/ui/
   launcher_window.py`) ретранслировал `browser_page`/`tray`, но не
   `settings_page` — до этапа 4 комбобокс языка жил прямо в
   `SettingsPage` и сам себя ретранслировал сразу при переключении,
   поэтому этот метод было незачем трогать; после переноса
   языка/темы в `AppSettingsDialog` он остался обрабатывать только
   "всё остальное", забыв про сам домашний экран — `SettingsPage.
   retranslate_ui()` был определён, но не вызывался ниоткуда.
   Исправлено: добавлен вызов `self.settings_page.retranslate_ui()`.
   Заодно закрыт смежный пробел: `SettingsPage.retranslate_ui()`
   безусловно вызывает `self.settings_dialog.retranslate_ui()`, поэтому
   при ВНЕШНЕЙ смене языка (из уже открытого окна другого инструмента)
   теперь полностью перерисовывается весь `AppSettingsDialog` (все пять
   страниц), а не только страница General, как было раньше (её
   собственная подписка на `language_changed_externally` ретранслировала
   только саму себя).

7. **Настройки PromptVault больше не требуют открытия самого
   PromptVault.** Раньше кнопка "Открыть настройки PromptVault..."
   поднимала весь `MainWindow` PromptVault (сканирование библиотеки,
   `FolderSync`, сетка миниатюр) только ради того, чтобы сразу поверх
   него показать его `SettingsWindow` — считалось, что `SettingsWindow`
   без уже открытого `MainWindow` собрать нельзя, поскольку её
   конструктор требовал `toolbar: Toolbar`, взятый из тулбара
   `MainWindow`. По прямому вопросу пользователя эта предпосылка была
   перепроверена и оказалась ложной: `self.toolbar` внутри
   `SettingsWindow` нигде не читался, кроме самого присваивания в
   `__init__` — список хоткеев берётся из отдельного
   `self.hotkey_manager = HotkeyManager()`, который сам читает/пишет
   назначения через `QSettings` и ничего не знает о `toolbar`.
   Остальные зависимости оказались дешёвыми: `GenerationRepository()`
   без аргумента открывает единую базу PromptVault напрямую (БД в
   режиме WAL, см. `core/database.py` — параллельное подключение
   безопасно, даже если "настоящий" PromptVault в этот момент тоже
   открыт), конструктор `GalleryManager` ничего не сканирует сам по
   себе (сканирование — только по явному `open_folder()`), а
   состояние семантического поиска и настройки
   производительности/хранения читаются/пишутся через `QSettings` —
   общие для процесса, а не привязанные к конкретному экземпляру
   `GalleryManager`.

   Исправлено: `toolbar` в `SettingsWindow.__init__` стал по-настоящему
   опциональным (`toolbar: Toolbar | None = None`) — раньше был
   обязательным лишь формально. Добавлена лёгкая фабрика
   `comfyui_studio.promptvault.main.create_settings_window()`, которая
   собирает `SettingsWindow` напрямую (свой `GenerationRepository`,
   `GalleryManager`, `ThemeManager`, `LocalizationManager`), не поднимая
   `MainWindow`. `PromptVaultSettingsPage`
   (`launcher/ui/settings/promptvault_page.py`) переведена на неё —
   кэширует открытое окно (тот же `WA_DeleteOnClose` + `destroyed` +
   `gc.collect()` паттерн, что и у окон "Других инструментов", см.
   пункт 3 выше) и корректно закрывает соединение с БД
   (`GenerationRepository.close()`) при закрытии окна. Заодно убрана
   вся "мостовая" логика, которая стала не нужна: параметр
   `open_promptvault_settings` в `AppSettingsDialog`/`SettingsPage` и
   метод `SettingsPage._open_promptvault_settings`. Один сознательный
   компромисс остался: смена горячих клавиш из этого лёгкого окна
   корректно сохраняется через `QSettings`, но не применяется live к
   уже открытому (если есть) полноценному окну PromptVault — тот же
   нюанс, что был бы и при двух одновременно открытых окнах PromptVault,
   просто более заметный здесь; подхватится при следующем открытии.

### Расширение этапа 4: настройки Prompt Builder + доработки PromptVault

По отдельному запросу пользователя, пока последовательность этапов ещё
не дошла до этапа 5, единое дерево настроек расширено дальше — без
этого страница "Prompt Builder" оставалась пустой заготовкой, а
несколько вещей в PromptVault показывали интерфейс, не имеющий смысла
в текущем состоянии переключателей.

**Prompt Builder — верхнее меню "Файл"/"Справка" убрано целиком**
(`comfyui_studio/prompt_builder/main.py`). Было: "Открыть папку
расширения...", "Открыть characters.json...", "Открыть
prompt_builder_config.json...", "Указать папку с файлами LoRA...",
"Сохранить текущую вкладку", "Сохранить как...", "О программе",
"Выход". Стало — тулбар из двух кнопок:

- **💾 Сохранить всё** (Ctrl+S, `save_all()` — не менялся).
- **📂 Открыть файл...** (Ctrl+O, новый `open_existing_file()`) —
  заменяет три раздельных диалога одним: по имени файла определяет,
  в какую вкладку грузить (`characters.json` /
  `prompt_builder_config.json`), для файла с другим именем — спрашивает
  явно (`QMessageBox`), не угадывает по содержимому.

Папка расширения и папка LoRA перестали быть "тем, что выбирается
изнутри редактора" — теперь это настройки, управляемые извне:

- Новый `comfyui_studio/prompt_builder/pb_settings.py` —
  `get/set_extension_folder()` (тот же QSettings-ключ `last_folder`,
  что и раньше — обратная совместимость, просто сузился до одной этой
  роли) и `get/set_backup_keep()` (новое). `lora_combo.py` не
  продублирован — его существующие `get_lora_folder`/`set_lora_folder`
  используются как есть.
- `json_store.py` — число хранимых бэкапов (`*.bak-ГГГГММДД-ЧЧММСС`)
  читается из `pb_settings.get_backup_keep()` вместо жёстко зашитой
  константы `BACKUP_KEEP = 10`; читается заново при каждом сохранении
  (изменение применяется сразу, без перезапуска).
- `launcher/ui/settings/prompt_builder_page.py` — страница перестала
  быть заготовкой: разделы "Папки" (расширение, LoRA — оба через
  `QFileDialog.getExistingDirectory` + прямое поле для ручного ввода) и
  "Резервные копии" (`QSpinBox`, 0 — не хранить вовсе). Пишет напрямую
  в собственный `QSettings` Prompt Builder (тот же паттерн, что и
  Database в `promptvault_page.py`) — не через `cfg`/`config.json`
  лаунчера, поэтому не участвует в общем debounce-автосейве
  `AppSettingsDialog`.

Смена папки расширения применяется при следующем открытии Prompt
Builder (он читает её один раз при старте, как и раньше читал
"последнюю папку"); смена папки LoRA — сразу же (список LoRA и так
пересканирует папку при каждом раскрытии выпадающего списка).

**PromptVault — Search: модель/устройство/пересчёт теперь СКРЫВАЮТСЯ,
а не только дизейблятся, пока "Enable semantic search" выключен**
(`comfyui_studio/promptvault/ui/settings_window.py`). Выбор модели
эмбеддинга и устройства обёрнут в отдельный `QWidget`
(`embedding_settings_widget`) вместо голого `QFormLayout`, добавленного
напрямую в layout — тому нельзя было вызвать `setVisible()` целиком.
Новый `_apply_semantic_search_enabled_visibility()` ортогонален уже
существовавшему `_apply_semantic_search_availability()` (тот дизейблит
то же самое, если сама библиотека физически не установлена, — теперь
виджеты видны, только если переключатель включён, и активны, только
если вдобавок доступна зависимость). При доработке чуть не сломали
существующий инвариант: `setChecked(False)` в ветке "зависимость
недоступна" теперь обёрнут в `blockSignals`, иначе новый обработчик
`_on_semantic_search_toggled` реально вызвал бы
`gallery.set_semantic_search_enabled(False)` и молча затёр сохранённый
выбор пользователя — то, что исходный (более старый) комментарий рядом
прямо запрещал.

**PromptVault — новая кнопка "Delete all vectors"** — удаляет
посчитанные векторы эмбеддингов у всех генераций (`embedding = NULL`),
без пересчёта. `GenerationRepository.clear_all_embeddings()` +
`GalleryManager.clear_all_embeddings()` — чистая операция над БД, не
требует самой библиотеки sentence-transformers/torch (можно подчистить
БД, даже уже удалив тяжёлые зависимости). Видна и активна всегда,
независимо от переключателя семантического поиска — это очистка
хранилища, а не его настройка.

**PromptVault — поле "Semantic search" в фильтрах исчезает, если
поиск выключен** (`ui/filter_popup.py`, `ui/main_window.py`).
`FilterPopup` получил параметр `semantic_search_enabled` (читается из
`gallery.semantic_search_enabled() and gallery.semantic_search_available()`
один раз при создании `MainWindow` — так же, как пользователь и просил,
"после перезапуска приложения"). Виджет `semantic_search_box`
по-прежнему создаётся всегда (нужен для остальных методов класса), но
строка в форму не добавляется; `semantic_query()` дополнительно
подстрахован — возвращает `""`, если строка не показана, даже если в
самом виджете случайно оказался непустой текст (например, из ранее
сохранённого состояния фильтров, записанного, пока поиск ещё был
включён) — иначе скрытое поле могло бы незаметно продолжать влиять на
результат фильтрации.

**PromptVault — своя кнопка "⚙ Settings" в тулбаре скрывается внутри
Studio** (`ui/toolbar.py`). Тот же параметр `standalone`, что уже был
у `MainWindow`/`SettingsWindow` (см. пункт 3 выше) — при
`standalone=False` кнопка просто не видна (единое дерево настроек
лаунчера уже даёт прямой доступ через
`create_settings_window()`/`promptvault_page.py`), при
`standalone=True` (самостоятельный запуск PromptVault) остаётся —
иначе у самостоятельного PromptVault не осталось бы вообще никакого
способа открыть свои настройки.

**Известное ограничение, требующее отдельного шага сборки:**
PromptVault переведён через настоящий Qt `QTranslator`/`.ts`/`.qm`
(`comfyui_studio/promptvault/i18n.py`), в отличие от ручного
`TRANSLATIONS`-словаря лаунчера/Prompt Builder — исходный язык
`self.tr()` здесь английский, `.qm` компилируется из `.ts`
инструментом `pyside6-lrelease` (см.
`tools/promptvault/tools/update_translations.py`). Новые строки
("Delete all vectors" и её диалоги) добавлены вручную в
`resources/translations/promptvault_ru.ts` с готовым русским переводом,
но сам `promptvault_ru.qm` **не пересобран** — ни `pyside6-lupdate`, ни
`pyside6-lrelease` недоступны в песочнице, где это делалось (PySide6
там не установлен и нет сети, чтобы это исправить). До пересборки
(`python -m tools.update_translations compile` из `tools/promptvault/`)
эти конкретные строки будут показываться по-английски даже при
русском языке интерфейса — это не падение, `QTranslator` штатно
откатывается на исходный текст, если перевода не находит, но
пересобрать `.qm` перед использованием всё равно нужно.

---

### Фикс мигания встроенного интерфейса ComfyUI после обновления фронтенда (post-build)

Обнаружено уже после того, как этап 4 в целом был закрыт, но правится
тот же слой (`browser_page.py`, точки входа `main.py`/
`launcher_window.py`, сборка) — поэтому зафиксировано здесь, а не
отдельным этапом.

**Симптом:** после обновления ComfyUI встроенный интерфейс
(`QWebEngineView` в `comfyui_studio/launcher/ui/browser_page.py`) начал
моргать целиком (сайдбар, топбар, миникарта, иногда ноды) при
панорамировании графа, перетаскивании нод, добавлении нод — и даже в
статике после сборки в exe. Из исходников (`python main.py`) не
воспроизводилось никогда. В обычном браузере интерфейс ComfyUI
стабилен всегда.

**Ход диагностики (по факту, две независимые причины):**

1. Первая гипотеза (CSS `backdrop-filter`/блюр-панели конфликтуют с
   композитором Qt WebEngine на Windows) — **не подтвердилась**.
   Пробная инъекция `QWebEngineScript`, глушащая `backdrop-filter: none
   !important` на всей странице, ничего не изменила и была убрана из
   кода (`browser_page.py`) как мёртвый код.
2. Вторая причина, реальная: **Windows Native Window Occlusion**
   Chromium ложно считает `QWebEngineView` перекрытым/неактивным
   из-за соседства с другими нативными Qt-виджетами в том же топбаре
   (`ResourceBar` с таймером, адрес, кнопки) и пересобирает
   композитинг чаще, чем нужно — это давало заметный, но не единственный
   вклад в мигание. **Фикс:** `main.py` и
   `comfyui_studio/launcher/ui/launcher_window.py` (оба места, где
   создаётся `QApplication` — монолит и автономный запуск лаунчера)
   теперь сами выставляют
   `os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-features=CalculateNativeWinOcclusion"`
   до создания `QApplication` (обязательно раньше — Chromium читает эту
   переменную только на старте процесса).
3. Третья причина, **основная** (обнаружена, когда фликер сохранился
   после фикса #2 именно в собранном exe, но не из исходников):
   **UPX ломает Qt6/Chromium-бинарники**. `build_exe.bat` собирает оба
   профиля (`ComfyUIStudio-core.spec` / `ComfyUIStudio-full.spec`) с
   `upx=True` и пустым `upx_exclude=[]` — PyInstaller с версии 4.3 сам
   исключает из UPX-сжатия только Qt-*плагины*, но НЕ сам движок
   WebEngine, ANGLE-библиотеки и хелпер-процесс. Известный баг апстрима
   UPX с Qt5/Qt6 DLL: `github.com/upx/upx/issues/107`. **Фикс:** в оба
   `.spec`-файла добавлен список `UPX_EXCLUDE` (`Qt6*.dll`,
   `libEGL.dll`, `libGLESv2.dll`, `d3dcompiler_47.dll`,
   `opengl32sw.dll`, `QtWebEngineProcess.exe`), передан в `upx_exclude`
   и `EXE(...)`, и `COLLECT(...)`. Это и оказалось решающим —
   мигание после пересборки исчезло полностью, включая случаи, где
   фикс #2 сам по себе не помогал (статика, перетаскивание нод).

**Итог:** нужны были обе правки (#2 и #3) вместе — Native Window
Occlusion давал заметный вклад при активном взаимодействии с графом,
но именно повреждение UPX Qt6-бинарников объясняло мигание в статике и
устойчивость проблемы после сборки exe при полной стабильности из
исходников. Диагностика по логу подсказки: сообщение `js: ComfyApp
graph accessed before initialization` и деприкейшн-нотисы про legacy
меню/`scripts/ui.js` в консоли — это шум самого ComfyUI, к багу
отношения не имеют.

---



Отдельный этап специально под то, чтобы после этапов 1–4 не осталось архитектурного мусора: новая структура плюс забытые compatibility-shim'ы плюс старые пути плюс старые комментарии — ровно то, что через пару месяцев снова превращается в путаницу, которую вы сейчас разгребаете.

Выполняется после слияния этапов 3 и 4, а не сразу после этапа 2 — потому что и опциональные зависимости, и дерево настроек сами по себе могут оставить переходный код (временные fallback-импорты, старые ключи конфига), который правильнее вычищать одним проходом в конце, а не дважды.

- **Удалить старые `sys.path` hacks** — вставку `PROMPTVAULT_DIR`/`TOOLS_DIR`/`ROOT_DIR` в корневом `main.py`, которая стала не нужна после этапа 2.
- **Удалить старые compatibility imports** — любые временные `from app.xxx import ...`, оставленные как переходные реэкспорты во время переименования `app` → `promptvault`.
- **Удалить дубли `shared_*`** — `prompt_builder/shared_theme.py`, `prompt_builder/shared_language.py`, `prompt_builder/pb_i18n.py`, дублирующие логику, поднятую на уровень пакета в этапе 2; заменить на импорт из `comfyui_studio.shared_theme` / `comfyui_studio.shared_language`.
- **Обновить тесты** — все 31 файл в `tools/promptvault/tests/` (и новые тесты из этапа 0 для лаунчера) переключить с `from app.` на `from comfyui_studio.promptvault.`; прогнать полный набор и убедиться, что ничего не сломано этапами 1–4.
- **Проверить PyInstaller** — оба `.spec`-профиля (`core` и `full`, из этапа 3) собираются и реально запускаются; сверить размер `.exe` core-профиля с референсом из этапа 0 — именно здесь должно стать видно, что `torch` больше не тянется в сборку без PromptVault.
- **Удалить старые entry points, если они больше не нужны** — устаревшие ссылки на `tools/prompt_builder`/`comfyui_launcher.py` в `build_exe.bat`, `ComfyUIStudio.spec`, README.

После этого этапа повторить ручной smoke-чек-лист из этапа 0 целиком — это последняя точка перед тем, как переходить к новой функциональности (этапы 6–8).

---

## 6. HTTP API abstraction

Первая половина бывшего единого пункта про ComfyUI API — намеренно отделена от WebSocket-слоя (этап 7), чтобы риски не смешивались: если WebSocket окажется проблемным (нестабильное соединение, несовместимость версий ComfyUI), у вас уже будет рабочее приложение на стабильном HTTP-слое, а не всё сразу в подвешенном состоянии.

### Точка роста

В `comfyui_launcher.py` уже есть зачаток этого слоя — `fetch_queue_status`, `fetch_history_ids`, `count_steps_in_prompt`, `is_port_open` (после этапа 1 — в `core/comfy_api.py`). Сейчас это набор функций, вызываемых напрямую из UI-кода (лог-панели, ресурс-бара). Задача этапа 6 — собрать их в класс и расширить чисто по HTTP, без изменения транспорта:

```
comfyui_studio/launcher/core/comfy_api.py
└── class ComfyAPIClient
    ├── get_queue() -> QueueState          # расширяет fetch_queue_status
    ├── get_history(limit=...) -> list      # расширяет fetch_history_ids
    ├── get_system_stats() -> SystemStats   # новое: /system_stats эндпоинт ComfyUI
    ├── get_current_workflow() -> dict      # новое: текущий running prompt (через polling)
    └── get_object_info() -> dict           # новое: доступные ноды/модели (/object_info)
```

### Критерий готовности этапа

Все текущие точки вызова в UI (`ResourceBar`, `LogPanel`, индикатор очереди) переведены с прямых вызовов `fetch_queue_status`/`fetch_history_ids` на методы `ComfyAPIClient`, и **UI полностью работает через API-класс на существующем HTTP-опросе**, без единой строчки WebSocket-кода. Это самостоятельно ценный и стабильный результат сам по себе, даже если этап 7 не начнётся сразу следом.

---

## 7. WebSocket realtime layer

Начинается только после того, как этап 6 подтверждён рабочим (UI полностью переведён на `ComfyAPIClient` и стабилен).

ComfyUI отдаёт `/ws` эндпоинт с событиями `status`, `progress`, `executing`, `executed` в реальном времени — это то, что даёт текущую выполняемую ноду, прогресс конкретного workflow и ошибки конкретного запуска без задержки polling и без лишней нагрузки на ComfyUI.

```
ComfyAPIClient (продолжение)
└── subscribe_websocket(callback)   # /ws — status, progress, executing, executed
```

Добавляется как дополнительный опциональный канал поверх интерфейса `ComfyAPIClient` из этапа 6: `ResourceMonitor`/`LogPanel` переключаются на него, когда WS доступен, с graceful fallback на HTTP-polling из этапа 6, если WS недоступен (например, при подключении к внешнему/удалённому инстансу ComfyUI). Именно этот fallback и есть главная причина не сливать этапы 6 и 7 в один — если WebSocket-часть застрянет или окажется нестабильной, откат — это просто «не переключаться на неё», а не «откатывать весь API-слой».

---

## 8. Новая функциональность

Строится поверх этапов 6 и 7 вместе — то, ради чего затевалась вся ветка с ComfyUI API:

- **очередь задач, история генераций** — прямое расширение `get_queue()`/`get_history()` из этапа 6;
- **текущий workflow, выполняемая нода, прогресс** — через WebSocket `executing`/`progress` события из этапа 7;
- **какие модели используются, загрузка/выгрузка моделей** — через `get_system_stats()` (VRAM per-model) и `get_object_info()`;
- **ошибки конкретного workflow** — через `executed`/`execution_error` WS-события, которые сейчас, видимо, просто попадают в общий лог-поток `_LogReaderThread`, а не разбираются по workflow;
- **состояние custom nodes** — через `get_object_info()`, который перечисляет зарегистрированные ноды и может выявлять несовместимости.

Этот пункт выигрывает от единого namespace (этап 2): если в будущем Prompt Builder или PromptVault тоже захотят читать состояние очереди ComfyUI (например, PromptVault — чтобы автоматически связывать сгенерированные изображения с workflow), `ComfyAPIClient` должен быть импортируемым из `comfyui_studio.launcher.core.comfy_api` без хаков с `sys.path`.

---

## Итоговая последовательность

```
0. Зафиксировать текущее поведение          ✅ Выполнено
        │
        ▼
1. Разделить comfyui_launcher.py            ✅ Выполнено
        │
        ▼
2. Единый namespace comfyui_studio          ✅ Выполнено
        │
        ├───────────────┐
        ▼               ▼
3. Dependencies     4. Settings
   ✅ Выполнено         ✅ Выполнено
        │               │
        └───────┬───────┘
                ▼
        5. Cleanup / packaging        ✅ Выполнено (см. отступление
                                          в тексте — requirements.txt/
                                          pyproject.toml не слиты,
                                          PyInstaller не пересобирался
                                          руками, standalone-сборки
                                          инструментов сломаны)
                │
                ▼
        6. HTTP API abstraction       ◻ не начат
                │
                ▼
        7. WebSocket realtime layer   ◻ не начат
                │
                ▼
        8. Новая функциональность     ◻ не начат
```

| # | Этап | Статус | Зависит от | Риск |
|---|---|---|---|---|
| 0 | Зафиксировать текущее поведение | ✅ Выполнено | — | низкий — только тесты и чек-листы, код не трогается |
| 1 | Разбиение `comfyui_launcher.py` на `core/`/`ui/`/`integration/` | ✅ Выполнено | 0 (нужен baseline для сверки) | средний — механический, но большой объём правок импортов |
| 2 | Единый namespace `comfyui_studio/*`, переименование `app` → `promptvault` | ✅ Выполнено | 1 (проще переносить уже разбитые модули) | средний — широкий find-and-replace, критично не пропустить тесты |
| 3 | `pyproject.toml` + опциональные зависимости + runtime-guard | ✅ Выполнено (см. отступление по `numpy` в тексте этапа) | 2 | низкий |
| 4 | Дерево настроек `QTreeWidget`/`QStackedWidget` | ✅ Выполнено (см. отступление по разделу PromptVault в тексте этапа) | 2 | низкий — в основном новый UI-код, не трогает существующую логику |
| 5 | Cleanup / packaging (старые `sys.path` hacks, compat-импорты, дубли `shared_*`, тесты, оба PyInstaller-профиля) | ✅ Выполнено (см. отступление в тексте) | 3 и 4 (оба должны быть завершены — иначе cleanup придётся делать дважды) | средний — легко случайно удалить что-то ещё используемое; страхуется чек-листом этапа 0 |
| 6 | `ComfyAPIClient` — HTTP-абстракция (queue/history/system stats/object info) | ◻ Не начат | 5 (нужна чистая структура `comfy_api.py`) | низкий-средний — в основном перенос существующей логики в класс |
| 7 | WebSocket realtime layer поверх `ComfyAPIClient` | ◻ Не начат | 6 (интерфейс класса должен быть стабилен) | средний — новая сетевая логика, нужно тестировать против реального ComfyUI, включая fallback на HTTP |
| 8 | Новая функциональность (per-workflow ошибки, custom nodes state, model load/unload) | ◻ Не начат | 6 и 7 вместе | средний-высокий — самая содержательная, но и самая объёмная новая логика поверх всего фундамента |

Этапы 0–5 выполнены. Следующий на очереди — этап 6 (`ComfyAPIClient` —
HTTP-абстракция). Из этапа 5 остаются три открытых пункта, намеренно не
входивших в его исходный объём (см. отступление в начале документа):
слияние `requirements.txt` с `pyproject.toml` в единый источник правды
зависимостей, ручная проверка сборки PyInstaller (`build_exe.bat
core`/`full`) на реальной Windows-машине, и починка (или удаление, если
они больше не нужны) сломанных standalone-сборок `tools/prompt_builder/
build_windows.bat`/`tools/promptvault/build.bat` — обе всё ещё
ссылаются на раскладку файлов до переноса под `comfyui_studio/` (этап
2) и в текущем виде не соберут рабочий exe.

Этапы 0–2 — фундамент, без него остальное можно делать, но с большим риском. Этапы 3 и 4 идут параллельно. Этап 5 — единая точка уборки после того, как 3 и 4 закончены, чтобы не выметать мусор дважды. Этапы 6 и 7 разделены сознательно: HTTP-слой даёт рабочий, стабильный `ComfyAPIClient` сам по себе, и только на его основе достраивается более рискованный WebSocket-канал — если он забуксует, откатывать нужно только его, а не весь API-слой.
