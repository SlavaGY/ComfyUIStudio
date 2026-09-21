"""
i18n.py
=======
Переключение языка интерфейса лаунчера (RU/EN) — по образцу
app/i18n.py из PromptVault, но без QTranslator/.ts/.qm: исходные
строки в этом приложении и так на русском (а не на английском, как в
PromptVault), поэтому переводом здесь считается словарь ru -> en,
применяемый вручную через tr().

Как и тема (см. shared_theme.py), язык — общий на весь комплект
ComfyUI Studio: хранится в %APPDATA%\\ComfyUIStudio\\language.json
(shared_language.py) и синхронизируется живьём между уже запущенными
приложениями через QFileSystemWatcher.

Языковой переключатель есть только в лаунчере (см. README —
"общее окно настроек для всего комплекта"): PromptConfigEditor и
PromptVault сами язык не выбирают, только применяют то, что выбрано
здесь.

Область покрытия перевода (сознательно ограничена, см. README):
основной "хром" интерфейса — экран настроек, окно браузера ComfyUI,
меню трея. Живая телеметрия (CPU/RAM/GPU-чипы, подсказка трея) и
сообщения в лог-панели пока остаются на русском — их перевод стоит
меньше, чем перевод основных элементов управления, и отложен на потом.
"""
from PySide6.QtCore import QObject, QSettings, Signal

from . import shared_language

AVAILABLE_LANGUAGES = {
    "Русский": "ru",
    "English": "en",
}

DEFAULT_LANGUAGE = "ru"

# ru -> en, только для строк, реально обёрнутых в tr() ниже по коду
TRANSLATIONS = {
    "en": {
        "Лог последнего запуска ComfyUI": "Last ComfyUI launch log",
        "Тема оформления:": "Theme:",
        "Язык интерфейса:": "Language:",
        "Синхронизировать тему ComfyUI с темой приложения (ближайший встроенный вариант)": (
            "Sync ComfyUI's own theme with the app theme (closest built-in match)"
        ),
        (
            "Синхронизирует встроенную палитру ComfyUI (Comfy.ColorPalette) с "
            "темой приложения — вживую, пока ComfyUI уже открыт, и при "
            "следующем запуске. Не идентично Qt-теме — у ComfyUI своя "
            "цветовая система узлов."
        ): (
            "Syncs ComfyUI's built-in palette (Comfy.ColorPalette) with the app "
            "theme — live, while ComfyUI is already open, and on the next "
            "launch. Not identical to the Qt theme — ComfyUI has its own node "
            "color system."
        ),
        "Папка ComfyUI_windows_portable:": "ComfyUI_windows_portable folder:",
        "Аргументы запуска ComfyUI": "ComfyUI launch arguments",
        "Аргументы запуска ComfyUI...": "ComfyUI launch arguments...",
        "Дополнительные аргументы командной строки": "Additional command-line arguments",
        "Закрыть": "Close",
        (
            "Слушать на всех сетевых интерфейсах — ComfyUI станет доступен "
            "с других устройств в локальной сети. Поле необязательно: IP "
            "для прослушивания (по умолчанию — все интерфейсы)."
        ): (
            "Listen on all network interfaces — ComfyUI becomes reachable "
            "from other devices on your local network. The field is optional: "
            "the IP to listen on (defaults to all interfaces)."
        ),
        "Считать только на CPU, без GPU (медленно, но работает без видеокарты).": (
            "Compute on CPU only, no GPU (slow, but works without a graphics card)."
        ),
        "Меньше VRAM ценой части скорости — для видеокарт с небольшим объёмом VRAM.": (
            "Use less VRAM at some cost to speed — for GPUs with a small amount of VRAM."
        ),
        "Минимум VRAM — для видеокарт с очень малым объёмом VRAM (если --lowvram не помогает).": (
            "Minimal VRAM usage — for GPUs with very little VRAM (if --lowvram isn't enough)."
        ),
        "Держать модели в VRAM постоянно — для видеокарт с большим запасом VRAM.": (
            "Keep models loaded in VRAM at all times — for GPUs with plenty of VRAM."
        ),
        (
            "Держать вообще всё, включая текстовые энкодеры, в VRAM — для "
            "видеокарт с очень большим запасом VRAM."
        ): (
            "Keep absolutely everything, including text encoders, in VRAM — "
            "for GPUs with a very large amount of VRAM."
        ),
        "Зарезервировать под другие программы указанный объём VRAM, в гигабайтах.": (
            "Reserve this much VRAM (in GB) for other applications."
        ),
        "Отключить оптимизацию xFormers (если из-за неё возникают ошибки или чёрные изображения).": (
            "Disable the xFormers optimization (if it's causing errors or black images)."
        ),
        "Способ показа превью во время генерации: none, auto, latent2rgb или taesd.": (
            "Preview method used during generation: none, auto, latent2rgb, or taesd."
        ),
        (
            "Загрузить дополнительный файл extra_model_paths.yaml с путями "
            "к моделям/LoRA и т.п., лежащим вне папки ComfyUI."
        ): (
            "Load an additional extra_model_paths.yaml file with paths to "
            "models/LoRAs/etc. that live outside the ComfyUI folder."
        ),
        "Обзор...": "Browse...",
        "Скрипт запуска:": "Launch script:",
        "Порт:": "Port:",
        "Не давать ComfyUI открывать системный браузер при старте": (
            "Don't let ComfyUI open the system browser on startup"
        ),
        "Другие инструменты": "Other tools",
        "Запустить": "Launch",
        "Отмена": "Cancel",
        "Открыть папку с логами": "Open logs folder",
        "Очистить": "Clear",
        "ComfyUI уже запущен": "ComfyUI is already running",
        "Открыть ComfyUI": "Open ComfyUI",
        "Остановить": "Stop",
        "\u2190 Настройки": "\u2190 Settings",
        "Вернуться к настройкам, не останавливая ComfyUI": (
            "Back to settings without stopping ComfyUI"
        ),
        "\u23F9 Остановить": "\u23F9 Stop",
        "Остановить процесс ComfyUI": "Stop the ComfyUI process",
        "Показать окно": "Show window",
        "Остановить ComfyUI": "Stop ComfyUI",
        "Выход": "Quit",
        "Выберите папку ComfyUI_windows_portable": "Select the ComfyUI_windows_portable folder",
        "Найдено: {}": "Found: {}",
        "Готово — откроется в этом же приложении.": "Ready — opens in this same app.",
        "{} открыт.": "{} opened.",
        "{} запущен в отдельном процессе.": "{} launched in a separate process.",
        "Запуск ComfyUI, ожидание сервера...": "Launching ComfyUI, waiting for the server...",
        "Запуск ComfyUI, ожидание сервера... ({}с)": "Launching ComfyUI, waiting for the server... ({}s)",
        "Процесс ComfyUI неожиданно завершился (код выхода: {}). Подробности — в логе ниже.": (
            "The ComfyUI process exited unexpectedly (exit code: {}). See the log below for details."
        ),
        "ComfyUI не поднялся за {} секунд.": "ComfyUI didn't start within {} seconds.",
        "Запуск отменён.": "Launch cancelled.",
        "н/д": "n/a",
        "ГБ": "GB",
        "Указанная папка не существует": "The specified folder doesn't exist",
        "Не найден python_embeded\\python.exe — это не похоже на ComfyUI portable": (
            "python_embeded\\python.exe not found — this doesn't look like ComfyUI portable"
        ),
        "Не найден ComfyUI\\main.py": "ComfyUI\\main.py not found",
        "В папке не найдено ни одного run_*.bat": "No run_*.bat found in the folder",
        "Выберите скрипт запуска": "Choose a launch script",
        "Не удалось подготовить скрипт запуска: {}": "Failed to prepare the launch script: {}",
        "Очередь": "Queue",
        "Готово": "Done",
        "ComfyUI не запущен": "ComfyUI is not running",
        "оценка...": "estimating...",
        "< 1 с": "< 1 s",
        "мин": "min",
        "с": "s",

        # -- Этап 4 дорожной карты рефакторинга ("Единое дерево
        # настроек") -- comfyui_studio/launcher/ui/settings/*.py,
        # AppSettingsDialog и переработанный SettingsPage. Изначально
        # эти страницы по ошибке использовали английский текст как
        # исходный (см. историю правок) -- из-за чего при русском языке
        # интерфейса (ниже нет обратного en -> ru словаря) весь раздел
        # показывался бы по-английски вне зависимости от выбранного
        # языка. Ключ "Настройки...", в частности, был тем самым
        # отсутствующим переводом, из-за которого при переключении на
        # английский кнопка "Настройки..." оставалась на русском.
        "Настройки...": "Settings...",
        "Настройки ComfyUI Studio": "ComfyUI Studio Settings",
        "Общие": "General",
        "Дополнительно": "Advanced",

        # General -> Appearance/Startup/Updates
        "Оформление и язык": "Appearance & language",
        "Автозапуск": "Startup",
        "Запускать ComfyUI Studio при старте Windows": (
            "Launch ComfyUI Studio when Windows starts"
        ),
        (
            "Добавляет ComfyUI Studio в автозагрузку текущего пользователя "
            "Windows (права администратора не нужны). Сам ComfyUI при этом "
            "автоматически не запускается — только открывается приложение, "
            "как при обычном запуске вручную."
        ): (
            "Adds ComfyUI Studio to your Windows user startup items (no "
            "admin rights required). ComfyUI itself isn't launched "
            "automatically — only the app opens, same as starting it by "
            "hand."
        ),
        "Автозапуск доступен только в Windows.": "Autostart is only available on Windows.",
        "Обновления": "Updates",
        "Текущая версия: {version}": "Current version: {version}",
        "Открыть страницу релизов...": "Open releases page...",
        (
            "Открывает страницу релизов на GitHub в браузере — "
            "автоматической проверки обновлений пока нет, это заготовка "
            "на будущее (см. дорожную карту рефакторинга, этап 4)."
        ): (
            "Opens the GitHub releases page in your browser — there's no "
            "automatic update check yet, this is a placeholder for a "
            "future one (see the refactoring roadmap, stage 4)."
        ),

        # ComfyUI -> Installation/Environment
        "Установка": "Installation",
        "Переменные окружения": "Environment variables",
        (
            "Дополнительные переменные окружения только для процесса "
            "ComfyUI (добавляются поверх обычного окружения — например, "
            "HF_HOME, чтобы перенести кэш HuggingFace, или "
            "CUDA_VISIBLE_DEVICES, чтобы выбрать видеокарту)."
        ): (
            "Extra environment variables for the ComfyUI process only "
            "(added on top of the normal environment — e.g. HF_HOME to "
            "relocate the HuggingFace cache, or CUDA_VISIBLE_DEVICES to "
            "pick a GPU)."
        ),
        "Имя": "Name",
        "Значение": "Value",
        "Добавить переменную": "Add variable",
        "Удалить выбранные": "Remove selected",

        # Prompt Builder -> Folders / Backups (страница перестала быть
        # пустой заготовкой — редактор лишился меню "Файл", выбор папок
        # переехал сюда, см. launcher/ui/settings/prompt_builder_page.py)
        "Папки": "Folders",
        "Папка расширения:": "Extension folder:",
        (
            "Папка с characters.json и prompt_builder_config.json — "
            "Prompt Builder подхватывает файлы из неё автоматически при "
            "следующем открытии."
        ): (
            "The folder with characters.json and prompt_builder_config."
            "json — Prompt Builder picks up files from it automatically "
            "the next time it opens."
        ),
        "Папка с файлами LoRA:": "LoRA files folder:",
        (
            "Список LoRA в редакторе пересканирует эту папку заново при "
            "каждом открытии выпадающего списка — изменение здесь "
            "применяется сразу же, без перезапуска Prompt Builder."
        ): (
            "The editor's LoRA list rescans this folder every time the "
            "dropdown opens — changes here take effect immediately, no "
            "need to restart Prompt Builder."
        ),
        "Выберите папку расширения": "Select the extension folder",
        "Выберите папку с файлами LoRA": "Select the LoRA files folder",
        "Резервные копии": "Backups",
        "Хранить резервных копий (на файл):": "Backups to keep (per file):",
        (
            "Резервная копия (*.bak-ГГГГММДД-ЧЧММСС) создаётся при каждом "
            "сохранении; лишние сверх этого числа удаляются сразу же "
            "(самые старые — первыми). 0 — не хранить резервные копии "
            "вовсе."
        ): (
            "A backup (*.bak-YYYYMMDD-HHMMSS) is created on every save; "
            "extras beyond this number are deleted right away (oldest "
            "first). 0 — don't keep backups at all."
        ),

        # PromptVault -> Database / мост в его собственные настройки
        "База данных": "Database",
        "Расположение:": "Location:",
        (
            "Только для чтения — перенос базы данных пока не "
            "поддерживается (потребовался бы отдельный шаг миграции, см. "
            "дорожную карту рефакторинга). Здесь всегда лежит единая, "
            "общая база PromptVault, независимо от того, какая папка "
            "открыта в PromptVault в данный момент."
        ): (
            "Read-only — relocating the database isn't supported yet "
            "(would need a separate migration step, see the refactoring "
            "roadmap). This is where PromptVault's single, shared "
            "database always lives, regardless of which folder is "
            "currently open in PromptVault."
        ),
        "Открыть папку с файлом": "Open containing folder",
        "Сделать резервную копию": "Back up now",
        "Поиск, производительность и хранение": "Search, performance & storage",
        (
            "Открывает собственное окно настроек PromptVault "
            "(семантический поиск, размер страницы ленивой загрузки, "
            "автоочистка миниатюр/логов, горячие клавиши) — без запуска "
            "самого PromptVault целиком."
        ): (
            "Opens PromptVault's own settings window (semantic search, "
            "lazy-loading page size, thumbnail/log auto-cleanup, "
            "hotkeys) — without launching PromptVault itself."
        ),
        "Открыть настройки PromptVault...": "Open PromptVault settings...",
        "Не удалось открыть настройки PromptVault: {error}": (
            "Couldn't open PromptVault settings: {error}"
        ),
        "Файл базы данных пока не найден: {path}.": "No database file found yet at {path}.",
        "Не удалось сделать резервную копию: {error}": "Backup failed: {error}",
        "Резервная копия сохранена: {path}": "Backup saved to {path}",

        # Advanced -> Logging/Diagnostics/Reset/Application
        "Логирование": "Logging",
        "Уровень логирования консоли:": "Console log level:",
        (
            "Файл лога всегда сохраняет полную детализацию независимо от "
            "этой настройки — она влияет только на то, что выводится в "
            "консоль."
        ): (
            "The log file always keeps full detail regardless of this "
            "setting — it only affects what's printed to the console."
        ),
        "Открыть файл лога": "Open log file",
        "Диагностика": "Diagnostics",
        "Сброс": "Reset",
        (
            "Сбрасывает только настройки лаунчера ComfyUI (путь установки, "
            "порт, аргументы запуска, переменные окружения, тему, язык). "
            "Prompt Builder и PromptVault сохраняют свои настройки — это "
            "их не затрагивает."
        ): (
            "Resets ComfyUI Launcher settings only (installation path, "
            "port, launch arguments, environment variables, theme, "
            "language). Prompt Builder and PromptVault keep their own "
            "settings — this doesn't touch them."
        ),
        "Сбросить настройки лаунчера к значениям по умолчанию": (
            "Reset launcher settings to defaults"
        ),
        "Сброс настроек лаунчера": "Reset launcher settings",
        (
            "Это сбросит папку установки ComfyUI, порт, аргументы "
            "запуска, переменные окружения, тему и язык к значениям по "
            "умолчанию. Действие необратимо. Продолжить?"
        ): (
            "This resets the ComfyUI installation path, port, launch "
            "arguments, environment variables, theme and language back "
            "to defaults. This cannot be undone. Continue?"
        ),
        (
            "Настройки лаунчера сброшены. Перезапустите ComfyUI Studio, "
            "чтобы изменения вступили в силу полностью."
        ): (
            "Launcher settings have been reset. Restart ComfyUI Studio "
            "for the change to fully take effect."
        ),
        "Приложение": "Application",
        (
            "Закрывает или перезапускает весь комплект ComfyUI Studio "
            "целиком — лаунчер и открытые окна остальных инструментов. "
            "Если ComfyUI запущен, он будет корректно остановлен перед "
            "выходом/перезапуском."
        ): (
            "Closes or restarts all of ComfyUI Studio — the launcher and "
            "any open windows of the other tools. If ComfyUI is running, "
            "it will be stopped cleanly before quitting/restarting."
        ),
        "🔄 Перезапустить ComfyUI Studio": "🔄 Restart ComfyUI Studio",
        "⏻ Закрыть ComfyUI Studio": "⏻ Quit ComfyUI Studio",
        "Перезапуск ComfyUI Studio": "Restart ComfyUI Studio",
        (
            "Перезапустить ComfyUI Studio целиком сейчас? Если ComfyUI "
            "запущен, он будет остановлен."
        ): "Restart all of ComfyUI Studio now? If ComfyUI is running, it will be stopped.",
        "Закрытие ComfyUI Studio": "Quit ComfyUI Studio",
        (
            "Закрыть ComfyUI Studio целиком сейчас? Если ComfyUI запущен, "
            "он будет остановлен."
        ): "Quit all of ComfyUI Studio now? If ComfyUI is running, it will be stopped.",

        # -- Интерфейс: imagine-app как вариант запуска ComfyUI --------
        "Интерфейс": "Interface",
        (
            "Чем открыть ComfyUI после запуска: его собственным веб-"
            "интерфейсом (как раньше) или Imagine — карточным "
            "генератором поверх того же ComfyUI, запущенного этим же "
            "лаунчером."
        ): (
            "What to open ComfyUI with after launch: its own web "
            "interface (as before), or Imagine — a card-based generator "
            "on top of the same ComfyUI, started by this same launcher."
        ),
        "ComfyUI": "ComfyUI",
        "Imagine": "Imagine",
        "Порт Imagine:": "Imagine port:",
        (
            "Запускать Imagine в дев-режиме (редактирование каталога стилей)"
        ): "Launch Imagine in dev mode (edit style catalog)",
        (
            "В интерфейсе самого Imagine нет переключателя дев-режима "
            "(осознанное решение — см. его документацию): дев-режим "
            "включается только здесь, тумблером, при запуске из "
            "Studio."
        ): (
            "Imagine's own interface has no dev-mode switch (a deliberate "
            "design choice — see its documentation): dev mode is only "
            "enabled here, with this toggle, when launched from Studio."
        ),
        "Запуск ComfyUI, ожидание сервера...": "Starting ComfyUI, waiting for the server...",
        "Запуск Imagine, ожидание сервера...": "Starting Imagine, waiting for the server...",
        "Imagine не поднялся за {} секунд.": "Imagine did not come up within {} seconds.",
        (
            "Процесс Imagine неожиданно завершился (код выхода: {}). "
            "Подробности — в лог-файле: {}"
        ): "The Imagine process exited unexpectedly (exit code: {}). See the log file for details: {}",
        "Не удалось запустить Imagine: {}": "Failed to start Imagine: {}",
        # НОВОЕ (Remote, этап 1 дорожной карты
        # ComfyUIStudio_Remote_Roadmap.md) -- ui/settings/remote_page.py
        "Удалённый доступ": "Remote access",
        (
            "Доступ к ComfyUI Studio с телефона в той же локальной "
            "сети (Remote API, этапы 1–4 дорожной карты) — pairing, "
            "realtime-статус и «терминал» с Imagine внутри уже "
            "работают, Android-приложения пока нет (см. README)."
        ): (
            "Access ComfyUI Studio from your phone on the same local "
            "network (Remote API, roadmap stages 1-4) — pairing, "
            "realtime status, and a built-in Imagine \"terminal\" already "
            "work; there's no Android app yet (see README)."
        ),
        "Включить удалённый доступ": "Enable remote access",
        "Порт Remote:": "Remote port:",
        "Разрешить доступ по локальной сети (не только с этого ПК)":
            "Allow access from the local network (not just this PC)",
        (
            "Remote начнёт слушать все сетевые интерфейсы этого ПК, "
            "а не только localhost -- любое устройство в той же "
            "локальной сети сможет обращаться к нему (без токена "
            "устройства доступа к данным всё равно не получит, см. "
            "pairing выше). Также может понадобиться разрешить порт "
            "в брандмауэре Windows."
        ): (
            "Remote will start listening on all network interfaces of "
            "this PC, not just localhost — any device on the same local "
            "network will be able to reach it (it still won't get any "
            "data without a paired device token, see pairing above). You "
            "may also need to allow the port through Windows Firewall."
        ),
        "Адрес для телефона (в этой же сети): {}": "Address for your phone (same network): {}",
        "Остановлен.": "Stopped.",
        (
            "Запущен, порт {}. Изменения порта/сети применятся "
            "после перезапуска -- выключите и включите переключатель выше."
        ): (
            "Running, port {}. Port/network changes take effect after a "
            "restart — toggle the switch above off and back on."
        ),
        "Ошибка запуска: {}": "Failed to start: {}",
        "Подключить телефон": "Connect a phone",
        "Получить код для телефона": "Get a code for your phone",
        (
            "Код: {} (действителен до {}, осталось попыток: {}). "
            "Введите его в приложении на телефоне."
        ): (
            "Code: {} (valid until {}, attempts left: {}). Enter it in "
            "the app on your phone."
        ),
        "Не удалось получить код: {}": "Failed to get a code: {}",
        "Сопряжённые устройства": "Paired devices",
        "Имя": "Name",
        "Создано": "Created",
        "Последний раз в сети": "Last seen",
        "Статус": "Status",
        "Отозвано": "Revoked",
        "Активно": "Active",
        "Обновить": "Refresh",
        "Отозвать выбранные": "Revoke selected",
        "Не удалось получить список устройств: {}": "Failed to get the device list: {}",
        # -- Генератор промптов (Imagine + llama.cpp), см. ui/settings/prompt_generator_page.py --
        "Должен вмещать текст запроса, изображение и ответ (до 8192 токенов). Чем больше, тем больше видеопамяти занимает KV-кэш — а нехватка VRAM заставляет llama-server оставить часть слоёв на CPU, и генерация замедляется. 0 — взять значение из самой модели.": (
            "Must fit the request text, the image and the answer (up to 8192 tokens). The larger it is, the more VRAM the KV cache takes — and a shortage of VRAM makes llama-server keep some layers on the CPU, which slows generation down. 0 — use the value from the model itself."
        ),
        "Выгружать модели ComfyUI перед запуском:": "Unload ComfyUI models before starting:",
        "Автоматически (если не хватает VRAM)": "Automatically (if VRAM is short)",
        "Всегда": "Always",
        "Никогда": "Never",
        "Видеопамять, занятая ComfyUI, заставляет llama-server оставить часть слоёв модели на CPU — и генерация становится в разы медленнее. «Автоматически» выгружает модели ComfyUI, только если по оценке свободной видеопамяти не хватает (нужен драйвер NVIDIA); следующая генерация картинки загрузит их заново.": (
            "VRAM occupied by ComfyUI makes llama-server keep some of the model's layers on the CPU — and generation becomes several times slower. “Automatically” unloads the ComfyUI models only if, by estimate, there isn't enough free VRAM (needs the NVIDIA driver); the next image generation will load them again."
        ),
        "Макс. сторона изображения (px):": "Max image side (px):",
        "Прикреплённая картинка уменьшается до этого размера перед отправкой: чем меньше, тем меньше токенов зрения и тем быстрее обработка запроса.": (
            "An attached picture is downscaled to this size before sending: the smaller it is, the fewer vision tokens and the faster the request is processed."
        ),
        "promptgen.log — ход каждой генерации с таймингами, снимками видеопамяти и предупреждениями; в папке llama-server — полный вывод llama-server каждого запуска.": (
            "promptgen.log — the course of every generation with timings, VRAM snapshots and warnings; the llama-server folder holds the full llama-server output of every launch."
        ),
        "Кнопка генератора в Imagine запускает локальную модель GGUF через llama.cpp, отправляет ей ваш текст (и изображение, если указан mmproj), вставляет ответ в поле промпта и сразу выключает модель. Изменения применяются без перезапуска Imagine.": (
            "The generator button in Imagine runs a local GGUF model through llama.cpp, sends it your text (and an image, if mmproj is set), puts the answer into the prompt field and immediately shuts the model down. Changes apply without restarting Imagine."
        ),
        "Модель и llama.cpp": "Model and llama.cpp",
        "Папка llama.cpp:": "llama.cpp folder:",
        "Файл модели (GGUF):": "Model file (GGUF):",
        "Файл mmproj (необязательно):": "mmproj file (optional):",
        "Нужен только для работы с изображениями: в окне генератора в Imagine появится кнопка «Прикрепить изображение».": (
            "Only needed for image input: the generator window in Imagine will get an “Attach an image” button."
        ),
        "Текст перед запросом": "Text before the request",
        "Этот текст добавляется перед тем, что вы напишете в окне генератора (это не системный промпт — всё уходит одним сообщением). Метка {input} задаёт место, куда подставится ваш текст; если метки нет, текст дописывается в конец.": (
            "This text is added before whatever you type in the generator window (it is not a system prompt — everything is sent as a single message). The {input} marker sets where your text is inserted; without it, your text is appended at the end."
        ),
        "Сбросить на значение по умолчанию": "Reset to default",
        "Размер контекста (токенов):": "Context size (tokens):",
        "из модели": "from model",
        "Слои на GPU (-ngl):": "GPU layers (-ngl):",
        "авто": "auto",
        "Пусто — llama-server сам подберёт под доступную видеопамять. Можно указать число слоёв, all или auto.": (
            "Empty — llama-server picks a value to fit the available VRAM. You can enter a number of layers, all or auto."
        ),
        "Доп. папка с DLL:": "Extra DLL folder:",
        "Добавляется в PATH процесса llama-server. Для llama-server из LM Studio папка backends\\vendor\\… с CUDA-библиотеками находится автоматически; сюда нужно писать, только если автопоиск не сработал.": (
            "Added to the PATH of the llama-server process. For LM Studio's llama-server, the backends\\vendor\\… folder with the CUDA libraries is found automatically; only fill this in if auto-detection didn't work."
        ),
        "Доп. аргументы llama-server:": "Extra llama-server arguments:",
        "Добавляются в командную строку как есть — например, --reasoning off, чтобы отключить «рассуждения» у thinking-моделей.": (
            "Appended to the command line as is — for example, --reasoning off to disable “thinking” in reasoning models."
        ),
        "Укажите папку llama.cpp и файл модели — пока они не заданы, кнопка генератора в Imagine скрыта.": (
            "Set the llama.cpp folder and the model file — until they are set, the generator button in Imagine stays hidden."
        ),
        "✔ Всё найдено — генератор готов к работе.": "✔ Everything found — the generator is ready.",
        "Папки DLL, добавляемые в PATH при запуске: {}": "DLL folders added to PATH at launch: {}",
        "Выберите папку с llama-server.exe": "Select the folder with llama-server.exe",
        "Выберите файл модели GGUF": "Select the GGUF model file",
        "Выберите файл mmproj": "Select the mmproj file",
        "Выберите папку с DLL": "Select the DLL folder",
        "Модели GGUF (*.gguf);;Все файлы (*)": "GGUF models (*.gguf);;All files (*)",
        "Генератор промптов": "Prompt generator",
        "Не указана папка llama.cpp (Настройки Studio → Генератор промптов).": (
            "llama.cpp folder is not set (Studio settings → Prompt generator)."
        ),
        "Папка llama.cpp не найдена: {}": "llama.cpp folder not found: {}",
        "В папке llama.cpp нет llama-server.exe: {}": "There is no llama-server.exe in the llama.cpp folder: {}",
        "Не указан файл модели GGUF (Настройки Studio → Генератор промптов).": (
            "GGUF model file is not set (Studio settings → Prompt generator)."
        ),
        "Файл модели не найден: {}": "Model file not found: {}",
        "Файл mmproj не найден: {}": "mmproj file not found: {}",
        "Дополнительная папка с DLL не найдена: {}": "Extra DLL folder not found: {}",
    }
}


class LocalizationManager(QObject):
    """Переключает язык интерфейса лаунчера и держит его в синхроне с
    остальными приложениями комплекта (shared_language.py), аналогично
    ThemeManager для тем (themes/theme_manager.py)."""

    language_changed_externally = Signal(str)

    def __init__(self):
        super().__init__()
        self._settings = QSettings("ComfyUILauncher", "ComfyUILauncher")
        self._applied_language = None

        self._watcher = None
        if hasattr(shared_language, "SharedLanguageWatcher"):
            self._watcher = shared_language.SharedLanguageWatcher(self)
            self._watcher.language_changed.connect(self._on_shared_language_changed)

    def _on_shared_language_changed(self, language_code):
        valid_codes = set(AVAILABLE_LANGUAGES.values())
        if language_code == self._applied_language or language_code not in valid_codes:
            return
        self.apply_language(language_code)
        self.language_changed_externally.emit(language_code)

    def available_languages(self):
        return list(AVAILABLE_LANGUAGES.keys())

    def current_language(self):
        """Код языка: сначала общий язык комплекта (shared_language.py),
        иначе собственные QSettings, иначе язык по умолчанию."""
        shared = shared_language.read_shared_language()
        if shared in AVAILABLE_LANGUAGES.values():
            return shared
        saved = self._settings.value("language", DEFAULT_LANGUAGE)
        if saved not in AVAILABLE_LANGUAGES.values():
            return DEFAULT_LANGUAGE
        return saved

    def apply_language(self, language_code):
        if language_code not in AVAILABLE_LANGUAGES.values():
            language_code = DEFAULT_LANGUAGE

        self._settings.setValue("language", language_code)
        self._applied_language = language_code
        if self._watcher is not None:
            self._watcher.mark_applied(language_code)

        shared_language.write_shared_language(language_code)

    def tr(self, text):
        """Возвращает перевод text для текущего языка, либо сам text,
        если языка нет в словаре или перевода для этой строки ещё нет
        (сознательно неполный охват — см. docstring модуля)."""
        lang = self.current_language()
        return TRANSLATIONS.get(lang, {}).get(text, text)
