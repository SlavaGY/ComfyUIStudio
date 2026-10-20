/* Local chat application. Model output is rendered as DOM text, never HTML. */
"use strict";
(() => {
  const $ = id => document.getElementById(id);
  let chat = null, chats = [], models = [], ready = false, busy = false, modelBusy = false, navigating = false;
  let controller = null, saveQueue = Promise.resolve(), pendingSaveError = null, stopRequested = false;

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }
  function banner(message = "") { $("banner").textContent = message; $("banner").hidden = !message; }
  async function api(path, method = "GET", body) {
    const res = await fetch("api/" + path, {
      method, headers: {"Content-Type": "application/json"},
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
    return data;
  }
  function settings() {
    return {title: $("chatTitle").value, model: $("modelSelect").value, system: $("system").value,
      temperature: Number($("temperature").value), top_p: Number($("topP").value), max_tokens: Number($("maxTokens").value)};
  }
  function controls() {
    $("send").disabled = !ready || busy || modelBusy || navigating || !$("modelSelect").value;
    $("stop").hidden = !busy;
    $("send").hidden = busy;
    $("retry").disabled = !ready || busy || modelBusy || navigating || !chat || !chat.messages.length;
    for (const id of ["newChat", "deleteChat", "chatTitle", "modelSelect", "system", "temperature", "topP", "maxTokens", "contextLength", "refreshModels", "reloadModels"])
      $(id).disabled = busy || modelBusy || navigating;
    document.querySelectorAll(".chat-item").forEach(button => { button.disabled = busy || modelBusy || navigating; });
    document.querySelectorAll(".model-card button").forEach(button => { button.disabled = busy || modelBusy || !ready; });
    $("prompt").disabled = busy || navigating;
  }
  function save() {
    if (!chat || busy) return saveQueue;
    const id = chat.id, body = settings();
    saveQueue = saveQueue.then(async () => {
      try {
        await api("chats/" + id, "PUT", body); pendingSaveError = null;
        const item = chats.find(c => c.id === id);
        if (item) { item.title = body.title; item.model = body.model; renderHistory(); }
      }
      catch (e) { pendingSaveError = e; banner(e.message); }
    });
    return saveQueue;
  }
  async function flush() { await save(); if (pendingSaveError) throw pendingSaveError; }
  async function history() { chats = (await api("chats")).chats; renderHistory(); }
  function renderHistory() {
    const list = $("chatList"); list.replaceChildren();
    const query = $("chatSearch").value.toLocaleLowerCase();
    for (const item of chats.filter(c => (c.title || t("Новый диалог")).toLocaleLowerCase().includes(query))) {
      const button = node("button", "chat-item" + (chat && chat.id === item.id ? " is-active" : ""), item.title || t("Новый диалог"));
      button.title = item.title || t("Новый диалог");
      button.onclick = () => guarded(async () => { await flush(); await open(item.id); });
      list.append(button);
    }
    if (!chats.length) list.append(node("p", "hint", t("Нет сохранённых диалогов")));
    controls();
  }
  async function open(id) {
    navigating = true; controls();
    try {
    chat = await api("chats/" + id);
    $("chatTitle").value = chat.title;
    $("system").value = chat.system;
    $("temperature").value = chat.temperature;
    $("topP").value = chat.top_p;
    $("maxTokens").value = chat.max_tokens;
    renderModels(); renderMessages(); renderHistory();
    $("generationInfo").textContent = "";
    } finally { navigating = false; controls(); }
  }
  async function newChat() {
    navigating = true; controls();
    try {
      if (chat) await flush();
      const created = await api("chats", "POST");
      await history(); await open(created.id); $("prompt").value = ""; $("prompt").focus();
    } finally { navigating = false; controls(); }
  }
  // A small safe renderer: paragraphs, headings, emphasis, inline/fenced code.
  // HTML and model-generated links deliberately remain text.
  function inline(element, text) {
    const regex = /(`[^`\n]+`|\*\*[^*\n]+\*\*)/g; let offset = 0, match;
    while ((match = regex.exec(text))) {
      element.append(document.createTextNode(text.slice(offset, match.index)));
      const code = match[0].startsWith("`");
      element.append(node(code ? "code" : "strong", "", match[0].slice(code ? 1 : 2, code ? -1 : -2)));
      offset = regex.lastIndex;
    }
    element.append(document.createTextNode(text.slice(offset)));
  }
  function markdown(element, text) {
    element.replaceChildren();
    text.split("```").forEach((part, index) => {
      if (index % 2) {
        const newline = part.indexOf("\n"), pre = node("pre");
        pre.append(node("code", "", newline >= 0 ? part.slice(newline + 1) : part)); element.append(pre);
      } else {
        for (const paragraph of part.split(/\n\s*\n/)) {
          if (!paragraph) continue;
          const heading = paragraph.match(/^#{1,3}\s+(.+)$/);
          const block = node(heading ? "h3" : "p"); inline(block, heading ? heading[1] : paragraph); element.append(block);
        }
      }
    });
  }
  function messageElement(message) {
    const article = node("article", "message message--" + message.role);
    article.append(node("div", "message__label", t(message.role === "user" ? "Вы" : "Ассистент")));
    if (message.reasoning) {
      const details = node("details", "reasoning");
      details.open = message.status === "streaming";
      details.append(node("summary", "", t("Рассуждение")), node("pre", "", message.reasoning)); article.append(details);
    }
    const body = node("div", "message__body"); markdown(body, message.content); article.append(body);
    const actions = node("div", "message__actions");
    if (message.content) {
      const copy = node("button", "btn-link", t("Копировать"));
      copy.onclick = () => guarded(async () => { await navigator.clipboard.writeText(message.content); copy.textContent = t("Скопировано"); });
      actions.append(copy);
    }
    if (message.status && !["completed", "streaming"].includes(message.status)) actions.append(node("span", "interrupted", t("Ответ прерван")));
    if (message.seconds) actions.append(node("span", "", message.seconds + " s"));
    if (message.error) article.append(node("p", "error-text", message.error));
    article.append(actions); return article;
  }
  function renderMessages() {
    const pane = $("messages"); pane.replaceChildren();
    if (!chat || !chat.messages.length) {
      const welcome = node("div", "welcome");
      welcome.append(node("span", "mark", "◆"), node("h2", "", t("О чём поговорим?")), node("p", "", t("Выберите локальную модель и начните диалог. Ваша история останется на этом компьютере.")));
      pane.append(welcome);
    } else for (const message of chat.messages) pane.append(messageElement(message));
    pane.scrollTop = pane.scrollHeight;
  }
  function renderModels() {
    const select = $("modelSelect"), previous = chat ? chat.model : select.value;
    select.replaceChildren(node("option", "", t("Выберите модель"))); select.firstChild.value = "";
    for (const model of models.filter(m => m.type === "llm")) {
      const instances = model.loaded_instances || [];
      const options = instances.length ? instances.map(i => ({value: i.id, label: model.display_name + " · " + t("В памяти")})) : [{value: model.key, label: model.display_name}];
      for (const option of options) { const el = node("option", "", option.label); el.value = option.value; select.append(el); }
    }
    if (previous && !Array.from(select.options).some(o => o.value === previous)) { const o = node("option", "", previous); o.value = previous; select.append(o); }
    select.value = previous;
    const cards = $("modelCards"); cards.replaceChildren();
    for (const model of models.filter(m => m.type === "llm")) {
      const card = node("article", "model-card"), row = node("div", "row"), loaded = (model.loaded_instances || []).length > 0;
      card.append(node("h3", "", model.display_name || model.key));
      card.append(node("p", "hint", [model.params_string, model.quantization && model.quantization.name, model.size_bytes ? (model.size_bytes / 1024 ** 3).toFixed(1) + " GB" : "", model.architecture].filter(Boolean).join(" · ")));
      row.append(node("span", "badge" + (loaded ? " loaded" : ""), t(loaded ? "В памяти" : "На диске")));
      if (!loaded) {
        const button = node("button", "btn-ghost", t("Загрузить"));
        button.onclick = () => modelAction("load", {model: model.key, context_length: Number($("contextLength").value)});
        row.append(button);
      } else for (const instance of model.loaded_instances) {
        const button = node("button", "btn-ghost", t("Выгрузить")); button.title = instance.id;
        button.onclick = () => modelAction("unload", {instance_id: instance.id}); row.append(button);
      }
      card.append(row); cards.append(card);
    }
    if (!cards.children.length) cards.append(node("p", "hint", t("Нет локальных языковых моделей. Скачайте модель в оригинальном LM Studio.")));
    controls();
  }
  async function refreshModels() { models = (await api("models")).models || []; renderModels(); }
  async function modelAction(action, body) {
    if (busy || modelBusy) return;
    modelBusy = true; controls(); banner(t(action === "load" ? "Загрузка модели…" : "Выгрузка модели…"));
    try { await api("models/" + action, "POST", body); await refreshModels(); banner(); }
    catch (e) { banner(e.message); }
    finally { modelBusy = false; controls(); }
  }
  async function send(retry = false) {
    if (busy || modelBusy || !ready || !chat) return;
    if (!$("modelSelect").value) { banner(t("Сначала выберите модель")); return; }
    const text = $("prompt").value.trim(); if (!retry && !text) return;
    await flush();
    if (busy) return;
    const id = chat.id, body = {...settings(), text, retry};
    busy = true; stopRequested = false; controller = new AbortController(); controls(); banner();
    $("generationInfo").textContent = t("Генерация…");
    if (retry && chat.messages.at(-1)?.role === "assistant") chat.messages.pop();
    if (!retry) chat.messages.push({role: "user", content: text});
    const answer = {role: "assistant", content: "", reasoning: "", status: "streaming"};
    chat.messages.push(answer); renderMessages();
    let article = $("messages").lastChild, terminal = false;
    try {
      const response = await fetch("api/chats/" + id + "/send", {method: "POST", signal: controller.signal, headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
      if (!response.ok) { const error = await response.json(); throw Error(typeof error.detail === "string" ? error.detail : JSON.stringify(error.detail)); }
      if (!retry) $("prompt").value = "";
      const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = "";
      while (true) {
        const {value, done} = await reader.read(); if (done) break;
        buffer += decoder.decode(value, {stream: true});
        let end;
        while ((end = buffer.indexOf("\n\n")) >= 0) {
          const block = buffer.slice(0, end); buffer = buffer.slice(end + 2);
          if (!block.startsWith("data: ")) continue;
          const delta = JSON.parse(block.slice(6));
          if (delta.error) throw Error(delta.error);
          if (delta.done) { terminal = true; answer.status = "completed"; }
          answer.content += delta.content || ""; answer.reasoning += delta.reasoning || "";
          const pane = $("messages"), nearBottom = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 140;
          const reasoningOpen = article.querySelector("details")?.open;
          const replacement = messageElement(answer);
          if (answer.status === "streaming" && reasoningOpen !== undefined && replacement.querySelector("details"))
            replacement.querySelector("details").open = reasoningOpen;
          article.replaceWith(replacement); article = replacement;
          if (nearBottom) pane.scrollTop = pane.scrollHeight;
        }
      }
      if (!terminal) throw Error(t("Соединение прервано до завершения ответа."));
    } catch (e) { if (e.name !== "AbortError" && !stopRequested) banner(e.message); }
    finally {
      controller = null;
      // The server commits its partial response in the stream's finally block.
      // Retry briefly after abort rather than overwriting that response locally.
      for (let attempt = 0; attempt < 10; attempt++) {
        try { const saved = await api("chats/" + id); if (saved.messages.length >= chat.messages.length) { chat = saved; break; } }
        catch (e) { if (attempt === 9) banner(e.message); }
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      $("chatTitle").value = chat.title;
      busy = false; controls(); renderMessages();
      $("generationInfo").textContent = terminal ? "" : t("Остановлено");
      await history(); $("prompt").focus();
    }
  }
  async function guarded(action) { try { await action(); } catch (e) { banner(e.message); } }
  async function health() {
    try {
      const state = await api("health"); const wasReady = ready; ready = state.state === "ready";
      $("statusDot").className = "status-dot" + (ready ? " is-ok" : state.state === "error" ? " is-error" : "");
      $("statusText").textContent = t(ready ? "Готов к работе" : state.state === "error" ? "Ошибка подключения" : "Подключение…");
      if (state.error) banner(state.error);
      if (ready && !wasReady) { banner(); await refreshModels(); }
    } catch (e) { ready = false; $("statusText").textContent = t("Ошибка подключения"); banner(e.message); }
    controls();
  }
  async function init() {
    await I18N.init();
    $("newChat").onclick = () => guarded(newChat);
    $("chatSearch").oninput = renderHistory;
    $("send").onclick = () => guarded(() => send(false));
    $("retry").onclick = () => guarded(() => send(true));
    $("stop").onclick = () => guarded(async () => {
      stopRequested = true;
      try { await api("chats/" + chat.id + "/cancel", "POST"); }
      finally { controller?.abort(); }
    });
    $("prompt").onkeydown = e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); guarded(() => send(false)); } };
    for (const id of ["chatTitle", "system", "temperature", "topP", "maxTokens"]) $(id).onchange = () => guarded(save);
    $("modelSelect").onchange = () => { if (chat) chat.model = $("modelSelect").value; controls(); guarded(save); };
    $("refreshModels").onclick = $("reloadModels").onclick = () => guarded(refreshModels);
    $("deleteChat").onclick = () => guarded(async () => {
      if (!chat || !confirm(t("Удалить этот диалог?"))) return;
      await flush(); await api("chats/" + chat.id, "DELETE"); chat = null; await history();
      if (chats.length) await open(chats[0].id); else await newChat();
    });
    document.querySelectorAll("[data-tab]").forEach(button => { button.onclick = () => {
      const chatTab = button.dataset.tab === "chat";
      $("chatPane").hidden = $("parameters").hidden = !chatTab; $("modelsPane").hidden = chatTab;
      document.querySelectorAll("[data-tab]").forEach(tab => tab.classList.toggle("is-active", tab === button));
    }; });
    window.addEventListener("imagine:languagechange", () => { renderHistory(); renderModels(); if (!busy) renderMessages(); health(); });
    await history(); if (chats.length) await open(chats[0].id); else await newChat();
    await health(); setInterval(health, 4000);
  }
  guarded(init);
})();
