/* ============================================================
   Общий движок переводов и темы для Imagine и Imagine Pony
   ============================================================
   Раньше это были две копии одного файла (по ~150 строк), различавшиеся
   только префиксом ключей localStorage ("imagine.*" / "imaginepony.*").
   Теперь движок один, а каждое приложение в своём js/i18n.js держит только
   СЛОВАРЬ и создаёт экземпляр:

       const I18N = window.createI18n({ storagePrefix: "imagine", EN });
       window.I18N = I18N;
       window.t = I18N.t;

   Файл раздаётся бэкендом обоих приложений по /common (см.
   comfyui_studio/web_common/__init__.py) и подключается в index.html
   ОТНОСИТЕЛЬНЫМ путём `common/js/i18n_engine.js` ПЕРЕД js/i18n.js --
   относительным, потому что за reverse-proxy Remote страница живёт под
   префиксом /api/v1/remote/apps/<id>/, и путь с ведущим "/" ушёл бы мимо.

   Принцип словаря: русская строка -- исходный текст и он же ключ, EN --
   перевод; язык, для которого нет записи, показывается как есть. Тема и
   язык синхронизируются с остальным комплектом через GET/PUT
   api/ui-prefs (тоже относительный путь).

   Поведение проверяется tests/shared/test_web_i18n_engine.py (Node, с
   подставным DOM); словари -- tests/shared/test_js_i18n_dictionaries.py.
   ============================================================ */

(function () {
  "use strict";

  // Имя события смены языка общее у обоих приложений: app.js каждого
  // подписывается на него, и второе имя плодить незачем.
  const LANGUAGE_EVENT = "imagine:languagechange";

  window.createI18n = function createI18n(options) {
    const EN = options.EN;
    const KEY_LANGUAGE = options.storagePrefix + ".language";
    const KEY_THEME = options.storagePrefix + ".theme";

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
          localStorage.setItem(KEY_LANGUAGE, CURRENT_LANG);
        } catch (e) { /* localStorage недоступен (приватный режим) -- не критично */ }
        if (!opts || !opts.skipSync) {
          fetch("api/ui-prefs", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ language: CURRENT_LANG }),
          }).catch(() => {});
        }
        document.dispatchEvent(new CustomEvent(LANGUAGE_EVENT, { detail: { language: CURRENT_LANG } }));
      },

      getTheme() {
        return document.documentElement.getAttribute("data-theme") || DEFAULT_THEME;
      },

      setTheme(name, opts) {
        applyTheme(name);
        try {
          localStorage.setItem(KEY_THEME, name);
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
          lang = localStorage.getItem(KEY_LANGUAGE) || lang;
          theme = localStorage.getItem(KEY_THEME) || theme;
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

    return I18N;
  };
})();
