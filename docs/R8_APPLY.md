# R8 — темы и i18n

Архив — **наложение** на корень репозитория (после R7). Но 13 файлов
нужно **удалить вручную** (архив удалять не умеет) — команды ниже.

## Что было не так

- **Три набора по 6 `.qss`.** У Prompt Builder и PromptVault они побайтно
  равны; студийные = они же + хвост 52 строки «Дополнено для ComfyUI
  Launcher». То есть «64 строки diff» из плана — это не пропавшие правки,
  а именно этот хвост.
- **Три `ThemeManager`** с почти одинаковым кодом; три `LocalizationManager`
  (у лаунчера и Prompt Builder — копии друг друга).
- В `shared_theme.py` / `shared_language.py` — по 5 `except Exception`.
- `promptvault/themes.zip` — мёртвый старый срез тем с `.pyc`, нигде не
  используется.

## Что изменилось

**Темы**
- Один набор базовых `.qss`: `comfyui_studio/themes/*.qss` (содержимое —
  версия Prompt Builder/PromptVault).
- Хвост лаунчера вынесен в `themes/launcher/*.qss` (текст дословный).
- `themes/base.py` — `BaseThemeManager`. Приложения различаются только
  точками расширения: `settings_org/app`, `log_name`, `repolish_widgets`,
  `supplemental_qss()`.
- Лаунчер: база + `themes/launcher/*.qss`; Prompt Builder: база + довесок
  из палитры (`EXTRA_PALETTE`, как было); PromptVault: только база.
- Пути импорта прежние (`...themes.theme_manager`,
  `...prompt_builder.theme_manager`, `...promptvault.themes.theme_manager`).

**i18n**
- `comfyui_studio/i18n_base.py`: `BaseLocalizationManager` (язык, QSettings,
  общий файл, живая синхронизация) и `DictLocalizationManager` (+ `tr()`).
  Лаунчер и Prompt Builder — тонкие наследники со своими словарями.
  PromptVault переопределяет только `_install_language` (QTranslator/.qm).

**shared_theme / shared_language**
- `except Exception` → `OSError` / `ValueError` / `ImportError` /
  `RuntimeError` по месту. Повреждённый файл пишется в лог
  предупреждением; отсутствующий файл — штатно, без шума.
  `ValueError`, а не только `JSONDecodeError`: иначе не-UTF-8 файл
  упал бы. Добавлена явная проверка «корень JSON — объект» (раньше
  её заменял широкий except).

**Сборка**
- Из трёх корневых `.spec` убраны datas `prompt_builder/themes` и
  `promptvault/themes`.
- Отдельные сборки (`tools/prompt_builder/build.spec`,
  `tools/promptvault/PromptVault.spec`, `tools/promptvault/build.bat`)
  теперь кладут `comfyui_studio/themes` вместо своих папок.
  **Эти три сборки я не запускал.**

## Что НЕ сделано из плана, и почему

- **«Влить `pb_i18n.py` в общий словарь» — отклонено.** Общий ключ у
  словарей ровно один — «Обновить», и он переводится по-разному:
  лаунчер `Refresh`, Prompt Builder `Update`. Слияние молча сменило бы
  одну из кнопок, а выигрыш — одна строка. Закреплено тестом
  `test_dictionaries_are_deliberately_not_merged`.
- `shared_theme` и `shared_language` остаются двумя похожими модулями
  (дубль watcher'а) — их объединение вышло бы за рамки R8; пути к
  `%APPDATA%` по-прежнему ждут R2.

## Поведение

Вид приложений не менялся. Проверено автоматически: итоговый QSS всех
3 приложений × 6 тем (+ невалидное имя) сравнён с кодом R7:
Prompt Builder и PromptVault — **побайтно идентичны (14/14)**, лаунчер —
идентичен с точностью до пробелов (7/7), различий нет.

Небольшие осознанные отличия:
1. Невалидное имя темы теперь везде заменяется на `Dark` (раньше у
   Prompt Builder/PromptVault оно сохранялось в QSettings и общий файл).
2. PromptVault, как и остальные, при невалидном коде языка применяет
   язык по умолчанию (раньше записывал код как есть).
3. Не найден файл темы — ошибка в лог у всех трёх (раньше только у лаунчера).
4. `QSettings.sync()` после выбора темы теперь у всех (раньше — только
   у лаунчера). Безвредно.
5. Re-polish виджетов по-прежнему только у лаунчера (флаг
   `repolish_widgets`); включать его в PromptVault без визуальной
   проверки не стал.

## Тесты

- Новые: `tests/shared/` (175 тестов): общие файлы, три `ThemeManager`,
  три `LocalizationManager`.
- **Изменён один существующий тест:**
  `tools/promptvault/tests/test_frozen_resource_paths.py` — проверка
  `THEMES_DIR` внутри сборки PyInstaller перенесена с PromptVault на
  общий `themes/base.py` (своей папки тем у PromptVault больше нет).
- Новый корневой `conftest.py` изолирует общие файлы темы/языка и
  QSettings менеджеров. Раньше **каждый pytest перезаписывал
  `theme.json`/`language.json`** (на Windows — настоящие, в
  `%APPDATA%\ComfyUIStudio`) и писал в реестр: тест PromptVault в конце
  ставил язык `en`. Остальные файлы под APPDATA
  (`prompt_generator.json` и др.) пока не изолированы — это R2.
- Линт: в `pyproject.toml` добавлен строгий mypy для `themes.base` и
  `i18n_base` (чисто). Старые 12 ошибок mypy в `shared_*` и 2 `F601`
  в `i18n.py` — прежние, не тронуты.
- Ожидаемый результат: на Linux 1041 passed (было 866). На Windows 68 тестов
  `promptgen` пропускаются, поэтому ожидайте около **973 passed, 68 skipped**
  (798 + 175; оценка, не гарантия).

## Удалить вручную (13 файлов)

PowerShell из корня репозитория:

```
git rm -r comfyui_studio/prompt_builder/themes
git rm comfyui_studio/promptvault/themes.zip
git rm comfyui_studio/promptvault/themes/catppuccin.qss comfyui_studio/promptvault/themes/dark.qss comfyui_studio/promptvault/themes/dracula.qss comfyui_studio/promptvault/themes/github_dark.qss comfyui_studio/promptvault/themes/light.qss comfyui_studio/promptvault/themes/nord.qss
```

(`promptvault/themes/__init__.py` и `theme_manager.py` остаются.)

## Проверка у вас (пункт 7 чек-листа)

1. `pytest` — зелёный.
2. Лаунчер: переключить все 6 тем и язык; тема ComfyUI в окне
   браузера синхронизируется как раньше.
3. Запущенные Prompt Builder и PromptVault подхватывают смену темы и
   языка из лаунчера «на лету».
4. Сборки: `build_exe.bat core` и `full`, отдельные Prompt Builder и
   PromptVault — запускаются, темы на месте (папки
   `prompt_builder/themes` и `promptvault/themes` в сборке исчезнут,
   останется `comfyui_studio/themes` с подпапкой `launcher`).
5. После `pytest` язык и тема лаунчера больше не должны сбрасываться.
