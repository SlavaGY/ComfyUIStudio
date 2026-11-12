/* IEditor uses the same theme/language engine as the other Studio apps. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const slots = [null, null];
  const uploads = [null, null];
  const jobs = [];
  let selected = null, online = false, submitting = false, saveTimer, toastTimer;
  let saveChain = Promise.resolve();

  async function api(path, options = {}) {
    const response = await fetch(path, { signal: AbortSignal.timeout(60000), ...options });
    const data = await response.json();
    if (!response.ok) {
      const detail = data.detail;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail || data));
    }
    return data;
  }
  function jsonBody(data) {
    return { headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) };
  }
  function showError(message) {
    $("formError").textContent = message || "";
    $("formError").hidden = !message;
  }
  function toast(message) {
    $("toast").textContent = message;
    $("toast").hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { $("toast").hidden = true; }, 4500);
  }
  function formData() {
    return {
      prompt: $("prompt").value,
      images: slots.filter(Boolean).map((slot) => slot.image),
      steps: Number($("steps").value), cfg: Number($("cfg").value),
      sampler_name: $("sampler").value, megapixels: Number($("megapixels").value),
      seed: Number($("seed").value), randomize_seed: $("randomSeed").checked,
    };
  }
  function saveForm() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      const data = formData();
      saveChain = saveChain.catch(() => {}).then(() => api("api/state", { method: "PUT", ...jsonBody(data) }))
        .catch(() => { toast(t("Не удалось сохранить состояние формы")); });
    }, 400);
  }
  function updateButton() {
    const busy = uploads.some(Boolean);
    $("generate").disabled = !online || busy || submitting || !slots[0] || !$("prompt").value.trim();
    $("generate").textContent = busy ? t("Загрузка изображения…") : submitting ? t("Отправка…") : t("Обработать изображение");
    $("imageCount").textContent = `${slots.filter(Boolean).length} / 2`;
    $("seed").disabled = $("randomSeed").checked;
  }
  function inputUrl(ref) {
    const slash = ref.lastIndexOf("/");
    return "api/image?" + new URLSearchParams({filename: ref.slice(slash + 1), subfolder: ref.slice(0, slash), type: "input"});
  }
  function renderSlot(index) {
    const slot = slots[index];
    const busy = Boolean(uploads[index]);
    $("drop" + index).setAttribute("aria-label", t(index === 0 ? "Исходное изображение" : "Второе изображение"));
    $("remove" + index).setAttribute("aria-label", t("Убрать изображение"));
    $("drop" + index).classList.toggle("busy", busy);
    $("preview" + index).hidden = !slot;
    $("empty" + index).hidden = Boolean(slot);
    $("remove" + index).hidden = !slot || busy;
    if (slot) $("preview" + index).src = slot.url;
    else $("preview" + index).removeAttribute("src");
    $("caption" + index).textContent = busy ? t("Загрузка изображения…") : slot ? slot.name || t("Изображение загружено") : index === 0 ? t("PNG, JPEG, WebP, GIF · до 32 МБ") : t("Одежда, стиль, предмет или другой образец");
    updateButton();
  }
  async function upload(index, file) {
    if (!file) return;
    if (file.size > 32 * 1024 * 1024) throw new Error(t("Изображение больше 32 МБ"));
    uploads[index]?.abort();
    const controller = new AbortController();
    uploads[index] = controller;
    renderSlot(index);
    try {
      const body = new FormData();
      body.append("file", file);
      const result = await api("api/upload-image", {method: "POST", body, signal: controller.signal});
      if (uploads[index] !== controller) return;
      slots[index] = {...result, name: file.name};
      showError("");
      saveForm();
    } catch (error) {
      if (controller.signal.aborted) return;
      throw error;
    } finally {
      if (uploads[index] === controller) uploads[index] = null;
      renderSlot(index);
    }
  }
  async function uploadFiles(index, files) {
    if (!files.length) return;
    if (files.length > 2) { showError(t("Можно прикрепить не больше двух изображений")); return; }
    try {
      if (files.length === 2) await Promise.all([upload(0, files[0]), upload(1, files[1])]);
      else await upload(index, files[0]);
    } catch (error) { showError(error.message); }
  }
  function wireUploads() {
    for (let index = 0; index < 2; index++) {
      $("file" + index).addEventListener("change", (event) => {
        void uploadFiles(index, Array.from(event.target.files));
        event.target.value = "";
      });
      const drop = $("drop" + index);
      drop.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); $("file" + index).click(); }
      });
      drop.addEventListener("dragover", (event) => { event.preventDefault(); drop.classList.add("dragover"); });
      drop.addEventListener("dragleave", () => drop.classList.remove("dragover"));
      drop.addEventListener("drop", (event) => {
        event.preventDefault(); drop.classList.remove("dragover");
        void uploadFiles(index, Array.from(event.dataTransfer.files));
      });
      $("remove" + index).addEventListener("click", () => {
        slots[index] = null;
        if (index === 0 && slots[1]) { slots[0] = slots[1]; slots[1] = null; }
        renderSlot(0); renderSlot(1); saveForm();
      });
    }
  }
  function statusLabel(job) {
    if (job.connectionError) return t("Восстанавливаем связь с ComfyUI…");
    switch (job.state) {
      case "done": return t("Готово");
      case "error": return t("Ошибка обработки");
      case "cancelled": return t("Остановлено");
      case "running": return t("Обработка изображения…");
      case "pending": return t("В очереди: {n}", {n: job.queue_position || 1});
      default: return t("Ожидание ComfyUI…");
    }
  }
  function renderHistory() {
    $("history").replaceChildren();
    $("historyCount").textContent = jobs.length ? String(jobs.length) : "";
    for (const job of jobs) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "history-item" + (job === selected ? " selected" : "");
      button.title = job.prompt || statusLabel(job);
      button.setAttribute("aria-label", `${statusLabel(job)}. ${job.prompt || ""}`);
      if (job.images?.[0]) {
        const image = document.createElement("img");
        image.src = job.images[0].url; image.alt = ""; image.loading = "lazy";
        button.append(image);
      } else {
        const placeholder = document.createElement("span");
        placeholder.className = "history-placeholder"; placeholder.textContent = statusLabel(job);
        button.append(placeholder);
      }
      const caption = document.createElement("span");
      caption.className = "history-caption";
      caption.textContent = `${new Date(job.created_at * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})} · ${job.image_count} ${t("реф.")}`;
      button.append(caption);
      button.addEventListener("click", () => { selected = job; renderResult(); renderHistory(); });
      $("history").append(button);
    }
  }
  function renderResult() {
    const job = selected;
    $("resultImageButton").setAttribute("aria-label", t("Результат"));
    $("resultImage").alt = t("Результат");
    $("largeImage").alt = t("Результат");
    const done = job?.state === "done" && job.images?.length;
    const failed = job && ["error", "cancelled"].includes(job.state);
    $("resultEmpty").hidden = Boolean(job);
    $("resultImageButton").hidden = !done;
    $("resultActions").hidden = !done;
    $("jobProgress").hidden = !job || done || failed;
    $("resultError").hidden = !failed;
    $("resultError").textContent = failed ? job.error || statusLabel(job) : "";
    $("jobInfo").textContent = job ? statusLabel(job) : "";
    if (job && !done && !failed) $("progressText").textContent = statusLabel(job);
    if (done) {
      $("resultImage").src = job.images[0].url;
      $("downloadResult").href = job.images[0].url;
      $("downloadResult").download = job.images[0].filename || "IEditor.png";
      $("resultSeed").textContent = `${t("Сид")}: ${job.seed}`;
    }
    updateElapsed();
  }
  function updateElapsed() {
    if (selected) $("elapsed").textContent = t("Прошло {n} с", {n: Math.max(0, Math.floor(Date.now() / 1000 - selected.created_at))});
  }
  async function refreshJobs() {
    let failed = false, changed = false;
    await Promise.all(jobs.filter((job) => !["done", "error", "cancelled"].includes(job.state)).map(async (job) => {
      try {
        const result = await api(`api/generate/${job.prompt_id}/status`);
        job.connectionError = false;
        if (result.state === "unknown") {
          job.unknownSince ??= Date.now();
          if (Date.now() - job.unknownSince > 20000) Object.assign(result, {state: "error", error: t("Задание больше не найдено в ComfyUI. Запустите обработку повторно.")});
        } else delete job.unknownSince;
        Object.assign(job, result); changed = true;
      } catch (error) { job.connectionError = true; failed = true; }
    }));
    if (changed || failed) { renderResult(); renderHistory(); }
    return !failed;
  }
  async function refreshStatus() {
    try {
      const result = await api("api/comfyui/status"); online = result.alive;
    } catch (error) { online = false; }
    $("statusDot").className = "status-dot " + (online ? "alive" : "dead");
    $("statusText").textContent = online ? t("ComfyUI на связи") : t("ComfyUI недоступен");
    updateButton();
    return online;
  }
  async function generate(event) {
    event.preventDefault();
    if ($("generate").disabled) return;
    if (!$("editForm").reportValidity()) return;
    submitting = true; updateButton(); showError("");
    try {
      const job = await api("api/generate", {method: "POST", ...jsonBody(formData())});
      job.state = "pending"; jobs.unshift(job); selected = job;
      if (jobs.length > 30) jobs.length = 30;
      $("seed").value = String(job.seed); saveForm(); renderResult(); renderHistory();
      void refreshJobs();
    } catch (error) { showError(error.message); }
    finally { submitting = false; updateButton(); }
  }
  async function init() {
    wireUploads();
    $("editForm").addEventListener("submit", generate);
    $("editForm").addEventListener("input", () => { saveForm(); updateButton(); });
    $("prompt").addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") { event.preventDefault(); $("editForm").requestSubmit(); }
    });
    $("cancelJob").addEventListener("click", async () => {
      const job = selected;
      if (!job) return;
      $("cancelJob").disabled = true;
      try {
        const result = await api(`api/generate/${job.prompt_id}/interrupt`, {method: "POST"});
        if (result.state !== "interrupting") Object.assign(job, result);
        renderResult(); renderHistory();
        void refreshJobs();
      } catch (error) { showError(error.message); }
      finally { $("cancelJob").disabled = false; }
    });
    $("freeMemory").addEventListener("click", async () => {
      $("freeMemory").disabled = true;
      try { await api("api/comfyui/free-memory", {method: "POST"}); toast(t("Модели выгружены из памяти")); }
      catch (error) { showError(error.message); }
      finally { $("freeMemory").disabled = false; }
    });
    $("resultImageButton").addEventListener("click", () => {
      $("largeImage").src = $("resultImage").src; $("imageDialog").showModal();
    });
    $("closeDialog").addEventListener("click", () => $("imageDialog").close());
    $("imageDialog").addEventListener("click", (event) => { if (event.target === $("imageDialog")) $("imageDialog").close(); });
    $("reuseResult").addEventListener("click", async () => {
      const result = selected?.images?.[0];
      if (!result) return;
      $("reuseResult").disabled = true;
      try {
        const response = await fetch(result.url);
        if (!response.ok) throw new Error(t("Не удалось загрузить результат"));
        await upload(0, new File([await response.blob()], result.filename || "IEditor.png", {type: "image/png"}));
        toast(t("Результат добавлен как исходник"));
      } catch (error) { showError(error.message); }
      finally { $("reuseResult").disabled = false; }
    });
    await I18N.init();
    try {
      const [config, state, savedJobs] = await Promise.all([api("api/config"), api("api/state"), api("api/jobs")]);
      const data = {...config.defaults, ...state};
      $("modelName").textContent = config.defaults.model;
      $("encoderName").textContent = config.defaults.text_encoder;
      $("sampler").replaceChildren(...config.samplers.map((name) => new Option(name, name)));
      $("prompt").value = data.prompt || "";
      for (const [id, key] of [["steps", "steps"], ["megapixels", "megapixels"], ["cfg", "cfg"], ["seed", "seed"], ["sampler", "sampler_name"]]) $(id).value = String(data[key]);
      $("randomSeed").checked = data.randomize_seed;
      (data.images || []).slice(0, 2).forEach((image, index) => { slots[index] = {image, url: inputUrl(image)}; });
      for (const saved of savedJobs.jobs) jobs.push({...saved, state: "pending", ...saved.result});
      const requestedJob = new URLSearchParams(window.location.search).get("prompt_id");
      selected = jobs.find((job) => job.prompt_id === requestedJob) || jobs[0] || null;
      if (config.missing.length) {
        $("modelWarning").textContent = t("В ComfyUI не найдены файлы: {files}", {files: config.missing.join(", ")});
        $("modelWarning").hidden = false;
      }
    } catch (error) { showError(error.message); }
    renderSlot(0); renderSlot(1); renderResult(); renderHistory();
    await refreshStatus();
    window.createBackgroundPoll(refreshStatus, 6000);
    window.createBackgroundPoll(refreshJobs, 2000, {immediate: true});
    setInterval(updateElapsed, 1000);
  }
  document.addEventListener("imagine:languagechange", () => {
    renderSlot(0); renderSlot(1); renderResult(); renderHistory(); void refreshStatus();
  });
  document.addEventListener("DOMContentLoaded", () => { void init().catch((error) => showError(error.message)); });
})();
