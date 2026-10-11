/* ============================================================
   Ultimate RVC (Studio) — интерфейс
   Все URL — ОТНОСИТЕЛЬНЫЕ ("api/..." без ведущего "/"), как у Imagine:
   страница должна работать и по своему адресу, и за reverse-proxy Remote.
   Сервер — comfyui_studio/rvc/backend/main.py.
   ============================================================ */
(function () {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const t = (s) => (window.RVC_I18N ? window.RVC_I18N.t(s) : s);

  // -- сеть --------------------------------------------------------------

  async function api(path, opts) {
    const res = await fetch(path, opts);
    let data = null;
    try { data = await res.json(); } catch (e) { /* тело не JSON */ }
    if (!res.ok) throw new Error((data && data.detail) || res.status + " " + res.statusText);
    return data;
  }
  const send = (method, body) => ({
    method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });

  async function uploadFile(file) {
    return api("api/upload?name=" + encodeURIComponent(file.name), { method: "PUT", body: file });
  }

  function setMsg(el, text, kind) {
    el.textContent = text || "";
    el.className = "msg" + (kind ? " msg--" + kind : "");
  }

  async function guarded(button, msgEl, fn) {
    button.disabled = true;
    setMsg(msgEl, "");
    try { await fn(); } catch (e) { setMsg(msgEl, e.message, "error"); } finally { button.disabled = false; }
  }

  // -- хранилище настроек формы (localStorage может быть недоступен) -----------

  function store(key, value) {
    try { localStorage.setItem("rvc." + key, JSON.stringify(value)); } catch (e) { /* без сохранения */ }
  }
  function load(key, fallback) {
    try {
      const raw = localStorage.getItem("rvc." + key);
      return raw === null ? fallback : JSON.parse(raw);
    } catch (e) { return fallback; }
  }

  const state = { ready: false, options: null, models: [], voices: null, publicModels: null, coverFile: null };

  // -- описание полей ---------------------------------------------------------------
  // Значения по умолчанию совпадают с run_pipeline() ultimate_rvc.

  const rng = (key, label, min, max, step, def) => ({ key, label, kind: "range", min, max, step, def });
  const int = (key, label, min, max, def) => ({ key, label, kind: "int", min, max, def });
  const bool = (key, label) => ({ key, label, kind: "bool" });
  const sel = (key, label, source) => ({ key, label, kind: "select", source });

  function groups(kind) {
    const song = kind === "cover";
    const pitch = {
      title: "Высота тона", fields: [
        int("n_octaves", "Сдвиг голоса, октав", -3, 3, 0),
        int("n_semitones", song ? "Сдвиг тона, полутонов (голос и музыка)" : "Сдвиг тона, полутонов", -12, 12, 0),
      ],
    };
    const convert = {
      title: "Преобразование голоса", fields: [
        sel("f0_method", "Метод определения высоты", "f0"),
        sel("embedder_model", "Эмбеддер", "embedder"),
        rng("index_rate", "Влияние индекса", 0, 1, 0.05, 0.3),
        rng("rms_mix_rate", "Смешивание громкости", 0, 1, 0.05, 1),
        rng("protect_rate", "Защита согласных", 0, 0.5, 0.01, 0.33),
        int("sid", "ID говорящего", 0, 1000, 0),
      ],
    };
    const process = song ? {
      title: "Обработка вокала", advanced: true, fields: [
        bool("split_vocals", "Разбивать вокал на фрагменты"),
        bool("autotune_vocals", "Автотюн"),
        rng("autotune_strength", "Сила автотюна", 0, 1, 0.05, 1),
        bool("clean_vocals", "Очистка вокала"),
        rng("clean_strength", "Сила очистки", 0, 1, 0.05, 0.7),
        bool("proposed_pitch", "Подгонять высоту под диапазон модели"),
        int("proposed_pitch_threshold", "Порог подгонки высоты, Гц", 50, 500, 155),
      ],
    } : {
      title: "Обработка речи", advanced: true, fields: [
        bool("split_speech", "Разбивать речь на фрагменты"),
        bool("autotune_speech", "Автотюн"),
        rng("autotune_strength", "Сила автотюна", 0, 1, 0.05, 1),
        bool("clean_speech", "Очистка речи"),
        rng("clean_strength", "Сила очистки", 0, 1, 0.05, 0.7),
        bool("proposed_pitch", "Подгонять высоту под диапазон модели"),
        int("proposed_pitch_threshold", "Порог подгонки высоты, Гц", 50, 500, 155),
      ],
    };
    const out = [pitch, convert, process];
    if (song) {
      out.push({ title: "Микс, дБ", fields: [
        int("main_gain", "Вокал", -20, 20, 0),
        int("inst_gain", "Инструментал", -20, 20, 0),
        int("backup_gain", "Бэк-вокал", -20, 20, 0),
      ] });
      out.push({ title: "Реверберация вокала", advanced: true, fields: [
        rng("room_size", "Размер комнаты", 0, 1, 0.05, 0.15),
        rng("wet_level", "Эффект", 0, 1, 0.05, 0.2),
        rng("dry_level", "Исходный звук", 0, 1, 0.05, 0.8),
        rng("damping", "Затухание", 0, 1, 0.05, 0.7),
      ] });
    } else {
      out.push({ title: "Громкость", fields: [int("output_gain", "Усиление результата, дБ", -20, 20, 0)] });
    }
    out.push({ title: "Результат", fields: [
      sel("output_format", "Формат", "format"),
      sel("output_sr", "Частота дискретизации", "sr"),
      { key: "output_name", label: "Имя файла (необязательно)", kind: "text" },
    ] });
    return out;
  }

  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => {
      if (v === false || v === null || v === undefined) return;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else el.setAttribute(k, v === true ? "" : v);
    });
    children.flat().forEach((c) => c && el.append(c));
    return el;
  }

  function fieldEl(f) {
    const common = { "data-param": f.key };
    if (f.kind === "bool") {
      return h("label", { class: "field field--checkbox" },
        h("input", { ...common, type: "checkbox", "data-type": "bool" }), h("span", { text: t(f.label) }));
    }
    let control;
    if (f.kind === "int") {
      control = h("input", { ...common, type: "number", "data-type": "int", min: f.min, max: f.max, step: 1, value: f.def });
    } else if (f.kind === "range") {
      const input = h("input", { ...common, type: "range", "data-type": "float", min: f.min, max: f.max, step: f.step, value: f.def });
      const out = h("output", { text: String(f.def) });
      input.addEventListener("input", () => { out.textContent = input.value; });
      control = h("div", { class: "range" }, input, out);
    } else if (f.kind === "select") {
      control = h("select", { ...common, "data-type": "str", "data-source": f.source });
    } else {
      control = h("input", { ...common, type: "text", "data-type": "str" });
    }
    return h("label", { class: "field" }, h("span", { text: t(f.label) }), control);
  }

  function renderParams(kind) {
    const host = $(kind === "cover" ? "#coverParams" : "#speechParams");
    const saved = captureValues(host);
    host.textContent = "";
    groups(kind).forEach((g) => {
      const grid = h("div", { class: "field-grid" }, g.fields.map(fieldEl));
      const body = g.advanced
        ? h("details", { class: "advanced" }, h("summary", { text: t(g.title) }), grid)
        : h("div", {}, h("h3", { class: "card__label", text: t(g.title) }), grid);
      host.append(h("div", { class: "card" }, body));
    });
    fillSelects();
    applyValues(host, Object.keys(saved).length ? saved : load("form." + kind, {}));
    $$("input[type=range]", host).forEach((r) => { if (r.nextElementSibling) r.nextElementSibling.textContent = r.value; });
  }

  function captureValues(root) {
    const out = {};
    $$("[data-param]", root).forEach((el) => {
      out[el.dataset.param] = el.dataset.type === "bool" ? el.checked : el.value;
    });
    return out;
  }
  function applyValues(root, values) {
    $$("[data-param]", root).forEach((el) => {
      const v = values[el.dataset.param];
      if (v === undefined) return;
      if (el.dataset.type === "bool") el.checked = !!v;
      else if (el.tagName !== "SELECT" || Array.from(el.options).some((o) => o.value === String(v))) el.value = v;
    });
  }

  function collectParams(root) {
    const out = {};
    $$("[data-param]", root).forEach((el) => {
      const type = el.dataset.type;
      if (type === "bool") { out[el.dataset.param] = el.checked; return; }
      if (el.value === "") return;
      out[el.dataset.param] = type === "int" || type === "float" ? Number(el.value) : el.value;
    });
    return out;
  }

  function fillSelect(el, items, preferred) {
    const keep = el.value || preferred;
    el.textContent = "";
    items.forEach(([value, label]) => el.append(h("option", { value, text: label })));
    if (keep && items.some(([v]) => String(v) === String(keep))) el.value = keep;
  }

  function fillSelects() {
    const o = state.options;
    if (!o) return;
    $$("select[data-source]").forEach((el) => {
      const src = el.dataset.source;
      if (src === "f0") fillSelect(el, o.f0_methods.map((m) => [m.value, m.label]), o.f0_default);
      if (src === "embedder") {
        // Свой эмбеддер требует отдельной загрузки модели -- в этой версии не поддерживается.
        fillSelect(el, o.embedder_models.filter((m) => m.value !== "custom").map((m) => [m.value, m.label]), o.embedder_default);
      }
      if (src === "format") fillSelect(el, o.audio_formats.map((m) => [m.value, m.label.toLowerCase()]), o.format_default);
      if (src === "sr") fillSelect(el, o.sample_rates.map((r) => [String(r), r + " Гц"]), "44100");
    });
  }

  // -- состояние движка ------------------------------------------------------------

  function setEngine(health) {
    const dot = $("#statusDot"), text = $("#statusText"), banner = $("#engineBanner");
    dot.className = "status-dot";
    banner.hidden = true;
    if (health.state === "ready") {
      dot.classList.add("is-ok");
      text.textContent = t("движок готов");
    } else if (health.state === "error") {
      dot.classList.add("is-error");
      text.textContent = t("ошибка движка");
      banner.textContent = health.error;
      banner.hidden = false;
    } else {
      dot.classList.add("is-busy");
      text.textContent = t("загрузка движка RVC…");
    }
  }

  async function pollHealth() {
    let again = true;
    try {
      const health = await api("api/health");
      setEngine(health);
      if (health.state === "ready") {
        again = false;
        if (!state.ready) { state.ready = true; await onReady(); }
      } else if (health.state === "error") {
        again = false;
      }
    } catch (e) {
      setEngine({ state: "loading" });
      $("#statusText").textContent = t("нет связи с сервером");
    }
    if (again) setTimeout(pollHealth, 1200);
  }

  async function onReady() {
    state.options = await api("api/options");
    fillSelects();
    applyValues($("#coverParams"), load("form.cover", {}));
    applyValues($("#speechParams"), load("form.speech", {}));
    await loadModels();
    if (activeTab() === "speech") ensureVoices();
  }

  // -- вкладки ------------------------------------------------------------------------

  const activeTab = () => ($(".tab.is-active") || {}).dataset.tab;

  function showTab(name) {
    $$(".tab").forEach((b) => b.classList.toggle("is-active", b.dataset.tab === name));
    $$("[data-pane]").forEach((p) => { p.hidden = p.dataset.pane !== name; });
    store("tab", name);
    if (state.ready && name === "speech") ensureVoices();
    if (state.ready && name === "models") loadModels();
  }

  // -- модели -----------------------------------------------------------------------------

  async function loadModels() {
    try {
      state.models = (await api("api/models")).models;
    } catch (e) {
      setMsg($("#modelListMsg"), e.message, "error");
      return;
    }
    ["cover", "speech"].forEach((kind) => {
      const el = $(kind === "cover" ? "#coverModel" : "#speechModel");
      const items = state.models.map((m) => [m, m]);
      fillSelect(el, items, load("model." + kind, ""));
      if (!items.length) el.append(h("option", { value: "", text: t("— нет моделей, добавьте на вкладке «Модели» —") }));
    });
    renderModelList();
  }

  function renderModelList() {
    const host = $("#modelList");
    host.textContent = "";
    if (!state.models.length) { host.append(h("div", { class: "empty-hint", text: t("Моделей пока нет.") })); return; }
    state.models.forEach((name) => {
      const del = h("button", { class: "btn-ghost btn-danger", text: t("Удалить") });
      del.addEventListener("click", async () => {
        if (!confirm(t("Удалить модель «{}»?").replace("{}", name))) return;
        try {
          state.models = (await api("api/models/" + encodeURIComponent(name), { method: "DELETE" })).models;
          await loadModels();
        } catch (e) { setMsg($("#modelListMsg"), e.message, "error"); }
      });
      host.append(h("div", { class: "list__item" }, h("span", { class: "grow", text: name }), del));
    });
  }

  function baseName(file) { return file.replace(/\.[^.]+$/, ""); }

  async function importModel() {
    const files = Array.from($("#importFiles").files);
    if (!files.length) throw new Error(t("Выберите файлы модели"));
    const name = $("#importName").value.trim() || baseName(files[0].name);
    const ids = [];
    for (const f of files) {
      setMsg($("#importMsg"), t("Загрузка: ") + f.name);
      ids.push((await uploadFile(f)).id);
    }
    setMsg($("#importMsg"), t("Установка модели…"));
    await api("api/models/import", send("POST", { name, upload_ids: ids }));
    setMsg($("#importMsg"), t("Модель добавлена."), "ok");
    $("#importFiles").value = "";
    await loadModels();
  }

  async function downloadModel(url, name, msgEl) {
    setMsg(msgEl, t("Скачивание… это может занять несколько минут"));
    await api("api/models/download", send("POST", { url, name }));
    setMsg(msgEl, t("Модель скачана."), "ok");
    await loadModels();
  }

  async function loadPublic() {
    state.publicModels = await api("api/models/public");
    const tag = $("#pubTag");
    fillSelect(tag, [["", t("все теги")]].concat(state.publicModels.tags.map((x) => [x, x])), "");
    renderPublic();
  }

  function renderPublic() {
    const host = $("#pubList");
    host.textContent = "";
    if (!state.publicModels) return;
    const q = $("#pubSearch").value.trim().toLowerCase(), tag = $("#pubTag").value;
    const rows = state.publicModels.models.filter((m) =>
      (!tag || (m.tags || []).includes(tag)) &&
      (!q || (m.name + " " + m.description).toLowerCase().includes(q)));
    rows.slice(0, 100).forEach((m) => {
      const btn = h("button", { class: "btn-ghost", text: t("Скачать") });
      btn.addEventListener("click", () => guarded(btn, $("#pubMsg"), () => downloadModel(m.url, m.name, $("#pubMsg"))));
      host.append(h("div", { class: "list__item" },
        h("div", { class: "grow" }, h("div", { text: m.name }), h("div", { class: "list__meta", text: (m.description || "").slice(0, 140) })),
        (m.tags || []).slice(0, 2).map((x) => h("span", { class: "tag", text: x })), btn));
    });
    if (!rows.length) host.append(h("div", { class: "empty-hint", text: t("Ничего не найдено.") }));
    else if (rows.length > 100) host.append(h("div", { class: "empty-hint", text: t("Показаны первые 100 из {}. Уточните поиск.").replace("{}", rows.length) }));
  }

  // -- голоса Edge TTS ---------------------------------------------------------------------

  async function ensureVoices() {
    if (state.voices) return;
    try {
      state.voices = (await api("api/edge-voices")).voices;
    } catch (e) {
      setMsg($("#speechMsg"), t("Не удалось получить список голосов (нужен интернет): ") + e.message, "error");
      return;
    }
    const locales = Array.from(new Set(state.voices.map((v) => v.split("-").slice(0, 2).join("-")))).sort();
    const saved = load("voice.locale", locales.includes("ru-RU") ? "ru-RU" : "en-US");
    fillSelect($("#voiceLocale"), locales.map((l) => [l, l]), saved);
    fillVoices();
  }

  function fillVoices() {
    const locale = $("#voiceLocale").value;
    const voices = (state.voices || []).filter((v) => v.startsWith(locale + "-"));
    fillSelect($("#ttsVoice"), voices.map((v) => [v, v.slice(locale.length + 1)]), load("form.speech", {}).tts_voice);
  }

  // -- задания ---------------------------------------------------------------------------------

  async function submitCover() {
    const pane = $('[data-pane="cover"]');
    const body = { ...collectParams(pane), model_name: $("#coverModel").value };
    if (state.coverFile) {
      setMsg($("#coverMsg"), t("Загрузка файла…"));
      body.upload_id = (await uploadFile(state.coverFile)).id;
    } else {
      body.source = $("#coverSource").value.trim();
    }
    await api("api/jobs/song-cover", send("POST", body));
    setMsg($("#coverMsg"), t("Задание добавлено в очередь."), "ok");
    refreshJobs();
  }

  async function submitSpeech() {
    const pane = $('[data-pane="speech"]');
    const body = { ...collectParams(pane), text: $("#speechText").value, model_name: $("#speechModel").value };
    await api("api/jobs/speech", send("POST", body));
    setMsg($("#speechMsg"), t("Задание добавлено в очередь."), "ok");
    refreshJobs();
  }

  const jobNodes = new Map();
  const STATUS = { queued: "в очереди", running: "выполняется", done: "готово", error: "ошибка", cancelled: "отменено" };

  function buildJob(id) {
    const el = h("div", { class: "job" },
      h("div", { class: "job__head" }, h("span", { class: "job__title" }), h("span", { class: "job__badge" })),
      h("div", { class: "job__stage" }),
      h("div", { class: "bar" }, h("div", { class: "bar__fill" })),
      h("div", { class: "job__actions" }));
    el.dataset.jobId = id;
    return el;
  }

  function updateJob(el, j) {
    el.className = "job job--" + j.status;
    $(".job__title", el).textContent = j.title;
    $(".job__title", el).title = j.title;
    $(".job__badge", el).textContent = t(STATUS[j.status] || j.status);
    $(".job__stage", el).textContent = j.status === "running" ? j.stage : "";
    $(".bar__fill", el).style.width = Math.round((j.status === "done" ? 1 : j.progress) * 100) + "%";
    $(".bar", el).hidden = !["running", "queued"].includes(j.status);

    const actions = $(".job__actions", el);
    const active = j.status === "queued" || j.status === "running";
    let cancel = $("[data-cancel]", actions);
    if (active && !cancel) {
      cancel = h("button", { class: "btn-ghost", "data-cancel": true });
      cancel.addEventListener("click", async () => {
        try { await api("api/jobs/" + j.id + "/cancel", { method: "POST" }); refreshJobs(); } catch (e) { /* уже завершено */ }
      });
      actions.append(cancel);
    }
    if (cancel) {
      cancel.hidden = !active;
      cancel.disabled = j.cancel_requested;
      cancel.textContent = j.cancel_requested ? t("отмена после текущего этапа…") : t("Отменить");
    }

    if (j.status === "error" && !$(".job__error", el)) actions.before(h("div", { class: "job__error", text: j.error }));
    if (j.status === "done" && j.files.length && !el._audio) {
      // <audio> создаётся ОДИН раз: пересоздание при каждом опросе сбрасывало бы воспроизведение.
      el._audio = h("audio", { controls: true, preload: "none", src: "api/jobs/" + j.id + "/files/0" });
      const links = h("div", { class: "job__files" }, j.files.map((f) =>
        h("a", { href: "api/jobs/" + j.id + "/files/" + f.index + "?download=1", text: t(f.label), title: f.name })));
      actions.before(el._audio, links);
    }
  }

  function renderJobs(jobs) {
    $("#jobsEmpty").hidden = jobs.length > 0;
    const seen = new Set();
    let prev = $("#jobsEmpty");
    jobs.forEach((j) => {
      seen.add(j.id);
      let el = jobNodes.get(j.id);
      if (!el) { el = buildJob(j.id); jobNodes.set(j.id, el); }
      updateJob(el, j);
      if (prev.nextElementSibling !== el) prev.after(el);
      prev = el;
    });
    jobNodes.forEach((el, id) => { if (!seen.has(id)) { el.remove(); jobNodes.delete(id); } });
  }

  let jobsTimer = null;
  async function refreshJobs() {
    clearTimeout(jobsTimer);
    let active = false;
    try {
      const { jobs } = await api("api/jobs");
      renderJobs(jobs);
      active = jobs.some((j) => j.status === "queued" || j.status === "running");
    } catch (e) { /* сервер ещё поднимается */ }
    jobsTimer = setTimeout(refreshJobs, active ? 1000 : 4000);
  }

  // -- инициализация --------------------------------------------------------------------------------

  function wire() {
    $$(".tab").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
    $$("[data-refresh-models]").forEach((b) => b.addEventListener("click", loadModels));

    const drop = $("#coverDrop"), fileInput = $("#coverFile");
    const pick = (file) => {
      state.coverFile = file || null;
      drop.classList.toggle("has-file", !!file);
      $("#coverDropText").textContent = file ? file.name : t("Перетащите аудиофайл сюда или нажмите, чтобы выбрать");
      if (file) $("#coverSource").value = "";
    };
    fileInput.addEventListener("change", () => pick(fileInput.files[0]));
    ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("is-over"); }));
    ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("is-over"); }));
    drop.addEventListener("drop", (e) => { if (e.dataTransfer.files[0]) pick(e.dataTransfer.files[0]); });
    $("#coverSource").addEventListener("input", () => { if ($("#coverSource").value) pick(null); });

    $("#coverSubmit").addEventListener("click", () => guarded($("#coverSubmit"), $("#coverMsg"), submitCover));
    $("#speechSubmit").addEventListener("click", () => guarded($("#speechSubmit"), $("#speechMsg"), submitSpeech));
    $("#importBtn").addEventListener("click", () => guarded($("#importBtn"), $("#importMsg"), importModel));
    $("#dlBtn").addEventListener("click", () => guarded($("#dlBtn"), $("#dlMsg"), async () => {
      const name = $("#dlName").value.trim();
      if (!name) throw new Error(t("Укажите имя модели"));
      await downloadModel($("#dlUrl").value.trim(), name, $("#dlMsg"));
    }));
    $("#pubLoad").addEventListener("click", () => guarded($("#pubLoad"), $("#pubMsg"), loadPublic));
    $("#pubSearch").addEventListener("input", renderPublic);
    $("#pubTag").addEventListener("change", renderPublic);
    $("#voiceLocale").addEventListener("change", () => { store("voice.locale", $("#voiceLocale").value); fillVoices(); });

    // Сохранение значений формы при любом изменении.
    [["cover", "#coverParams"], ["speech", '[data-pane="speech"]']].forEach(([kind, sel_]) => {
      $(sel_).addEventListener("change", () => store("form." + kind, captureValues($(sel_))));
    });
    $("#coverModel").addEventListener("change", () => store("model.cover", $("#coverModel").value));
    $("#speechModel").addEventListener("change", () => store("model.speech", $("#speechModel").value));
  }

  function init() {
    renderParams("cover");
    renderParams("speech");
    wire();
    showTab(load("tab", "cover"));
    if (window.RVC_I18N) {
      window.RVC_I18N.onChange(() => {
        renderParams("cover");
        renderParams("speech");
        renderModelList();
        renderPublic();
        if (state.ready) pollHealthOnce();
      });
    }
    pollHealth();
    refreshJobs();
    setTimeout(() => $("#loadingScreen").classList.add("is-done"), 250);
  }

  async function pollHealthOnce() {
    try { setEngine(await api("api/health")); } catch (e) { /* ничего */ }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
