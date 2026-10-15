/* ============================================================
   Imagine Pony — i18n + синхронизация темы/языка с ComfyUIStudio
   ============================================================
   Здесь только СЛОВАРЬ; движок общий с Imagine (comfyui_studio/web_common/
   static/js/i18n_engine.js). RU — исходный текст и он же ключ словаря,
   EN — перевод; строки без перевода показываются как есть. Тема и язык
   ТОЛЬКО читаются из общих файлов Studio через GET/PUT api/ui-prefs
   (своего переключателя у приложения нет).
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
    "Останавливает то, что ComfyUI выполняет прямо сейчас (не только эту генерацию, если их несколько в очереди)": "Stops whatever ComfyUI is running right now (not only this generation if several are queued)",
    "Остановить": "Stop",
    "Очистить слот": "Clear slot",
    "Список LoRA": "LoRA list",
    "Список LoRA пуст или недоступен": "LoRA list is empty or unavailable",
    "Ничего не найдено": "Nothing found",
    "…и ещё {n}, уточните поиск": "…and {n} more, refine the search",
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

  // Движок (t(), переключение языка, темы, синхронизация с api/ui-prefs) --
  // общий для Imagine и Imagine Pony: comfyui_studio/web_common/static/js/
  // i18n_engine.js. index.html подключает его ПЕРЕД этим файлом.
  const I18N = window.createI18n({ storagePrefix: "imaginepony", EN });

  window.I18N = I18N;
  // ВАЖНО: app.js вызывает переводы как глобальную функцию t('...'),
  // а не I18N.t('...') -- см. EN выше и все места вызова в app.js.
  // Без этой строки КАЖДЫЙ вызов t() падал бы с "ReferenceError: t is not
  // defined": исключение обрывало функцию на середине (например,
  // wireStaticHandlers() могла не успеть навесить обработчики кнопок), и
  // статус ComfyUI зависал на "checking...".
  window.t = I18N.t;
})();
