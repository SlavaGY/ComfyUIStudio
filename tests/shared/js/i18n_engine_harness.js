// Проверка comfyui_studio/web_common/static/js/i18n_engine.js под Node с
// подставным DOM (ни браузера, ни jsdom не нужно). Запускается из
// tests/shared/test_web_i18n_engine.py:
//   node i18n_engine_harness.js <путь к движку> [<префикс>=<путь к js/i18n.js приложения> ...]
// Любое нарушение -- исключение assert, код выхода != 0.
"use strict";
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const code = fs.readFileSync(process.argv[2], "utf8");

function makeEnv({ storage = {}, prefs = null, fetchFails = false } = {}) {
  const state = { prefs };
  const events = [];
  const fetchCalls = [];
  const timers = [];
  const store = Object.assign({}, storage);

  function el(attrs) {
    return {
      attrs: Object.assign({}, attrs),
      textContent: "",
      getAttribute(name) { return this.attrs[name]; },
      setAttribute(name, value) { this.attrs[name] = value; },
    };
  }
  const elements = {
    "[data-i18n]": [el({ "data-i18n": "Запустить" })],
    "[data-i18n-placeholder]": [el({ "data-i18n-placeholder": "Промпт" })],
    "[data-i18n-title]": [el({ "data-i18n-title": "Запустить" })],
  };
  const documentElement = {
    attrs: {},
    lang: "",
    setAttribute(name, value) { this.attrs[name] = value; },
    getAttribute(name) { return name in this.attrs ? this.attrs[name] : null; },
  };
  const document = {
    documentElement,
    querySelectorAll(selector) { return elements[selector] || []; },
    dispatchEvent(event) { events.push(event); },
  };
  class CustomEvent {
    constructor(type, init) { this.type = type; this.detail = init && init.detail; }
  }
  const localStorage = {
    getItem(key) { return key in store ? store[key] : null; },
    setItem(key, value) { store[key] = String(value); },
  };
  const fetch = (url, init) => {
    fetchCalls.push({ url, init });
    if (fetchFails) return Promise.reject(new Error("offline"));
    return Promise.resolve({ ok: state.prefs !== null, json: async () => state.prefs });
  };
  const window = {};
  const context = {
    window, document, localStorage, fetch, CustomEvent, console,
    setInterval(fn, ms) { timers.push({ fn, ms }); return timers.length; },
  };
  vm.createContext(context);
  vm.runInContext(code, context);
  return { window, context, state, document, documentElement, elements, store, events, fetchCalls, timers };
}

const EN = {
  "Запустить": "Start",
  "Промпт": "Prompt",
  "Выбрано {n} из {m}": "Selected {n} of {m}",
};

function create(env, prefix) {
  return env.window.createI18n({ storagePrefix: prefix || "imagine", EN });
}

const tests = [];
function test(name, fn) { tests.push([name, fn]); }

test("createI18n is exposed and returns the public API", () => {
  const env = makeEnv();
  assert.strictEqual(typeof env.window.createI18n, "function");
  const I18N = create(env);
  for (const name of ["t", "getLanguage", "setLanguage", "getTheme", "setTheme", "init", "THEMES"]) {
    assert.ok(name in I18N, name);
  }
  assert.deepStrictEqual(Array.from(I18N.THEMES),
    ["Dark", "Light", "Nord", "Catppuccin Mocha", "Dracula", "GitHub Dark"]);
});

test("t(): russian shows the key, english the translation, unknown keys stay as is", () => {
  const I18N = create(makeEnv());
  assert.strictEqual(I18N.t("Запустить"), "Запустить");
  I18N.setLanguage("en", { skipSync: true });
  assert.strictEqual(I18N.t("Запустить"), "Start");
  assert.strictEqual(I18N.t("Нет в словаре"), "Нет в словаре");
});

test("t(): placeholders are substituted, in every language", () => {
  const I18N = create(makeEnv());
  assert.strictEqual(I18N.t("Выбрано {n} из {m}", { n: 2, m: 5 }), "Выбрано 2 из 5");
  I18N.setLanguage("en", { skipSync: true });
  assert.strictEqual(I18N.t("Выбрано {n} из {m}", { n: 2, m: 5 }), "Selected 2 of 5");
});

test("setLanguage('en') translates the markup, remembers the choice, notifies and syncs", () => {
  const env = makeEnv();
  const I18N = create(env);
  I18N.setLanguage("en");
  assert.strictEqual(env.elements["[data-i18n]"][0].textContent, "Start");
  assert.strictEqual(env.elements["[data-i18n-placeholder]"][0].attrs.placeholder, "Prompt");
  assert.strictEqual(env.elements["[data-i18n-title]"][0].attrs.title, "Start");
  assert.strictEqual(env.documentElement.lang, "en");
  assert.strictEqual(env.store["imagine.language"], "en");
  assert.strictEqual(I18N.getLanguage(), "en");
  assert.strictEqual(env.events.length, 1);
  assert.strictEqual(env.events[0].type, "imagine:languagechange");
  assert.strictEqual(env.events[0].detail.language, "en");
  assert.strictEqual(env.fetchCalls.length, 1);
  assert.strictEqual(env.fetchCalls[0].url, "api/ui-prefs");   // ОТНОСИТЕЛЬНЫЙ путь
  assert.strictEqual(env.fetchCalls[0].init.method, "PUT");
  assert.deepStrictEqual(JSON.parse(env.fetchCalls[0].init.body), { language: "en" });
});

test("setLanguage(skipSync) does not call the server; unknown language falls back to ru", () => {
  const env = makeEnv();
  const I18N = create(env);
  I18N.setLanguage("en", { skipSync: true });
  assert.strictEqual(env.fetchCalls.length, 0);
  I18N.setLanguage("de", { skipSync: true });
  assert.strictEqual(I18N.getLanguage(), "ru");
});

test("setTheme applies a valid theme, falls back to the default for an unknown one", () => {
  const env = makeEnv();
  const I18N = create(env);
  I18N.setTheme("Nord");
  assert.strictEqual(env.documentElement.attrs["data-theme"], "Nord");
  assert.strictEqual(I18N.getTheme(), "Nord");
  assert.strictEqual(env.store["imagine.theme"], "Nord");
  assert.deepStrictEqual(JSON.parse(env.fetchCalls[0].init.body), { theme: "Nord" });
  I18N.setTheme("Нет такой", { skipSync: true });
  assert.strictEqual(env.documentElement.attrs["data-theme"], "GitHub Dark");
});

test("storage keys follow the prefix, so two apps do not overwrite each other", () => {
  const env = makeEnv();
  const pony = create(env, "imaginepony");
  pony.setLanguage("en", { skipSync: true });
  pony.setTheme("Dracula", { skipSync: true });
  assert.strictEqual(env.store["imaginepony.language"], "en");
  assert.strictEqual(env.store["imaginepony.theme"], "Dracula");
  assert.ok(!("imagine.language" in env.store) && !("imagine.theme" in env.store));
});

test("init(): restores from localStorage, then the shared server value wins", async () => {
  const env = makeEnv({
    storage: { "imagine.language": "en", "imagine.theme": "Nord" },
    prefs: { language: "ru", theme: "Dracula" },
  });
  const I18N = create(env);
  await I18N.init();
  // сеть ответила другим значением -> побеждает общее, без обратной записи (skipSync)
  assert.strictEqual(I18N.getLanguage(), "ru");
  assert.strictEqual(I18N.getTheme(), "Dracula");
  assert.deepStrictEqual(env.fetchCalls.map((c) => [c.url, c.init && c.init.method]), [["api/ui-prefs", undefined]]);
  // опрос раз в 5 секунд, тот же путь
  assert.strictEqual(env.timers.length, 1);
  assert.strictEqual(env.timers[0].ms, 5000);
});

test("init(): no event and no write when the server agrees with the local state", async () => {
  const env = makeEnv({
    storage: { "imagine.language": "en", "imagine.theme": "Nord" },
    prefs: { language: "en", theme: "Nord" },
  });
  const I18N = create(env);
  await I18N.init();
  assert.strictEqual(env.events.length, 0);
  assert.strictEqual(env.fetchCalls.length, 1);
});

test("init(): an unreachable server is not an error, local state is kept", async () => {
  const env = makeEnv({ storage: { "imagine.language": "en" }, fetchFails: true });
  const I18N = create(env);
  await I18N.init();
  assert.strictEqual(I18N.getLanguage(), "en");
  await env.timers[0].fn();   // очередной опрос тоже не бросает
});

test("polling picks up a change made in Studio while the page is open", async () => {
  const env = makeEnv({ prefs: { language: "ru", theme: "GitHub Dark" } });
  const I18N = create(env);
  await I18N.init();
  assert.strictEqual(I18N.getLanguage(), "ru");
  // в Studio сменили язык и тему: тот же эндпоинт теперь отвечает иначе
  env.state.prefs = { language: "en", theme: "Light" };
  await env.timers[0].fn();
  assert.strictEqual(I18N.getLanguage(), "en");
  assert.strictEqual(env.documentElement.attrs["data-theme"], "Light");
  assert.strictEqual(env.events.length, 1);
  assert.strictEqual(env.events[0].detail.language, "en");
});

// Реальные словари приложений поверх общего движка: файл словаря выполняется
// в том же контексте, что и страница (движок -> i18n.js).
for (const spec of process.argv.slice(3)) {
  const [prefix, file] = spec.split("=");
  test("app dictionary " + file + " wires window.I18N and window.t with prefix '" + prefix + "'", () => {
    const env = makeEnv();
    vm.runInContext(fs.readFileSync(file, "utf8"), env.context);
    assert.ok(env.window.I18N, "window.I18N не создан");
    assert.strictEqual(typeof env.window.t, "function");
    assert.strictEqual(env.window.t, env.window.I18N.t);
    assert.strictEqual(env.window.t("Загрузка…"), "Загрузка…");
    env.window.I18N.setLanguage("en", { skipSync: true });
    assert.strictEqual(env.window.t("Загрузка…"), "Loading…");
    assert.strictEqual(env.store[prefix + ".language"], "en");
    assert.deepStrictEqual(Object.keys(env.store), [prefix + ".language"]);
  });
}

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try {
      await fn();
      console.log("ok   - " + name);
    } catch (error) {
      failed += 1;
      console.log("FAIL - " + name + "\n" + (error && error.stack ? error.stack : error));
    }
  }
  console.log(failed ? failed + " FAILED" : "ALL " + tests.length + " PASSED");
  process.exit(failed ? 1 : 0);
})();
