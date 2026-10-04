# R0 + R1 — как применить

Архив — **наложение** на корень репозитория (только новые файлы, ничего
существующего не перезаписывается). Распакуйте с сохранением путей в
корень репозитория, затем выполните команды ниже.

## Что внутри

R1 (гигиена репозитория):
- `android/.gitignore` — новый

R0 (страховка):
- `tests/prompt_builder/{__init__,test_logic,test_json_store}.py`
- `tests/launcher/test_main_window_flow.py`
- `tests/launcher/test_resource_monitor_poll.py`
- `tests/launcher/test_config_load_save.py`
- `docs/baseline_R0.md`

## Команды git (R1)

```
git rm -r --cached --ignore-unmatch android/.gradle android/.idea android/local.properties
git rm comfyui_studio/prompt_builder/theme.py comfyui_studio/prompt_builder/widgets.py
```

`--cached` убирает файлы только из индекса — на диске `.gradle`, `.idea` и
`local.properties` остаются, Android Studio продолжит работать как раньше.
`theme.py` и `widgets.py` — остатки старого tkinter-редактора: `theme.py`
импортировал только `widgets.py`, а `widgets.py` не импортирует никто
(дубли `validate_tags_text`/`parse_lora_entry`/`format_lora_entry` давно
живут в `logic.py`).

## Проверка

```
pytest tests/prompt_builder tests/launcher
```

Ожидание: все новые тесты (134) зелёные на нетронутом коде. Если какой-то
красный — это ошибка теста, а не повод менять код: пришлите вывод.

Ручная проверка R1:
1. `git status` после открытия `android/` в Android Studio — чисто.
2. Prompt Builder запускается из Studio (и сам по себе).
3. Сборки `build_exe.bat core` и `build_exe.bat full` проходят.
