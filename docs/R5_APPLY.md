# R5 — цепочка запуска ComfyUI → Imagine вынесена из `MainWindow`

Архив — **наложение** на корень репозитория (после R4/R3b). Удалять в git
ничего не нужно.

## Что изменилось

Новое: `comfyui_studio/launcher/ui/launch_controller.py` —
`LaunchController` владеет `ComfyProcess`/`ImagineProcess`, двумя
watcher'ами готовности (`LaunchWatcher`, `ImagineLaunchWatcher`),
откатом при сбоях, отменой и остановкой.

`MainWindow` создаёт контроллер и слушает два сигнала:
- `comfy_ready` → `_show_comfyui_browser()` (как раньше);
- `imagine_ready(port)` → `_show_imagine_browser(port)` (новый тонкий
  метод: загрузить браузер и переключить страницу).

Из окна удалены `_on_launch` (теперь запоминает cfg и делегирует),
`_on_server_ready`, `_start_imagine`, `_on_imagine_ready`,
`_on_imagine_failed`, `_on_server_failed`, `_on_launch_cancelled`,
атрибуты `comfy_process`, `imagine_process`, `launch_watcher`,
`imagine_launch_watcher`. `launcher_window.py`: 523 → 397 строк.
`closeEvent` / `_stop_and_show_settings` / `_show_settings_keep_running`
обращаются к `launch_controller`.

**Поведение не менялось**, включая прежние «странности» (они
зафиксированы тестами и описаны в докстринге контроллера): сбой ComfyUI
гасит только ComfyUI; сбой Imagine гасит оба процесса; отмена и stop()
обнуляют `imagine_process`, но не `comfy_process`; порядок в
`_stop_and_show_settings` — выгрузка браузера → остановка процессов →
статус → переключение страницы.

Единственное различие, видимое только в редком случае: загрузка браузера
после готовности Imagine берёт порт через `cfg.get("imagine", {})`, а не
`cfg["imagine"]` — раньше при отсутствии секции был `KeyError`.

## Тесты

- `tests/launcher/test_launch_controller.py` (26): 16 перенесены из
  `test_main_window_flow.py` с теми же ожиданиями (в них «браузер
  открыт» заменено на «подан сигнал»), остальные — проводка watcher'ов на
  настоящих сигналах Qt, порядок сигнал/скрытие прогресса, инвариант
  «`stop_processes()` не трогает вид».
- `tests/launcher/test_main_window_flow.py` сокращён до 9 тестов уровня
  окна (делегирование, порядок остановки, открытие браузера, синхронизация
  темы). Ожидания, которые остались, не менялись.

## Проверка

```
pytest
```

Ожидание: на 19 больше, чем у вас было после R4 (−16 тестов цепочки в
старом файле, +9 оконных, +26 контроллера). В песочнице (Linux):
435 passed.

Вручную:
1. Запуск ComfyUI с интерфейсом ComfyUI → после готовности открывается
   встроенный браузер.
2. Запуск с интерфейсом Imagine → прогресс «Запуск Imagine…», затем
   браузер на порту Imagine.
3. «Отмена» во время запуска (оба режима) → оба процесса остановлены,
   статус «Запуск отменён.».
4. «Стоп» из браузера и из трея → возврат на страницу настроек.
5. Выход через трей при работающем ComfyUI → вопрос, «Да» гасит ComfyUI
   (и Imagine), процессов в диспетчере задач не остаётся.
