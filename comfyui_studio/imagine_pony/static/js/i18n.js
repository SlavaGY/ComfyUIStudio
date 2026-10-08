/* ============================================================
   Imagine Pony — i18n + синхронизация темы/языка с ComfyUIStudio
   ============================================================
   Тот же механизм, что в imagine/static/js/i18n.js: RU — исходный текст и
   он же ключ словаря, EN — перевод; строки без перевода показываются как
   есть. Тема и язык ТОЛЬКО читаются из общих файлов Studio через
   GET/PUT api/ui-prefs (своего переключателя у приложения нет).
   Событие смены языка называется так же, как в Imagine
   ("imagine:languagechange"), чтобы не плодить второе имя.
   ============================================================ */

(function () {
  "use strict";

  const EN = {
    "ComfyUI на связи": "ComfyUI online",
    "ComfyUI недоступен": "ComfyUI unavailable",
    "ComfyUI сообщил об ошибке выполнения": "ComfyUI reported an execution error",
    "LoRA (ручные слоты)": "LoRA (manual slots)",
    "{n} выбр.": "{n} selected",
    "{tag} — клик, чтобы убрать": "{tag} — click to remove",
    "{tag} — убран (клик, чтобы вернуть)": "{tag} — removed (click to restore)",
    "Батч": "Batch",
    "Билдер промпта": "Prompt builder",
    "Билдер промпта не загружен — проверьте ComfyUI и расширение character_search_ui": "Prompt builder isn't loaded — check ComfyUI and the character_search_ui extension",
    "Будет загружено:": "Will be loaded:",
    "В ComfyUI не найдены узлы: {list}. Проверьте установку расширения character_search_ui.": "Nodes not found in ComfyUI: {list}. Check the character_search_ui extension installation.",
    "Вес фона (сцены)": "Scene weight",
    "Выбраны:": "Selected:",
    "Выбрать": "Pick",
    "Выгрузить модели из RAM/VRAM": "Unload models from RAM/VRAM",
    "Генерация остановлена": "Generation stopped",
    "Дополнительно:": "Extra:",
    "Если «от» и «до» разные, число шагов выбирается случайно в этом диапазоне при каждой генерации.": "If \"from\" and \"to\" differ, the step count is picked randomly in that range on every generation.",
    "Загрузка…": "Loading…",
    "Закрыть (Esc)": "Close (Esc)",
    "Здесь появятся результаты.": "Results will show up here.",
    "Имя": "Name",
    "Источник": "Source",
    "Итоговый промпт:": "Final prompt:",
    "Кадр": "Frame",
    "Категории для рандома:": "Categories for random:",
    "Категории для случайного выбора": "Categories for random pick",
    "Качество": "Quality",
    "Кол-во:": "Count:",
    "Мегапиксели": "Megapixels",
    "Модели выгружены из памяти": "Models unloaded from memory",
    "Модель (checkpoint)": "Model (checkpoint)",
    "Не удалось выгрузить модели": "Failed to unload models",
    "Не удалось загрузить базу персонажей:": "Failed to load the character database:",
    "Не удалось загрузить конфиг билдера:": "Failed to load the builder config:",
    "Негативный промпт": "Negative prompt",
    "Негативный промпт:": "Negative prompt:",
    "Ничего не найдено": "Nothing found",
    "Останавливает то, что ComfyUI выполняет прямо сейчас (не только эту генерацию, если их несколько в очереди)": "Stops whatever ComfyUI is running right now (not only this generation if several are queued)",
    "Остановить": "Stop",
    "Очистить слот": "Clear slot",
    "Ошибка генерации": "Generation error",
    "Ошибка предпросмотра:": "Preview error:",
    "Персонажи": "Characters",
    "Поиск по имени…": "Search by name…",
    "Поиск по тегу…": "Search by tag…",
    "Потеряна связь с бэкендом во время генерации": "Lost connection to the backend during generation",
    "Предыдущее (←)": "Previous (←)",
    "Сгенерировать": "Generate",
    "Сгенерировать ещё (в работе: {n})": "Generate more ({n} in progress)",
    "Сид": "Seed",
    "Сила LoRA (можно отрицательную)": "LoRA strength (can be negative)",
    "Следующее (→)": "Next (→)",
    "Случайно": "Random",
    "Случайный промпт": "Random prompt",
    "Соотношение сторон": "Aspect ratio",
    "Сэмплер": "Sampler",
    "Тег": "Tag",
    "Теги на выходе:": "Output tags:",
    "Теги:": "Tags:",
    "Технические параметры": "Advanced parameters",
    "Только LoRA": "LoRA only",
    "Только персонажи с LoRA": "Only characters with a LoRA",
    "Шаги: до": "Steps: to",
    "Шаги: от": "Steps: from",
    "билдер": "builder",
    "в очереди (#{n})": "queued (#{n})",
    "вместо": "instead of",
    "восстановить": "restore",
    "вручную": "manual",
    "все": "all",
    "выбираю…": "picking…",
    "выбрано {n} из {total} подходящих": "picked {n} of {total} matching",
    "генерируется…": "generating…",
    "доп. негативные теги…": "extra negative tags…",
    "как в шаблоне": "as in template",
    "клик по тегу убирает его из негативного промпта": "click a tag to remove it from the negative prompt",
    "клик по тегу убирает его с выхода": "click a tag to remove it from the output",
    "копировать": "copy",
    "не задан": "not set",
    "не удалось": "failed",
    "нет связи с бэкендом": "no connection to backend",
    "ни одной": "none",
    "останавливается…": "stopping…",
    "ошибка запроса": "request failed",
    "персонаж": "character",
    "показано {shown} из {total} — уточните поиск": "showing {shown} of {total} — refine the search",
    "проверка ComfyUI…": "checking ComfyUI…",
    "пул пуст (0 персонажей)": "pool is empty (0 characters)",
    "случайный": "random",
    "случайный сид каждый раз": "randomize seed every time",
    "— нет — (слот {n})": "— none — (slot {n})",
    "−{n} убрано": "−{n} removed",
    "⚙ рандом": "⚙ random",
    "✓ скопировано": "✓ copied",
    "✕ сбросить": "✕ reset",
    "✕ сбросить выбор": "✕ clear selection",
    "⤓ выгрузить модели": "⤓ unload models"
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
        localStorage.setItem("imaginepony.language", CURRENT_LANG);
      } catch (e) { /* localStorage недоступен (приватный режим) -- не критично */ }
      if (!opts || !opts.skipSync) {
        fetch("api/ui-prefs", {
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
        localStorage.setItem("imaginepony.theme", name);
      } catch (e) { /* см. выше */ }
      if (!opts || !opts.skipSync) {
        fetch("api/ui-prefs", {
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
        lang = localStorage.getItem("imaginepony.language") || lang;
        theme = localStorage.getItem("imaginepony.theme") || theme;
      } catch (e) { /* см. выше */ }
      CURRENT_LANG = lang === "en" ? "en" : "ru";
      applyTheme(theme);
      applyStaticTranslations();

      await this._syncFromServer();
      setInterval(() => this._syncFromServer(), 5000);
    },

    async _syncFromServer() {
      try {
        const resp = await fetch("api/ui-prefs");
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
