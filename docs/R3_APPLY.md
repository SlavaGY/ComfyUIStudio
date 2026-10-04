# R3 — процессный слой: как применить

Архив — **наложение** на корень репозитория. Он перезаписывает несколько
существующих файлов и добавляет новые; удалять в git ничего не нужно.

## Что изменилось

Новые:
- `comfyui_studio/launcher/core/managed_process.py` — `ManagedProcess`
  (`is_running` / `exit_code` / `stop`, запуск без консольного окна,
  захват вывода с логом хвоста при аварийном выходе),
  `terminate_process_tree()` (`taskkill /F /T` на Windows),
  `find_missing_modules()`. Без Qt.
- `comfyui_studio/launcher/core/external_apps.py` — `ExternalApp`,
  `EXTERNAL_APPS`, `resolve_external_launch`, `launch_external_app`
  (перенос из `comfy_process.py` без изменения логики).
- `comfyui_studio/launcher/core/remote_net.py` — `is_remote_available`,
  `call_local_api`, `extract_error_detail`, `get_lan_ip` (перенос из
  `remote_process.py` без изменения логики).
- `tests/launcher/test_managed_process.py` — 48 тестов.

Изменены:
- `core/comfy_process.py`, `core/imagine_process.py`,
  `core/remote_process.py` — классы наследуют `ManagedProcess`;
  публичный интерфейс и сигнатуры конструкторов прежние.
- Импорты: `ui/settings_page.py`, `ui/launcher_window.py`,
  `remote/mdns.py`; только docstring/комментарии: `ui/settings/remote_page.py`,
  `prompt_builder/__main__.py`, `promptvault/__main__.py`,
  `integration/tool_registry.py`, `main.py`, `README.md`.

## Что НЕ менялось

Поведение. Тесты R0 (`test_main_window_flow.py` и остальные) не правились.
Единственное намеренное отличие: поток, пишущий в лог код выхода
Imagine/Remote, теперь получает объект процесса явно, а не читает
`self.proc` (раньше при быстром `stop()` лог завершения мог потеряться).

## Проверка

```
pytest
```

Ожидание: на Windows прежние 655 passed / 68 skipped плюс 48 новых
(≈ 703 passed / 68 skipped). Ручная проверка: запуск ComfyUI → Imagine →
Remote из окна, «Стоп» гасит все три без «осиротевших» python.exe в
диспетчере задач.

## Осталось за рамками R3

Те же `taskkill /F /T` ещё в трёх местах Remote/Imagine:
`remote/process_by_port.py::kill_pid_tree`, `remote/comfy_launcher.py`
(свой Qt-free запуск ComfyUI), `imagine/backend/promptgen.py::_kill_tree`.
Их можно перевести на `terminate_process_tree()` отдельным мелким шагом
(R3b) — функция уже Qt-free.
