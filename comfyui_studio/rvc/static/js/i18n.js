/* ============================================================
   Ultimate RVC — i18n + синхронизация темы/языка со Studio
   Устроено как у Imagine: ключ словаря -- русский исходный текст 1:1 из
   index.html / app.js, EN -- перевод; для строки без записи показывается
   сама строка. Тема и язык общие с остальным комплектом через
   GET/PUT api/ui-prefs (ОТНОСИТЕЛЬНЫЙ путь, см. app.js); если сервер ничего
   не отдал (нет общего файла), берётся последний выбор из localStorage.
   ============================================================ */
(function () {
  "use strict";

  const EN = {
    "Загрузка…": "Loading…",
    "проверка движка…": "checking engine…",
    "движок готов": "engine ready",
    "ошибка движка": "engine error",
    "загрузка движка RVC…": "loading RVC engine…",
    "нет связи с сервером": "no connection to the server",
    "Тема оформления": "Color theme",
    "Язык интерфейса": "Interface language",
    "Кавер": "Cover",
    "Озвучка": "Speech",
    "Модели": "Models",
    "Песня": "Song",
    "Перетащите аудиофайл сюда или нажмите, чтобы выбрать": "Drop an audio file here or click to choose",
    "или ссылка YouTube / путь к файлу на этом ПК": "or a YouTube link / a file path on this PC",
    "Голосовая модель": "Voice model",
    "Обновить список": "Refresh list",
    "— нет моделей, добавьте на вкладке «Модели» —": "— no models, add one on the “Models” tab —",
    "Создать кавер": "Create cover",
    "Текст": "Text",
    "Что должен сказать голос…": "What the voice should say…",
    "Голос Edge TTS": "Edge TTS voice",
    "Язык": "Language",
    "Голос": "Voice",
    "Тон, Гц": "Pitch, Hz",
    "Скорость, %": "Speed, %",
    "Громкость, %": "Volume, %",
    "Список голосов Edge TTS загружается из интернета.": "The Edge TTS voice list is loaded from the internet.",
    "Не удалось получить список голосов (нужен интернет): ": "Could not get the voice list (internet required): ",
    "Озвучить": "Generate speech",
    "Установленные модели": "Installed models",
    "Моделей пока нет.": "No models yet.",
    "Удалить": "Delete",
    "Удалить модель «{}»?": "Delete model “{}”?",
    "Добавить из файлов": "Add from files",
    "Имя модели": "Model name",
    "Укажите имя модели": "Enter a model name",
    "Файлы: .pth и .index или один .zip": "Files: .pth and .index, or a single .zip",
    "Выберите файлы модели": "Choose the model files",
    "Добавить модель": "Add model",
    "Загрузка: ": "Uploading: ",
    "Установка модели…": "Installing the model…",
    "Модель добавлена.": "Model added.",
    "Скачать по ссылке": "Download from a link",
    "Ссылка на zip-архив": "Link to a zip archive",
    "Скачать модель": "Download model",
    "Скачивание… это может занять несколько минут": "Downloading… this may take a few minutes",
    "Модель скачана.": "Model downloaded.",
    "Каталог публичных моделей": "Public model catalog",
    "поиск по названию и описанию": "search by name and description",
    "Показать каталог": "Show catalog",
    "все теги": "all tags",
    "Скачать": "Download",
    "Ничего не найдено.": "Nothing found.",
    "Показаны первые 100 из {}. Уточните поиск.": "Showing the first 100 of {}. Refine the search.",
    "Очередь и результаты": "Queue and results",
    "Заданий пока нет.": "No jobs yet.",
    "Загрузка файла…": "Uploading the file…",
    "Задание добавлено в очередь.": "Job added to the queue.",
    "в очереди": "queued",
    "выполняется": "running",
    "готово": "done",
    "ошибка": "error",
    "отменено": "cancelled",
    "Отменить": "Cancel",
    "отмена после текущего этапа…": "cancelling after the current stage…",
    "Высота тона": "Pitch",
    "Сдвиг голоса, октав": "Voice shift, octaves",
    "Сдвиг тона, полутонов (голос и музыка)": "Pitch shift, semitones (voice and music)",
    "Сдвиг тона, полутонов": "Pitch shift, semitones",
    "Преобразование голоса": "Voice conversion",
    "Метод определения высоты": "Pitch detection method",
    "Эмбеддер": "Embedder",
    "Влияние индекса": "Index influence",
    "Смешивание громкости": "Volume mix",
    "Защита согласных": "Consonant protection",
    "ID говорящего": "Speaker ID",
    "Обработка вокала": "Vocal processing",
    "Разбивать вокал на фрагменты": "Split vocals into segments",
    "Автотюн": "Autotune",
    "Сила автотюна": "Autotune strength",
    "Очистка вокала": "Clean vocals",
    "Сила очистки": "Clean strength",
    "Подгонять высоту под диапазон модели": "Fit pitch to the model's range",
    "Порог подгонки высоты, Гц": "Pitch fit threshold, Hz",
    "Обработка речи": "Speech processing",
    "Разбивать речь на фрагменты": "Split speech into segments",
    "Очистка речи": "Clean speech",
    "Микс, дБ": "Mix, dB",
    "Вокал": "Vocals",
    "Инструментал": "Instrumental",
    "Бэк-вокал": "Backing vocals",
    "Реверберация вокала": "Vocal reverb",
    "Размер комнаты": "Room size",
    "Эффект": "Effect",
    "Исходный звук": "Dry sound",
    "Затухание": "Damping",
    "Громкость": "Volume",
    "Усиление результата, дБ": "Output gain, dB",
    "Результат": "Output",
    "Формат": "Format",
    "Частота дискретизации": "Sample rate",
    "Имя файла (необязательно)": "File name (optional)",
    // Подписи файлов результата (engine.py: SONG_RESULT_LABELS и др.)
    "Готовый кавер": "Finished cover",
    "Исходная песня": "Original song",
    "Основной вокал": "Main vocals",
    "Основной вокал без реверберации": "Main vocals (de-reverbed)",
    "Реверберация": "Reverb",
    "Преобразованный вокал": "Converted vocals",
    "Вокал с эффектами": "Vocals with effects",
    "Инструментал (сдвиг тона)": "Instrumental (pitch-shifted)",
    "Бэк-вокал (сдвиг тона)": "Backing vocals (pitch-shifted)",
    "Файл": "File",
  };

  const THEMES = ["Dark", "Light", "Nord", "Catppuccin Mocha", "Dracula", "GitHub Dark"];
  const listeners = [];
  let lang = "ru";

  const $ = (s) => document.querySelector(s);
  const t = (s) => (lang === "en" && EN[s] ? EN[s] : s);

  function applyI18n() {
    document.documentElement.lang = lang;
    document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
    document.querySelectorAll("[data-i18n-title]").forEach((el) => { el.title = t(el.dataset.i18nTitle); });
    document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => { el.placeholder = t(el.dataset.i18nPlaceholder); });
  }

  function remember(key, value) {
    try { localStorage.setItem("rvc." + key, value); } catch (e) { /* без сохранения */ }
  }
  function recall(key) {
    try { return localStorage.getItem("rvc." + key); } catch (e) { return null; }
  }

  function pushPrefs(prefs) {
    fetch("api/ui-prefs", {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(prefs),
    }).catch(() => { /* общий файл недоступен -- останется localStorage */ });
  }

  function setLanguage(code, persist) {
    lang = code === "en" ? "en" : "ru";
    $("#langSelect").value = lang;
    remember("lang", lang);
    applyI18n();
    listeners.forEach((f) => f());
    if (persist) pushPrefs({ language: lang });
  }

  function setTheme(name, persist) {
    if (!THEMES.includes(name)) name = "GitHub Dark";
    document.documentElement.dataset.theme = name;
    $("#themeSelect").value = name;
    remember("theme", name);
    if (persist) pushPrefs({ theme: name });
  }

  async function init() {
    const themeSelect = $("#themeSelect");
    THEMES.forEach((name) => {
      const o = document.createElement("option");
      o.value = o.textContent = name;
      themeSelect.append(o);
    });
    themeSelect.addEventListener("change", () => setTheme(themeSelect.value, true));
    $("#langSelect").addEventListener("change", () => setLanguage($("#langSelect").value, true));

    setTheme(recall("theme") || "GitHub Dark", false);
    setLanguage(recall("lang") || "ru", false);
    try {
      const res = await fetch("api/ui-prefs");
      const prefs = await res.json();
      if (prefs.theme) setTheme(prefs.theme, false);
      if (prefs.language) setLanguage(prefs.language, false);
    } catch (e) { /* общих настроек нет -- остаются локальные */ }
  }

  window.RVC_I18N = { t, applyI18n, setLanguage, onChange: (f) => listeners.push(f), EN };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
