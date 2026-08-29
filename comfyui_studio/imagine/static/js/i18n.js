/* ============================================================
   Imagine — i18n + синхронизация темы/языка с ComfyUIStudio
   ============================================================
   Устроено по образцу comfyui_studio/i18n.py (RU — исходный текст и
   он же ключ словаря, EN — перевод; язык, для которого нет записи в
   словаре, просто отображается как есть) — так те же самые строки,
   что уже написаны в разметке (index.html, data-i18n="...") и в
   app.js (t('...')), не нужно дублировать под отдельные короткие
   ключи вида "generate_btn".

   Тема и язык synced с остальным комплектом через
   GET/PUT /api/ui-prefs (см. backend/main.py) — тот же
   %APPDATA%\ComfyUIStudio\theme.json/language.json, что читают и
   пишут shared_theme.py/shared_language.py у лаунчера, Prompt Config
   Editor и PromptVault. Открытое здесь значение при следующем запуске
   применится и в них, и наоборот — если сначала сменить тему в
   любом из Qt-приложений, а потом открыть/перезагрузить Imagine.
   Постоянного файлового watcher'а (как QFileSystemWatcher у Qt-
   приложений) у веб-страницы нет — сверяемся с сервером один раз при
   загрузке, этого достаточно: Imagine — не долгоживущее окно, которое
   держат открытым часами рядом с остальными.
   ============================================================ */

(function () {
  "use strict";

  // -- Переводы -------------------------------------------------------
  // Ключ — русский исходный текст 1:1 из index.html / app.js.
  const EN = {
    "Загрузка…": "Loading…",
    "проверка ComfyUI…": "checking ComfyUI…",
    "Запустить": "Start",
    "⤓ выгрузить модели": "⤓ unload models",
    "Выгрузить модели из RAM/VRAM": "Unload models from RAM/VRAM",
    "Дев-режим": "Dev mode",
    "⚙ дев-режим": "⚙ dev mode",
    "Тема оформления": "Color theme",
    "Язык интерфейса": "Interface language",
    "Промпт": "Prompt",
    "что хотите увидеть": "what you want to see",
    "негативный промпт": "negative prompt",
    "что нужно исключить из кадра…": "what to exclude from the shot…",
    "Стили": "Styles",
    "Каталог стилей пуст — добавьте записи в дев-режиме.": "The style catalog is empty — add entries in dev mode.",
    "Категории": "Categories",
    "Категорий пока нет — добавьте их в дев-режиме.": "No categories yet — add them in dev mode.",
    "Кадр": "Frame",
    "Соотношение сторон": "Aspect ratio",
    "Мегапиксели": "Megapixels",
    "Батч": "Batch",
    "Технические параметры": "Advanced parameters",
    "Шаги": "Steps",
    "Сэмплер": "Sampler",
    "Сид": "Seed",
    "случайный": "random",
    "случайный сид каждый раз": "randomize seed every time",
    "Сгенерировать": "Generate",
    "Здесь появятся результаты.": "Results will show up here.",
    "✕ закрыть": "✕ close",
    "Всегда LoRA": "Always LoRA",
    "Подключение": "Connection",
    "Каждая плашка — картинка, текст в промпт/негатив, и опционально LoRA (сила может быть отрицательной) с триггер-словами.":
      "Each tile is an image, text added to the prompt/negative, and optionally a LoRA (strength can be negative) with trigger words.",
    "Название": "Name",
    "Картинка (необязательно)": "Image (optional)",
    "Куда добавлять текст": "Where to add the text",
    "промпт": "prompt",
    "Текст промпта (необязательно)": "Prompt text (optional)",
    "Файл LoRA (необязательно)": "LoRA file (optional)",
    "выберите или введите вручную": "pick or type manually",
    "Сила LoRA (можно отрицательную)": "LoRA strength (can be negative)",
    "Триггер-слова (необязательно, всегда в промпт)": "Trigger words (optional, always added to the prompt)",
    "Добавить стиль": "Add style",
    "Отмена": "Cancel",
    "Категория — плашка с выпадающим списком вариантов. Группа — «категория категорий»: плашка без списка, при клике разворачивается в рамку с вложенными категориями/группами внутри. Группу можно вложить в другую группу — без ограничения глубины.":
      "A category is a tile with a dropdown of options. A group is a \u201ccategory of categories\u201d: a tile with no dropdown that expands into a box with nested categories/groups inside on click. Groups can be nested inside other groups with no depth limit.",
    "Тип": "Type",
    "Категория (со списком вариантов)": "Category (with a list of options)",
    "Группа (контейнер для других категорий/групп)": "Group (a container for other categories/groups)",
    "Куда добавить": "Where to add it",
    "— верхний уровень —": "— top level —",
    "например: Освещение": "e.g.: Lighting",
    "Добавить": "Add",
    "В какую категорию": "Which category",
    "Название варианта": "Option name",
    "Текст в промпт (необязательно)": "Prompt text (optional)",
    "Текст в негатив (необязательно)": "Negative text (optional)",
    "Добавить вариант": "Add option",
    "Эти LoRA применяются к каждой генерации молча, поверх того, что выбрано в «Стилях»/«Категориях». Общий лимит одновременных LoRA (см. «Технические параметры») делится между этим списком и обычным выбором.":
      "These LoRAs are applied to every generation silently, on top of whatever is picked in \u201cStyles\u201d/\u201cCategories\u201d. The overall simultaneous-LoRA limit (see \u201cAdvanced parameters\u201d) is shared between this list and the regular picks.",
    "Название (для себя, на странице не показывается)": "Name (for your own reference, not shown on the page)",
    "Файл LoRA": "LoRA file",
    "Адрес ComfyUI Imagine получает от Studio автоматически при каждом запуске (см. «Интерфейс» в настройках ComfyUI) — здесь настраиваются только путь к .bat запуска и папка с файлами LoRA (сканируется рекурсивно).":
      "Imagine gets the ComfyUI address from Studio automatically on every launch (see \u201cInterface\u201d in ComfyUI settings) — only the launch .bat path and the LoRA folder (scanned recursively) are configured here.",
    "Путь к run_*.bat (необязательно)": "Path to run_*.bat (optional)",
    "Папка с LoRA (для сканирования)": "LoRA folder (to scan)",
    "Сохранить": "Save",
    "Закрыть (Esc)": "Close (Esc)",
    "Предыдущее (←)": "Previous (←)",
    "Следующее (→)": "Next (→)",

    // -- динамические строки из app.js --------------------------------
    "ComfyUI не запущен": "ComfyUI is not running",
    "ComfyUI запущен": "ComfyUI is running",
    "проверка…": "checking…",
    "запуск…": "starting…",
    "не удалось запустить": "failed to start",
    "модели выгружены": "models unloaded",
    "не удалось выгрузить модели": "failed to unload models",
    "Генерация…": "Generating…",
    "очередь: {n}": "queue: {n}",
    "Отменить": "Cancel",
    "генерация отменена": "generation cancelled",
    "не удалось отменить": "failed to cancel",
    "Редактировать": "Edit",
    "Удалить": "Delete",
    "Сохранить изменения": "Save changes",
    "удалить «{name}»?": "delete \u201c{name}\u201d?",
    "не больше {max} LoRA одновременно (выбрано {count})": "no more than {max} LoRAs at once (picked: {count})",
    "заполните название": "fill in the name",
    "выберите категорию": "pick a category",
    "не удалось сохранить": "failed to save",
    "не удалось удалить": "failed to delete",
    "не удалось загрузить каталог": "failed to load the catalog",
    "скачать": "download",
    "открыть": "open",
    "изображение {i} из {n}": "image {i} of {n}",

    // -- добавлены при переводе шаблонных строк app.js -----------------
    'Можно выбрать не больше {max} LoRA одновременно (часть лимита занята "всегда"-LoRA)':
      'You can pick at most {max} LoRAs at once (part of the limit is used by "always" LoRAs)',
    "— нет —": "— none —",
    "Активно:": "Active:",
    "ComfyUI на связи": "ComfyUI is connected",
    "ComfyUI недоступен": "ComfyUI is unavailable",
    "нет связи с бэкендом": "no connection to the backend",
    "Не удалось запустить ComfyUI": "Failed to start ComfyUI",
    "Не удалось выгрузить модели": "Failed to unload models",
    "Модели выгружены из памяти": "Models unloaded from memory",
    "Промпт не может быть пустым": "The prompt can't be empty",
    "Ошибка генерации": "Generation error",
    "Сгенерировать ещё (в работе: {n})": "Generate more (running: {n})",
    "генерируется…": "generating…",
    "в очереди (#{n})": "in queue (#{n})",
    "ComfyUI сообщил об ошибке выполнения": "ComfyUI reported an execution error",
    "Потеряна связь с бэкендом во время генерации": "Lost connection to the backend during generation",
    "запрашиваю список у ComfyUI…": "requesting the list from ComfyUI…",
    "от ComfyUI: {n} файлов LoRA": "from ComfyUI: {n} LoRA files",
    "ComfyUI недоступен и папка LoRA не задана — укажите её ниже, либо впишите путь к файлу вручную":
      "ComfyUI is unavailable and no LoRA folder is set — specify it below, or type the file path manually",
    "ComfyUI недоступен; в папке {folder} файлов LoRA не найдено": "ComfyUI is unavailable; no LoRA files found in {folder}",
    "ComfyUI недоступен, найдено сканированием папки: {n} ({folder})": "ComfyUI is unavailable, found by scanning the folder: {n} ({folder})",
    "не удалось получить список — проверьте бэкенд": "couldn't get the list — check the backend",
    "негатив: {text}": "negative: {text}",
    "промпт: {text}": "prompt: {text}",
    "триггер: {text}": "trigger: {text}",
    "файл: {text}": "file: {text}",
    "сила: {text}": "strength: {text}",
    "Свернуть": "Collapse",
    "Развернуть": "Expand",
    "Переименовать группу": "Rename group",
    "Переименовать категорию": "Rename category",
    "✕ группа": "✕ group",
    "✕ категория": "✕ category",
    "Не удалось загрузить картинку": "Failed to upload the image",
    "Сохранить название": "Save name",
  };

  let CURRENT_LANG = "ru";

  function t(key, params) {
    let str = (CURRENT_LANG === "en" && Object.prototype.hasOwnProperty.call(EN, key))
      ? EN[key]
      : key;
    if (params) {
      for (const k in params) {
        if (Object.prototype.hasOwnProperty.call(params, k)) {
          str = str.split("{" + k + "}").join(params[k]);
        }
      }
    }
    return str;
  }

  function applyStaticTranslations() {
    document.querySelectorAll("[data-i18n]").forEach((el) => {
      el.textContent = t(el.getAttribute("data-i18n"));
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
      el.setAttribute("placeholder", t(el.getAttribute("data-i18n-placeholder")));
    });
    document.querySelectorAll("[data-i18n-title]").forEach((el) => {
      el.setAttribute("title", t(el.getAttribute("data-i18n-title")));
    });
    document.documentElement.lang = CURRENT_LANG;
  }

  // -- Темы -------------------------------------------------------------
  // Тот же порядок и те же названия, что AVAILABLE_THEMES в
  // comfyui_studio/themes/theme_manager.py -- строки идут напрямую в
  // data-theme (см. css/themes.css) и в %APPDATA%\ComfyUIStudio\theme.json.
  const THEMES = ["Dark", "Light", "Nord", "Catppuccin Mocha", "Dracula", "GitHub Dark"];
  const DEFAULT_THEME = "GitHub Dark";

  function applyTheme(name) {
    if (!THEMES.includes(name)) name = DEFAULT_THEME;
    document.documentElement.setAttribute("data-theme", name);
  }

  // -- Публичный интерфейс ----------------------------------------------

  const I18N = {
    THEMES,
    t,

    getLanguage() {
      return CURRENT_LANG;
    },

    setLanguage(lang, opts) {
      CURRENT_LANG = lang === "en" ? "en" : "ru";
      applyStaticTranslations();
      try {
        localStorage.setItem("imagine.language", CURRENT_LANG);
      } catch (e) { /* localStorage недоступен (приватный режим) -- не критично */ }
      if (!opts || !opts.skipSync) {
        fetch("/api/ui-prefs", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ language: CURRENT_LANG }),
        }).catch(() => {});
      }
      document.dispatchEvent(new CustomEvent("imagine:languagechange", { detail: { language: CURRENT_LANG } }));
    },

    getTheme() {
      return document.documentElement.getAttribute("data-theme") || DEFAULT_THEME;
    },

    setTheme(name, opts) {
      applyTheme(name);
      try {
        localStorage.setItem("imagine.theme", name);
      } catch (e) { /* см. выше */ }
      if (!opts || !opts.skipSync) {
        fetch("/api/ui-prefs", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ theme: name }),
        }).catch(() => {});
      }
    },

    // Вызывается один раз при загрузке страницы (см. app.js, начало
    // init()). Сначала — то, что уже лежит в localStorage (мгновенно,
    // без ожидания сети, минимизирует "мигание" темой/языком по
    // умолчанию), затем сверяемся с общим для комплекта источником —
    // если там задано другое значение, оно и побеждает (тот же принцип
    // "shared-файл важнее локального", что и у Qt-приложений комплекта).
    // ИЗМЕНЕНО: убран ручной переключатель темы/языка в топбаре Imagine
    // (см. index.html/app.js) -- это дублировало то, что уже настраивается
    // централизованно в основных настройках ComfyUIStudio (см. "Общие" /
    // General page). Imagine теперь ТОЛЬКО читает то, что выбрано там
    // (через /api/ui-prefs), не предлагая собственного переключателя.
    // Не только при загрузке страницы, но и периодически, пока страница
    // открыта (см. startPolling() ниже) -- чтобы смена темы/языка в
    // Studio "живьём" подхватывалась и здесь, тем же принципом, что и
    // QFileSystemWatcher у shared_theme.py для Qt-приложений комплекта
    // (у веб-страницы такого watcher'а нет, поэтому вместо него — редкий
    // поллинг, раз в 5 секунд, той же периодичности, что и опрос статуса
    // ComfyUI в app.js).
    async init() {
      let lang = "ru";
      let theme = DEFAULT_THEME;
      try {
        lang = localStorage.getItem("imagine.language") || lang;
        theme = localStorage.getItem("imagine.theme") || theme;
      } catch (e) { /* см. выше */ }
      CURRENT_LANG = lang === "en" ? "en" : "ru";
      applyTheme(theme);
      applyStaticTranslations();

      await this._syncFromServer();
      setInterval(() => this._syncFromServer(), 5000);
    },

    async _syncFromServer() {
      try {
        const resp = await fetch("/api/ui-prefs");
        if (resp.ok) {
          const prefs = await resp.json();
          if (prefs.language && prefs.language !== CURRENT_LANG) {
            I18N.setLanguage(prefs.language, { skipSync: true });
          }
          if (prefs.theme && prefs.theme !== I18N.getTheme()) {
            I18N.setTheme(prefs.theme, { skipSync: true });
          }
        }
      } catch (e) {
        // Studio не смогла ответить (или Imagine запущен отдельно от
        // неё, без /api/ui-prefs вообще) -- остаёмся на том, что уже
        // применили из localStorage/умолчаний, ничего страшного.
      }
    },
  };

  window.I18N = I18N;
  // ВАЖНО: app.js вызывает переводы как глобальную функцию t('...'),
  // а не I18N.t('...') -- см. i18n.js.EN и все ~40 мест вызова в app.js.
  // Раньше этой строки не было, и КАЖДЫЙ вызов t() в app.js падал с
  // "ReferenceError: t is not defined": исключение обрывало ту функцию
  // на середине, а значит, например, wireStaticHandlers() (навешивает
  // обработчик клика на кнопку дев-режима) могла вообще не успеть
  // выполниться, если t() бросал исключение раньше неё в той же
  // цепочке init() -- отсюда и "кнопка дев-режима не работает", и
  // статус ComfyUI, зависший на "checking...".
  window.t = t;
})();
