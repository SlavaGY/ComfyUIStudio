# Imagine Pony

Второе приложение в стиле Imagine — под API-граф PonyXL (`000000.json`) с узлами
расширения `character_search_ui`. Лежит рядом с Imagine: `comfyui_studio/imagine_pony/`.

## Запуск

```
python -m comfyui_studio.imagine_pony --port 7862 --comfy-host 127.0.0.1 --comfy-port 8188
```

Без `--comfy-*` адрес берётся из `%APPDATA%\ComfyUIStudio\imagine_pony\config.json`
(создаётся при первом запуске). Зависимости те же, что у Imagine (`pip install .[imagine]`).
Тема и язык берутся из общих файлов Studio (`/api/ui-prefs`), своего переключателя нет.

## Что внутри

| Панель | Узел | Что умеет |
|---|---|---|
| Персонажи | `CharacterSearchUI` | поиск по имени / тегу, режим «Случайно» (по тегам, количество, только с LoRA; заменяет выбор), чипы выбранных, клик по тегу вкл/выкл его на выходе, LoRA персонажей |
| Билдер промпта | `PromptBuilderNode` | плашки качества/источника/категорий (в т.ч. вложенные группы), «Случайный промпт» с вкл/выкл категорий (⚙ рандом), вес сцены, пресеты негатива + доп. теги, клик по тегу негатива вкл/выкл, итоговый промпт (с тегами персонажей) |
| LoRA | `MultiLoraLoader` | 5 ручных слотов (имя из живого COMBO ComfyUI + сила, можно отрицательную) и сводка «что реально загрузится» с учётом приоритетов персонаж → билдер → вручную |

Остальное как в Imagine: кадр (соотношение сторон из `ResolutionSelector`, МП, батч), технические
параметры, очередь с плашками и «Остановить», галерея + лайтбокс, «выгрузить модели».
Добавлено: диапазон шагов «от/до» (в шаблоне шаги берутся из `Random Number` 20–40; если «от» = «до» —
шаги фиксированные) и выбор checkpoint.

Нет (как и просили): генератор промптов, дев-режим, «Стили» и «Категории» Imagine.

## Как устроено

* Данные персонажей и конфиг билдера **не копируются** — бэкенд проксирует маршруты самого расширения
  (`/character_search/*`, `/prompt_builder/*`). Логика сборки/рандома остаётся одна.
* Состояние интерфейса (выбор, билдер, слоты LoRA, параметры кадра) хранится на сервере
  (`state.json` рядом с `config.json`) — десктоп и телефон видят одно и то же.
* Граф строится из `assets/workflow_template.json` (id узлов зашиты в `backend/workflow.py`; если
  заменить шаблон — поправить константы).

## Важно: исключённые теги негатива в узле

В `prompt_builder_node.py` метод `build()` вызывает `build_negative(cfg, state, extra_negative)` **без
набора исключённых тегов**, тогда как маршрут `/prompt_builder/preview` их учитывает. То есть в самом
ComfyUI-узле клик по тегу негатива влияет только на предпросмотр, а не на генерацию.

Imagine Pony это обходит: если в негативе что-то исключено, бэкенд берёт готовый негатив у `preview` и
подставляет его в `Text Multiline` (узел 129) вместо ссылки на билдер. Если хотите починить в узле:

```python
excluded_neg = set(json.loads(state.get("__excluded_negative__", "[]")))
negative_text, _ = build_negative(cfg, state, extra_negative, excluded_neg)
```

(тогда подмену в `backend/main.py` можно убрать).

## Интеграция в Studio

* Настройки → ComfyUI → «Интерфейс»: третий вариант **Imagine Pony** + «Порт Imagine Pony»
  (`cfg["interface"] == "imagine_pony"`, `cfg["imagine_pony"] = {"port": 7862}`). Порты: Imagine 7860,
  Remote 7861, Imagine Pony 7862.
* Цепочка запуска та же: ComfyUI → Pony → встроенный браузер на Pony. Процесс лежит в том же
  `LaunchController.imagine_process`, поэтому отмена, откат и остановка работают без изменений.
  Сообщения прогресса/ошибок называют «Imagine Pony» (RU/EN в `i18n.py`).
* `launcher/core/imagine_pony_process.py` — `ImaginePonyProcess` (из исходников
  `python -m comfyui_studio.imagine_pony`, из exe — самозапуск со скрытым `IMAGINE_PONY_CLI_FLAG`;
  диспетчеризация в `main.py` до импорта Qt).
* Сборка: `datas` (static, assets) и `hiddenimports` во всех трёх `.spec`, `package-data` в
  `pyproject.toml`. Зависимости те же (`pip install .[imagine]`), `build_exe.bat` менять не нужно.

## Remote (телефон / терминал)

* Домашняя страница терминала показывает плитку **Imagine Pony** (запустить / запускается… / открыть),
  `GET /api/v1/remote/apps` отдаёт её с `id = "imagine_pony"`, путь `/apps/imagine_pony/`.
* Reverse-proxy `/apps/imagine_pony/*` → порт Pony (общий код с Imagine, токен внедряется в HTML так же).
* Запуск с телефона: поднимает ComfyUI, если он не запущен (не дублируя запуск, если он уже стартует по
  запросу Imagine), затем Pony — `remote/pony_launcher.py`. «Выключить сервер» останавливает Pony →
  Imagine → ComfyUI; Pony, запущенный из Studio, находится по порту.
* Studio передаёт порт в Remote аргументом `--imagine-pony-port` (`RemoteProcess`, `RemoteController`);
  **Remote нужно перезапустить** (выключить/включить «Удалённый доступ»), чтобы он узнал порт.
* `GenerationWatcher` определяет завершение генерации и у Pony (спрашивает Imagine, потом Pony), события
  и push получают ссылки с префиксом `/apps/imagine_pony/`.
* Android: события и push получили поле `app_path` (`/apps/imagine/` или `/apps/imagine_pony/` —
  какое приложение ответило за генерацию). `FcmService.kt` по нему ведёт тап по push сразу в нужное
  приложение (с `?prompt_id=`), `GeneratedImageSaver.kt` — качает картинки из него. Без поля (старый
  сервер, событие без приложения) работает прежний путь Imagine. Kotlin **не компилировался** —
  соберите приложение и проверьте тап и автосохранение на реальной генерации Pony.

## Не сделано

* Пошаговый прогресс в Remote (аналог `progress_forwarder.py` Imagine).

## Проверено

Мок-ComfyUI на реальной логике `char_logic`/`prompt_logic` вашего расширения + jsdom: загрузка, поиск,
выбор, исключение тегов, плашки/группы билдера, рандом (в т.ч. с отключёнными категориями), слоты LoRA
с переопределением, генерация, галерея, сохранение состояния; собранный граф сверен по узлам.
Тесты: `tests/imagine_pony/test_workflow.py` (сборка графа), `tests/launcher/test_imagine_pony_process.py`,
`tests/launcher/test_comfyui_page_interface.py`, `tests/remote/test_imagine_pony_remote.py`, плюс новые кейсы в `test_launch_controller.py`; обновлены
`test_config_load_save.py` (новый ключ конфига). Запуск через `main.py --imagine-pony-subprocess`
и связка Remote ↔ Pony (плитка, прокси, запуск, остановка) проверены вживую против мока ComfyUI. Живой ComfyUI и собранный exe (PyInstaller) не проверял.
