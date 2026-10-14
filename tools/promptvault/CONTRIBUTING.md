# Contributing: PromptVault

Правила работы с кодом PromptVault. Сам пакет живёт в
`comfyui_studio/promptvault/` (раньше — `app/`), тесты — в
`tests/promptvault/`, конфигурация pytest/ruff/mypy — в корневом
`pyproject.toml`. Все команды ниже запускаются **из корня репозитория**.

> Страница описывает только PromptVault. Общая картина проекта и сборка —
> в корневом `README.md`; порядок работ и принятые решения — в
> `ComfyUIStudio_Audit_Roadmap.md`.

## Структура пакета

```
comfyui_studio/promptvault/
├── config.py            # константы приложения (одно место для настройки);
│                        # APP_VERSION берётся из comfyui_studio.__version__
├── main.py              # create_window(...) — окно без своего QApplication
├── utils.py             # мелкие кроссплатформенные хелперы
├── i18n.py              # LocalizationManager — язык через QTranslator
├── resources/
│   ├── icon.png
│   └── translations/    # promptvault_ru.ts (исходник) + promptvault_ru.qm
├── core/                # бизнес-логика, про Qt-виджеты не знает
│   ├── database.py      # схема SQLite, миграции, самовосстановление
│   ├── repository.py    # GenerationRepository (публичный класс): синхронизация БД с диском;
│   │                    # собран из миксинов ниже — импортировать нужно именно его
│   ├── repository_query.py     # чтение: выборки, подсчёты, фильтры, списки значений
│   ├── repository_edit.py      # метаданные + история, избранное, рейтинг, теги, удаление
│   ├── repository_transfer.py  # экспорт в ZIP, импорт пользовательских данных
│   ├── repository_stats.py     # статистика для окна статистики
│   ├── repository_base.py      # общая основа (соединение с БД) для миксинов
│   ├── gallery_manager.py   # состояние галереи, фильтры, сортировка, массовые операции
│   ├── generation_filter.py / generation_sorter.py / sort_options.py
│   ├── generation.py    # доменные модели
│   ├── parser.py        # разбор JSON-файла генерации
│   ├── thumbnails.py    # кэш миниатюр
│   └── logger.py
└── ui/                  # виджеты PySide6: только отображение и передача действий
```

Точный состав файлов смотрите в репозитории: список выше — карта
ответственности, а не перечень.

Новый метод репозитория кладите в миксин по смыслу (запрос / редактирование /
перенос / статистика), а не в `repository.py`; публичная поверхность класса
зафиксирована в `tests/promptvault/test_repository_contract.py` — добавьте метод
и в его таблицу `SURFACE`.

## Архитектурное правило

**`MainWindow` ничего не знает о том, как генерации фильтруются,
сортируются, хранятся и редактируются.** Он создаёт виджеты, соединяет их
сигналы с методами `GalleryManager` и обратно, а сам обрабатывает только
чисто UI-события (горячие клавиши, позиционирование попапов, диалоги
подтверждения перед разрушительными действиями).

Новая бизнес-логика (фильтр, поле генерации, операция с БД) живёт в
`core/`, а не в `ui/`. UI-виджеты не обращаются к `GenerationRepository`
напрямую и не меняют состояние: они только испускают сигналы с намерением
пользователя (какие id, какое значение), а решает `GalleryManager`.

## Константы

«Магические числа» (размеры, интервалы таймеров, версия, путь к иконке)
живут в `comfyui_studio/promptvault/config.py`, а не в месте использования.
Версия приложения не хранится отдельно: `APP_VERSION` — это
`comfyui_studio.__version__` (единственный источник версии Python-части,
см. `pyproject.toml`).

## Логирование

`logging.getLogger(__name__)` в начале модуля, не `print()`. Логи пишутся в
`~/.promptvault/logs/` (см. `core/logger.py`). Логируйте ключевые действия
пользователя, итоги синхронизации БД, миграции и восстановление, а также
пойманные исключения, которые не роняют приложение.

О том, что пользователь должен узнать сам, сообщайте через
`GalleryManager.error_occurred` (человекочитаемое сообщение, `MainWindow`
показывает его в `QMessageBox.warning`), а не только через `logger.error`.

Широкие `except Exception` допустимы лишь как граница подсистемы с логом;
их число ограничено бюджетом `tests/shared/test_broad_except_budget.py`.

## Тесты

```bash
python -m pip install -e ".[dev,imagine]"
python -m pytest                                  # testpaths = tests/
python -m pytest tests/promptvault                # только PromptVault
python -m pytest --cov=comfyui_studio             # с покрытием
```

Qt без экрана: `QT_QPA_PLATFORM=offscreen` выставляет корневой
`conftest.py`, отдельно задавать не нужно.

- Тесты не должны писать в реальные данные пользователя. Корневой
  `conftest.py` подменяет `QSettings`, а `tests/promptvault/conftest.py` —
  `~/.promptvault` (БД и миниатюры). Для своих файлов берите `tmp_path`.
- Новую логику в `core/` (особенно БД, миграции, фильтрацию) сопровождайте
  тестами в `tests/promptvault/`.
- Фикстура `qapp` и `qtbot` приходят из `pytest-qt`, в проекте они не
  определяются.
- **Не вызывайте `QMenu.exec()` / `QDialog.exec()` в тестах:** это
  блокирующие вызовы, в headless они зависают, а подменить `exec()` через
  monkeypatch нельзя (это скомпилированный биндинг PySide6). Построение
  меню или диалога выносите в отдельный метод без `exec()` (пример —
  `GenerationList._build_context_menu`) и тестируйте его напрямую.

## Типы и линтинг

Публичные функции и методы аннотируйте (параметры и возвращаемое
значение). В CI проверки жёсткие; те же команды локально:

```bash
python -m ruff check .                 # E, F, I, BLE
python -m mypy comfyui_studio          # весь пакет
python -m mypy -p comfyui_studio.promptvault.core   # strict-набор флагов для core/
```

`ruff format` в проекте настроен (`[tool.ruff.format]`), но **сознательно
не применяется**: у кода устоявшийся «разреженный» стиль с осмысленными
пустыми строками внутри функций, форматтер его схлопывает. Если решите
принять его стиль, делайте это одним отдельным PR на весь проект, а не
постепенно и не через хук.

Те же проверки можно запускать автоматически перед каждым коммитом
(хуки ruff, mypy и базовая гигиена из корневого `.pre-commit-config.yaml`;
`pre-commit` входит в группу `dev`):

```bash
pre-commit install            # один раз: поставит git-хук
pre-commit run --all-files    # прогнать вручную на всём проекте
```

CI описан в `.github/workflows/ci.yml` (pytest со страж-тестами сборки,
ruff, mypy, прогон на минимальных версиях зависимостей).

## Локализация (ru/en)

Каждая видимая пользователю строка в `ui/` оборачивается в
`self.tr("...")`: так переключается язык интерфейса (`i18n.py`,
переключатель — в `SettingsWindow`). Сознательно **не** оборачиваются:

- `logger.*(...)` — это сообщения для разработчика;
- бренд и версия (`"PromptVault"`) — имя собственное;
- значения, которые одновременно показываются и служат внутренним ключом
  сравнения (например, `"Auto"/"CPU"/"GPU"`): перевод тихо сломает
  `currentText() == "Auto"` после смены языка. Сравнивайте по
  `currentIndex()` (пример — `FilterPopup.favorites_only()`).

Технические детали:

- `pyside6-lupdate` извлекает только строковые **литералы** внутри
  `self.tr(...)`. Пишите `self.tr("Model: {}").format(x)`, а не
  `self.tr(f"Model: {x}")`.
- То же для строк из словаря: `self.tr(d[key])` не находится сканированием,
  нужен явный `if/elif` с литералами (пример — `SettingsWindow._quality_label`).
- Виджеты, которые пересоздаются при каждом показе (диалоги, контекстное
  меню), подхватывают текущий язык сами. Долгоживущие (`Toolbar`,
  `SettingsWindow`, `MainWindow`) нужно переводить вручную методом
  `retranslate_ui()` после `LocalizationManager.apply_language(...)`.

Рабочий процесс после правки `self.tr(...)`. Скрипт лежит в
`tools/promptvault/tools/update_translations.py`, запускать его нужно из
`tools/promptvault/`:

```bash
cd tools/promptvault
python -m tools.update_translations update    # .py -> .ts (существующие переводы не затираются)
# вручную заполнить <translation> для новых строк в
# comfyui_studio/promptvault/resources/translations/promptvault_ru.ts
python -m tools.update_translations compile   # .ts -> .qm
python -m tools.update_translations check     # падает, если остались непереведённые строки
```

Коммитить нужно **и** `.ts`, **и** `.qm`: `.ts` — редактируемый исходник,
`.qm` — то, что реально грузит `QTranslator`. Без пересборки `.qm` правки в
`.ts` приложение не увидит. `pyside6-lupdate` и `pyside6-lrelease` ставятся
вместе с PySide6. Автоматической проверки переводов в CI сейчас нет.

## Стиль кода

- Docstring на русском, в свободной форме (не Google/NumPy-style).
- Комментарии объясняют **почему**, а не **что**.
- Один файл — одна зона ответственности.
- Не называйте переменные одиночной `l` (легко спутать с `1`; правило `E741`).
