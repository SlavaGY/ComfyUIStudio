// Imagine — фронтенд. Ванильный JS, без сборки.
//
// Два независимых способа выбора стиля, оба разом на странице, оба
// теперь ОДИНАКОВОГО поведения -- одиночный выбор, подсветка активного:
//  - "Стили" (styletilegrid) -- плоские кликабельные плашки с картинкой.
//  - "Категории" (styleboxrow) -- ДЕРЕВО произвольной глубины: узел
//    "category" -- плашка с выпадающим списком вариантов (как раньше),
//    узел "group" -- "категория категорий", плашка БЕЗ списка, клик по
//    ней разворачивает рамку с вложенными узлами (снова category или
//    group, рекурсивно, без ограничения глубины).
// Ни то ни другое НЕ пишется в видимые textarea промпта/негатива --
// тексты стилей/категорий примешиваются к тому, что реально введено в
// полях, только в момент отправки на генерацию (см. computeFinalPrompt).
// Активный выбор виден пользователю через строку-сводку под полями.
// LoRA из обеих систем идут в общий пул с общим лимитом max_loras.

const state = {
  config: null,
  devMode: false,
  selectedLoras: new Map(),      // ключ 'tile:<id>' | 'box:<categoryId>' -> {file, strength}
  activeTileId: null,             // id активной плашки в "Стили" (максимум одна)
  activeBoxSelections: new Map(), // categoryId -> optionId (по одному варианту на категорию, id уникален во всём дереве)
  pendingImageUrl: '',            // картинка, загруженная в форму добавления стиля, но ещё не сохранённая
  editingStyleIndex: null,        // индекс редактируемого стиля в state.config.styles, либо null
  editingNodeId: null,            // id редактируемого узла дерева "Категорий" (категория или группа), либо null
  editingOption: null,            // {categoryId, optIdx} редактируемого варианта категории, либо null
  editingAlwaysLoraIndex: null,   // индекс редактируемой записи в state.config.always_loras, либо null
  expandedBoxIds: new Set(),      // id узлов, развёрнутых в СПИСКЕ дев-режима (категория -- показывает варианты, группа -- детей); по умолчанию все свёрнуты
  expandedGroupIds: new Set(),    // id ГРУПП, развёрнутых на самой СТРАНИЦЕ (обычный режим); по умолчанию все свёрнуты
  activeGenerations: 0,           // сколько заданий генерации сейчас в работе (для подписи кнопки, не блокирует её)
};

const el = (id) => document.getElementById(id);

// Состояние перетаскивания увеличенной картинки в лайтбоксе мышкой
// (отдельно от общего state -- сугубо переходное UI-состояние жеста,
// сбрасывать/сериализовать его никогда не требуется).
const lightboxDrag = { active: false, moved: false, startX: 0, startY: 0, startScrollLeft: 0, startScrollTop: 0 };

// ---------------------------------------------------------------- init

async function init() {
  await I18N.init();
  try {
    await loadMode();
    await loadConfig();
    await renderAspectRatios();
    renderMegapixels();
    renderStyleTileGrid();
    renderStyleBoxes();
    updateActiveSummary();
    wireStaticHandlers();
    await refreshComfyStatus();
  } catch (e) {
    // Не даём экрану загрузки зависнуть навечно, даже если что-то из
    // инициализации упало (например бэкенд ещё не поднялся) -- лучше
    // показать пользователю рабочий (пусть и не полностью заполненный)
    // интерфейс с ошибкой, чем бесконечный спиннер.
    console.error('Ошибка инициализации:', e);
  } finally {
    hideLoadingScreen();
  }
  setInterval(refreshComfyStatus, 6000);
}

function hideLoadingScreen() {
  const screen = el('loadingScreen');
  if (!screen) return;
  screen.classList.add('loading-screen--hidden');
  screen.addEventListener('transitionend', () => { screen.hidden = true; }, { once: true });
}

async function loadMode() {
  const res = await fetch('/api/mode');
  const data = await res.json();
  state.devMode = !!data.dev;
  el('devToggle').hidden = !state.devMode;
}

async function loadConfig() {
  const res = await fetch('/api/config');
  state.config = await res.json();
  const gen = state.config.generation;
  el('steps').value = gen.default_steps;
  el('cfg').value = gen.default_cfg;
  el('samplerName').value = gen.default_sampler;
  el('batchSize').value = gen.default_batch_size;
}

async function renderAspectRatios() {
  // Живой список от ComfyUI (см. GET /api/aspect-ratios) -- захардкоженный
  // в конфиге список рассинхронизировался с реальным набором значений,
  // которые принимает узел ResolutionSelector, и ComfyUI отклонял
  // задание с value_not_in_list. Конфиг остаётся только офлайн-фолбэком.
  const sel = el('aspectRatio');
  sel.innerHTML = '';
  let options = state.config.generation.aspect_ratios;
  try {
    const res = await fetch('/api/aspect-ratios');
    const data = await res.json();
    if (data.options && data.options.length) options = data.options;
  } catch {
    // остаёмся на офлайн-фолбэке из конфига
  }
  for (const ratio of options) {
    const opt = document.createElement('option');
    opt.value = ratio;
    opt.textContent = ratio;
    sel.appendChild(opt);
  }
}

function renderMegapixels() {
  const sel = el('megapixels');
  sel.innerHTML = '';
  const gen = state.config.generation;
  for (const mp of gen.megapixel_options) {
    const opt = document.createElement('option');
    opt.value = mp;
    opt.textContent = `${mp} MP`;
    if (mp === gen.default_megapixels) opt.selected = true;
    sel.appendChild(opt);
  }
}

// ================================================================
// "Стили" — плоские кликабельные плашки (одиночный выбор)
// ================================================================

function renderStyleTileGrid() {
  const grid = el('styleTileGrid');
  grid.innerHTML = '';
  const styles = state.config.styles || [];
  el('styleTileEmptyHint').hidden = styles.length > 0;

  for (const style of styles) {
    const tile = document.createElement('div');
    tile.className = 'styletile';
    tile.dataset.id = style.id;
    if (style.id === state.activeTileId) tile.classList.add('active');

    if (style.image) {
      const img = document.createElement('img');
      img.className = 'styletile__thumb';
      img.src = style.image;
      img.alt = style.label;
      tile.appendChild(img);
    } else {
      const placeholder = document.createElement('div');
      placeholder.className = 'styletile__thumb styletile__thumb--placeholder';
      placeholder.textContent = (style.label || '?').trim().charAt(0).toUpperCase();
      tile.appendChild(placeholder);
    }

    const label = document.createElement('div');
    label.className = 'styletile__label';
    label.textContent = style.label;
    tile.appendChild(label);

    tile.addEventListener('click', () => onStyleTileClick(style));
    grid.appendChild(tile);
  }
}

function onStyleTileClick(style) {
  const wasActive = state.activeTileId === style.id;

  // Снимаем предыдущий активный стиль (если был) -- выбор одиночный,
  // как у выпадающего списка.
  if (state.activeTileId) {
    state.selectedLoras.delete(`tile:${state.activeTileId}`);
  }
  state.activeTileId = null;

  if (!wasActive) {
    if (style.lora_file) {
      const max = effectiveMaxLoras();
      if (state.selectedLoras.size >= max) {
        showError(t('Можно выбрать не больше {max} LoRA одновременно (часть лимита занята "всегда"-LoRA)', {max}));
        renderStyleTileGrid();
        updateActiveSummary();
        return;
      }
      state.selectedLoras.set(`tile:${style.id}`, {
        file: style.lora_file,
        strength: style.lora_strength,
      });
    }
    state.activeTileId = style.id;
  }

  renderStyleTileGrid();
  updateActiveSummary();
}

// ================================================================
// Дерево "Категорий" — общие хелперы (используются и на странице, и в дев-режиме)
// ================================================================

// Ищет узел по id в дереве, возвращает сам узел или null.
function findNode(nodes, id) {
  const found = findNodeWithParent(nodes, id);
  return found ? found.node : null;
}

// То же самое, но возвращает ещё и массив-владелец узла + индекс в нём
// + сам родительский узел (или null для верхнего уровня) -- нужно для
// удаления/перемещения, где мало самого узла, надо знать, откуда его убрать.
function findNodeWithParent(nodes, id, parentNode = null) {
  for (let i = 0; i < nodes.length; i++) {
    if (nodes[i].id === id) {
      return { node: nodes[i], array: nodes, index: i, parentNode };
    }
    if (nodes[i].type === 'group') {
      const found = findNodeWithParent(nodes[i].children, id, nodes[i]);
      if (found) return found;
    }
  }
  return null;
}

// Плоский список всех ГРУПП дерева с глубиной вложенности -- для
// выпадающего списка "Куда добавить" в дев-режиме.
function flattenGroups(nodes, depth = 0) {
  let result = [];
  for (const node of nodes) {
    if (node.type === 'group') {
      result.push({ id: node.id, label: node.label, depth });
      result = result.concat(flattenGroups(node.children, depth + 1));
    }
  }
  return result;
}

// Плоский список всех КАТЕГОРИЙ (листьев) дерева с глубиной -- для
// выпадающего списка "В какую категорию" при добавлении варианта.
function flattenCategories(nodes, depth = 0) {
  let result = [];
  for (const node of nodes) {
    if (node.type === 'category') {
      result.push({ id: node.id, label: node.label, depth });
    } else if (node.type === 'group') {
      result = result.concat(flattenCategories(node.children, depth + 1));
    }
  }
  return result;
}

// id всех категорий внутри поддерева узла (сам узел, если это
// категория; либо рекурсивно все категории внутри группы) -- нужно,
// чтобы при удалении группы почистить активный выбор/LoRA всех
// категорий, которые в ней были, а не только удаляемого узла напрямую.
function collectDescendantCategoryIds(node) {
  if (node.type === 'category') return [node.id];
  let ids = [];
  for (const child of node.children) {
    ids = ids.concat(collectDescendantCategoryIds(child));
  }
  return ids;
}

// Массив-контейнер, куда добавляется новый узел: верхний уровень
// (state.config.boxes), либо .children конкретной группы по id.
function getChildrenArray(parentId) {
  if (!parentId) return state.config.boxes;
  const parent = findNode(state.config.boxes, parentId);
  return parent && parent.type === 'group' ? parent.children : state.config.boxes;
}

function idExistsInTree(id) {
  return findNode(state.config.boxes, id) !== null;
}

// slugify + проверка уникальности по ВСЕМУ дереву (не только среди
// соседей) -- id узла используется как ключ в state.activeBoxSelections
// и state.selectedLoras независимо от глубины вложенности, так что
// коллизия с любым другим узлом дерева, даже в другой ветке, реально
// сломала бы выбор.
function uniqueNodeId(label) {
  const base = slugify(label);
  let id = base;
  let i = 2;
  while (idExistsInTree(id)) {
    id = `${base}-${i}`;
    i++;
  }
  return id;
}



function renderStyleBoxes() {
  const row = el('styleBoxRow');
  row.innerHTML = '';
  const boxes = state.config.boxes || [];
  el('styleBoxEmptyHint').hidden = boxes.length > 0;
  for (const node of boxes) {
    row.appendChild(buildBoxNodeElement(node));
  }
}

function buildBoxNodeElement(node) {
  return node.type === 'group' ? buildGroupElement(node) : buildCategoryTile(node);
}

function buildCategoryTile(box) {
  const boxEl = document.createElement('div');
  boxEl.className = 'stylebox';
  boxEl.dataset.id = box.id;
  if (state.activeBoxSelections.has(box.id)) boxEl.classList.add('has-selection');

  const label = document.createElement('div');
  label.className = 'stylebox__label';
  label.textContent = box.label;
  boxEl.appendChild(label);

  const select = document.createElement('select');
  const noneOpt = document.createElement('option');
  noneOpt.value = '';
  noneOpt.textContent = t('— нет —');
  select.appendChild(noneOpt);
  for (const option of box.options) {
    const opt = document.createElement('option');
    opt.value = option.id;
    opt.textContent = option.label;
    select.appendChild(opt);
  }
  select.value = state.activeBoxSelections.get(box.id) || '';
  select.addEventListener('change', () => onBoxOptionChange(box, select.value, boxEl));
  boxEl.appendChild(select);

  return boxEl;
}

function buildGroupElement(group) {
  const expanded = state.expandedGroupIds.has(group.id);

  if (!expanded) {
    // Свёрнутая группа выглядит как обычная плашка (тот же .stylebox),
    // только без select и с пунктирной рамкой -- клик целиком по ней
    // разворачивает вложенные узлы.
    const tile = document.createElement('div');
    tile.className = 'stylebox stylebox--group';
    tile.dataset.id = group.id;
    const label = document.createElement('div');
    label.className = 'stylebox__label';
    label.textContent = `▸ ${group.label}`;
    tile.appendChild(label);
    tile.addEventListener('click', () => {
      state.expandedGroupIds.add(group.id);
      renderStyleBoxes();
    });
    return tile;
  }

  const frame = document.createElement('div');
  frame.className = 'stylegroup';
  frame.dataset.id = group.id;

  const header = document.createElement('div');
  header.className = 'stylegroup__header';
  header.textContent = `▾ ${group.label}`;
  header.addEventListener('click', () => {
    state.expandedGroupIds.delete(group.id);
    renderStyleBoxes();
  });
  frame.appendChild(header);

  const childrenWrap = document.createElement('div');
  childrenWrap.className = 'stylegroup__children';
  for (const child of group.children) {
    childrenWrap.appendChild(buildBoxNodeElement(child));
  }
  frame.appendChild(childrenWrap);

  return frame;
}

function onBoxOptionChange(box, optionId, boxEl) {
  const key = `box:${box.id}`;
  state.selectedLoras.delete(key);
  state.activeBoxSelections.delete(box.id);
  boxEl.classList.remove('has-selection');

  if (!optionId) {
    updateActiveSummary();
    return;
  }

  const option = box.options.find((o) => o.id === optionId);
  if (!option) {
    updateActiveSummary();
    return;
  }

  if (option.lora_file) {
    const max = effectiveMaxLoras();
    if (state.selectedLoras.size >= max) {
      showError(t('Можно выбрать не больше {max} LoRA одновременно (часть лимита занята "всегда"-LoRA)', {max}));
      boxEl.querySelector('select').value = '';
      updateActiveSummary();
      return;
    }
    state.selectedLoras.set(key, { file: option.lora_file, strength: option.lora_strength });
  }

  state.activeBoxSelections.set(box.id, optionId);
  boxEl.classList.add('has-selection');
  updateActiveSummary();
}

// ---------------------------------------------------------------- сводка активных выборов

function updateActiveSummary() {
  const parts = [];
  for (const lora of state.config.always_loras || []) {
    parts.push(`⚡ ${lora.label}`);
  }
  if (state.activeTileId) {
    const style = (state.config.styles || []).find((s) => s.id === state.activeTileId);
    if (style) parts.push(style.label);
  }
  for (const [boxId, optionId] of state.activeBoxSelections) {
    const box = findNode(state.config.boxes, boxId);
    const option = box && box.options.find((o) => o.id === optionId);
    if (box && option) parts.push(`${box.label}: ${option.label}`);
  }

  const line = el('activeSummary');
  if (!parts.length) {
    line.hidden = true;
    return;
  }
  line.hidden = false;
  line.textContent = t('Активно:') + ' ' + parts.join(' · ');
}

// Собирает финальные позитивный/негативный промпт из того, что реально
// введено пользователем в textarea, плюс скрытые вклады активного
// стиля и активных категорий -- ничего из этого не подставляется в
// сами textarea, пользователь видит только свой текст.
function computeFinalPrompt() {
  const posExtra = [];
  const negExtra = [];

  for (const lora of state.config.always_loras || []) {
    if (lora.trigger_words) posExtra.push(lora.trigger_words);
  }

  if (state.activeTileId) {
    const style = (state.config.styles || []).find((s) => s.id === state.activeTileId);
    if (style) {
      if (style.prompt_text) {
        (style.target === 'negative' ? negExtra : posExtra).push(style.prompt_text);
      }
      if (style.trigger_words) posExtra.push(style.trigger_words);
    }
  }

  for (const [boxId, optionId] of state.activeBoxSelections) {
    const box = findNode(state.config.boxes, boxId);
    const option = box && box.options.find((o) => o.id === optionId);
    if (!option) continue;
    if (option.positive_text) posExtra.push(option.positive_text);
    if (option.negative_text) negExtra.push(option.negative_text);
  }

  const basePositive = el('positivePrompt').value.trim();
  const baseNegative = el('negativePrompt').value.trim();

  return {
    positive: [basePositive, ...posExtra].filter(Boolean).join(', '),
    negative: [baseNegative, ...negExtra].filter(Boolean).join(', '),
  };
}

function effectiveMaxLoras() {
  // Всегда-LoRA (дев-режим, вкладка "Всегда LoRA") занимают часть
  // общего лимита безусловно -- то, что остаётся для выбора в
  // "Стилях"/"Категориях", уменьшается на их количество.
  const always = (state.config.always_loras || []).length;
  return Math.max(0, state.config.generation.max_loras - always);
}

function openNegative() {
  el('negativeBody').hidden = false;
  el('negativeToggle').classList.add('open');
}

// ---------------------------------------------------------------- wiring

function wireStaticHandlers() {
  el('negativeToggle').addEventListener('click', () => {
    const hidden = el('negativeBody').hidden;
    el('negativeBody').hidden = !hidden;
    el('negativeToggle').classList.toggle('open', hidden);
  });

  el('generateBtn').addEventListener('click', onGenerate);
  el('freeMemoryBtn').addEventListener('click', onFreeMemory);

  el('lightboxClose').addEventListener('click', closeLightbox);
  const closeOnBackdrop = (e) => {
    if (lightboxDrag.moved) return; // только что тащили картинку -- не закрываем по клику
    if (e.target === e.currentTarget) closeLightbox(); // клик по фону, не по самой картинке
  };
  el('lightbox').addEventListener('click', closeOnBackdrop);
  el('lightboxViewport').addEventListener('click', closeOnBackdrop);
  el('lightboxZoom').addEventListener('input', (e) => setLightboxZoom(parseInt(e.target.value, 10)));
  el('lightboxViewport').addEventListener('wheel', (e) => {
    e.preventDefault();
    const slider = el('lightboxZoom');
    const step = (parseInt(slider.step, 10) || 5) * 2;
    const delta = e.deltaY < 0 ? step : -step;
    const next = Math.min(parseInt(slider.max, 10), Math.max(parseInt(slider.min, 10), parseInt(slider.value, 10) + delta));
    slider.value = next;
    setLightboxZoom(next);
  }, { passive: false });

  // Перетаскивание увеличенной картинки зажатой ЛКМ -- двигаем не саму
  // картинку, а scrollLeft/scrollTop прокручиваемого .lightbox__viewport
  // вокруг неё (overflow:auto там уже настроен под масштаб от zoom-ползунка).
  el('lightboxImg').addEventListener('mousedown', (e) => {
    if (e.button !== 0) return; // только левая кнопка мыши
    e.preventDefault();
    const viewport = el('lightboxViewport');
    lightboxDrag.active = true;
    lightboxDrag.moved = false;
    lightboxDrag.startX = e.clientX;
    lightboxDrag.startY = e.clientY;
    lightboxDrag.startScrollLeft = viewport.scrollLeft;
    lightboxDrag.startScrollTop = viewport.scrollTop;
    el('lightboxImg').classList.add('lightbox__img--grabbing');
  });
  document.addEventListener('mousemove', (e) => {
    if (!lightboxDrag.active) return;
    const dx = e.clientX - lightboxDrag.startX;
    const dy = e.clientY - lightboxDrag.startY;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) lightboxDrag.moved = true;
    const viewport = el('lightboxViewport');
    viewport.scrollLeft = lightboxDrag.startScrollLeft - dx;
    viewport.scrollTop = lightboxDrag.startScrollTop - dy;
  });
  document.addEventListener('mouseup', () => {
    if (!lightboxDrag.active) return;
    lightboxDrag.active = false;
    el('lightboxImg').classList.remove('lightbox__img--grabbing');
  });

  el('lightboxPrev').addEventListener('click', () => navigateLightbox(-1));
  el('lightboxNext').addEventListener('click', () => navigateLightbox(1));

  document.addEventListener('keydown', (e) => {
    if (el('lightbox').hidden) return;
    if (e.key === 'Escape') closeLightbox();
    else if (e.key === 'ArrowLeft') navigateLightbox(-1);
    else if (e.key === 'ArrowRight') navigateLightbox(1);
  });

  el('devToggle').addEventListener('click', () => openDevDrawer());
  el('devClose').addEventListener('click', () => closeDevDrawer());
  document.querySelectorAll('.devtab').forEach((tab) => {
    tab.addEventListener('click', () => switchDevTab(tab.dataset.tab));
  });

  el('styleForm').addEventListener('submit', onAddStyle);
  el('styleFormCancel').addEventListener('click', cancelEditStyle);
  el('newStyleImage').addEventListener('change', onStyleImageSelected);
  el('nodeForm').addEventListener('submit', onAddNode);
  el('nodeFormCancel').addEventListener('click', cancelEditNode);
  el('optionForm').addEventListener('submit', onAddOption);
  el('optionFormCancel').addEventListener('click', cancelEditOption);
  el('alwaysLoraForm').addEventListener('submit', onAddAlwaysLora);
  el('alwaysLoraFormCancel').addEventListener('click', cancelEditAlwaysLora);
  el('connectionForm').addEventListener('submit', onSaveConnection);

  el('startComfyBtn').addEventListener('click', onStartComfy);
}

// ---------------------------------------------------------------- comfy status

async function refreshComfyStatus() {
  try {
    const res = await fetch('/api/comfyui/status');
    const data = await res.json();
    const dot = el('statusDot');
    const wasAlive = dot.classList.contains('alive');
    dot.className = 'status-dot ' + (data.alive ? 'alive' : 'dead');
    el('statusText').textContent = data.alive ? t('ComfyUI на связи') : t('ComfyUI недоступен');
    el('startComfyBtn').hidden = data.alive;
    // Если страница открылась до того, как ComfyUI поднялся, список
    // aspect_ratio при первой загрузке ушёл в офлайн-фолбэк -- как
    // только ComfyUI появляется на связи, подтягиваем живой список
    // без необходимости вручную обновлять страницу.
    if (data.alive && !wasAlive) {
      renderAspectRatios();
    }
  } catch {
    el('statusDot').className = 'status-dot dead';
    el('statusText').textContent = t('нет связи с бэкендом');
  }
}

async function onStartComfy() {
  el('startComfyBtn').disabled = true;
  try {
    const res = await fetch('/api/comfyui/start', { method: 'POST' });
    if (!res.ok) {
      const err = await res.json();
      showError(err.detail || t('Не удалось запустить ComfyUI'));
    }
  } finally {
    el('startComfyBtn').disabled = false;
    setTimeout(refreshComfyStatus, 2000);
  }
}

async function onFreeMemory() {
  const btn = el('freeMemoryBtn');
  btn.disabled = true;
  clearError();
  try {
    const res = await fetch('/api/comfyui/free-memory', { method: 'POST' });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || t('Не удалось выгрузить модели'));
    }
    showInfo(t('Модели выгружены из памяти'));
  } catch (e) {
    showError(e.message);
  } finally {
    btn.disabled = false;
  }
}

// ---------------------------------------------------------------- generation

function showError(msg) {
  const line = el('errorLine');
  line.textContent = msg;
  line.hidden = false;
  el('infoLine').hidden = true;
}
function clearError() {
  el('errorLine').hidden = true;
}
function showInfo(msg) {
  const line = el('infoLine');
  line.textContent = msg;
  line.hidden = false;
  el('errorLine').hidden = true;
  setTimeout(() => { line.hidden = true; }, 4000);
}

function collectRequest() {
  // Всегда-LoRA идут первыми и безусловно -- независимо от того, что
  // выбрано в "Стилях"/"Категориях" (см. state.selectedLoras ниже).
  const alwaysLoras = (state.config.always_loras || []).map((l) => ({
    file: l.lora_file,
    strength: l.lora_strength,
  }));
  const selectedLoras = Array.from(state.selectedLoras.values()).map((l) => ({
    file: l.file,
    strength: l.strength,
  }));
  const loras = [...alwaysLoras, ...selectedLoras];
  const { positive, negative } = computeFinalPrompt();
  return {
    positive_prompt: positive,
    negative_prompt: negative,
    aspect_ratio: el('aspectRatio').value,
    megapixels: parseFloat(el('megapixels').value),
    steps: parseInt(el('steps').value, 10),
    batch_size: parseInt(el('batchSize').value, 10),
    cfg: parseFloat(el('cfg').value),
    sampler_name: el('samplerName').value,
    seed: el('seed').value ? parseInt(el('seed').value, 10) : null,
    randomize_seed: el('randomizeSeed').checked,
    loras,
  };
}

async function onGenerate() {
  clearError();
  const payload = collectRequest();
  if (!payload.positive_prompt.trim()) {
    showError(t('Промпт не может быть пустым'));
    return;
  }

  // Кнопка НЕ блокируется -- можно нажимать сколько угодно раз подряд,
  // каждое нажатие ставит своё задание в очередь ComfyUI и получает
  // свою собственную плашку с независимым статусом (см. pollGeneration).
  state.activeGenerations = (state.activeGenerations || 0) + 1;
  updateGenerateBtnLabel();

  const pendingPlate = addPendingPlate();

  try {
    const res = await fetch('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || t('Ошибка генерации'));
    }
    const data = await res.json();
    pollGeneration(data.prompt_id, pendingPlate);
  } catch (e) {
    showError(e.message);
    pendingPlate.remove();
    finishGeneration();
  }
}

function finishGeneration() {
  state.activeGenerations = Math.max(0, (state.activeGenerations || 0) - 1);
  updateGenerateBtnLabel();
}

function updateGenerateBtnLabel() {
  const n = state.activeGenerations || 0;
  el('generateBtnLabel').textContent = n > 0 ? t('Сгенерировать ещё (в работе: {n})', {n}) : t('Сгенерировать');
}

function addPendingPlate() {
  el('galleryEmpty').hidden = true;
  const plate = document.createElement('div');
  plate.className = 'plate plate--pending';
  plate.innerHTML = '<div class="tray-ripple"></div><span>' + t('генерируется…') + '</span>';
  el('galleryGrid').prepend(plate);
  return plate;
}

async function pollGeneration(promptId, pendingPlate) {
  try {
    const res = await fetch(`/api/generate/${promptId}/status`);
    const data = await res.json();

    if (data.state === 'running') {
      pendingPlate.querySelector('span').textContent = t('генерируется…');
    } else if (data.state === 'pending') {
      pendingPlate.querySelector('span').textContent = t('в очереди (#{n})', {n: data.queue_position});
    } else if (data.state === 'done') {
      // Готовые изображения встают ровно туда, где была ЭТА плашка
      // ожидания, а не наверх всей галереи -- иначе завершение одной
      // генерации визуально расталкивало плашки других, ещё не
      // готовых, генераций, стоящих в очереди (см. баг).
      for (const img of data.images) {
        insertPlateBefore(pendingPlate, img.url);
      }
      pendingPlate.remove();
      finishGeneration();
      return;
    } else if (data.state === 'error') {
      pendingPlate.remove();
      showError(t('ComfyUI сообщил об ошибке выполнения'));
      finishGeneration();
      return;
    }
  } catch (e) {
    pendingPlate.remove();
    showError(t('Потеряна связь с бэкендом во время генерации'));
    finishGeneration();
    return;
  }
  setTimeout(() => pollGeneration(promptId, pendingPlate), 1200);
}

function insertPlateBefore(referenceNode, url) {
  const plate = document.createElement('div');
  plate.className = 'plate';
  const img = document.createElement('img');
  img.src = url;
  img.alt = 'generated';
  plate.appendChild(img);
  plate.addEventListener('click', () => openLightbox(url));
  referenceNode.parentNode.insertBefore(plate, referenceNode);
  return plate;
}

// ---------------------------------------------------------------- лайтбокс (просмотр в исходном разрешении)

function openLightbox(url) {
  const img = el('lightboxImg');
  // Сбрасываем инлайн-размеры от предыдущей картинки -- иначе на
  // мгновение (до onload) новая картинка отрисуется с размерами
  // старой. max-width/max-height:100% из CSS подхватывают эту паузу.
  img.style.width = '';
  img.style.height = '';
  img.style.maxWidth = '';
  img.style.maxHeight = '';
  el('lightboxZoom').value = 100;
  el('lightboxZoomValue').textContent = '100%';
  img.onload = () => setLightboxZoom(100);
  img.src = url;
  el('lightbox').hidden = false;
  el('lightboxViewport').scrollTo(0, 0);
  lightboxDrag.active = false;
  lightboxDrag.moved = false;
  updateLightboxNavState();
}

function closeLightbox() {
  el('lightbox').hidden = true;
  el('lightboxImg').src = '';
}

// "Вписанный" размер картинки при zoom=100% -- те же пропорции, что
// даёт object-fit: contain, но посчитанные явно, чтобы дальше можно
// было честно умножить их на процент увеличения.
function fitLightboxBaseSize() {
  const img = el('lightboxImg');
  const viewport = el('lightboxViewport');
  if (!img.naturalWidth || !img.naturalHeight || !viewport.clientWidth || !viewport.clientHeight) {
    return null; // картинка ещё не загрузилась -- пересчитается в onload
  }
  const scale = Math.min(viewport.clientWidth / img.naturalWidth, viewport.clientHeight / img.naturalHeight);
  // Math.floor -- защита от появления микро-скролла на доли пикселя
  // при 100% из-за погрешности плавающей точки в самом умножении.
  return { width: Math.floor(img.naturalWidth * scale), height: Math.floor(img.naturalHeight * scale) };
}

function setLightboxZoom(percent) {
  el('lightboxZoomValue').textContent = `${percent}%`;
  const img = el('lightboxImg');
  const viewport = el('lightboxViewport');
  const base = fitLightboxBaseSize();
  if (!base) return;
  // Реальные пиксельные width/height вместо transform: scale() -- см.
  // комментарий в CSS про то, почему transform ненадёжен для скролла.
  // max-width/max-height снимаем явно: иначе они из CSS продолжат
  // подрезать инлайновый width сверху, и зум выше 100% не сработает.
  img.style.maxWidth = 'none';
  img.style.maxHeight = 'none';
  const newWidth = (base.width * percent) / 100;
  const newHeight = (base.height * percent) / 100;
  img.style.width = `${newWidth}px`;
  img.style.height = `${newHeight}px`;
  // Как только картинка вырастает больше видимой области, margin: auto
  // у неё уже нет свободного места для центрирования и она прижимается
  // к левому верхнему углу прокручиваемой области -- а скролл по
  // умолчанию стоит на (0,0), то есть показывает как раз этот угол.
  // Центрируем прокрутку явно на середине картинки при каждом
  // изменении масштаба (отрицательные значения браузер сам обнулит,
  // когда картинка ещё меньше вьюпорта -- доп. проверка не нужна).
  viewport.scrollLeft = (newWidth - viewport.clientWidth) / 2;
  viewport.scrollTop = (newHeight - viewport.clientHeight) / 2;
}

// Список URL всех ГОТОВЫХ изображений в текущем порядке DOM (плашки
// "в процессе" не в счёт, листать по ним нечего) -- запрашивается
// каждый раз заново, а не кэшируется, чтобы навигация всегда отражала
// актуальное состояние галереи, даже если пока лайтбокс был открыт
// подоспели новые изображения.
function getGalleryImageUrls() {
  return Array.from(document.querySelectorAll('#galleryGrid .plate:not(.plate--pending) img')).map((img) => img.getAttribute('src'));
}

function updateLightboxNavState() {
  const urls = getGalleryImageUrls();
  const multiple = urls.length > 1;
  el('lightboxPrev').disabled = !multiple;
  el('lightboxNext').disabled = !multiple;
}

function navigateLightbox(direction) {
  const urls = getGalleryImageUrls();
  if (urls.length < 2) return;
  const currentUrl = el('lightboxImg').getAttribute('src');
  const idx = urls.indexOf(currentUrl);
  if (idx === -1) return;
  const nextIdx = (idx + direction + urls.length) % urls.length; // с зацикливанием на концах
  openLightbox(urls[nextIdx]);
}

// ================================================================
// Дев-режим
// ================================================================

function openDevDrawer() {
  if (!state.devMode) return;
  el('devDrawer').hidden = false;
  renderDevLists();
  loadAvailableLoraFiles();
  el('connBatPath').value = state.config.comfyui.launch_bat_path || '';
  el('connLoraFolder').value = state.config.comfyui.lora_folder || '';
}
function closeDevDrawer() {
  el('devDrawer').hidden = true;
  cancelEditStyle();
  cancelEditNode();
  cancelEditOption();
  cancelEditAlwaysLora();
}

function switchDevTab(tab) {
  document.querySelectorAll('.devtab').forEach((t) => t.classList.toggle('active', t.dataset.tab === tab));
  document.querySelectorAll('.devpane').forEach((p) => {
    p.hidden = p.id !== `devPane-${tab}`;
  });
}

async function loadAvailableLoraFiles() {
  const datalist = el('loraFileOptions');
  const datalist2 = el('loraFileOptionsCategory');
  const datalist3 = el('loraFileOptionsAlways');
  const hint = el('loraFolderHint');
  datalist.innerHTML = '';
  datalist2.innerHTML = '';
  datalist3.innerHTML = '';
  hint.textContent = t('запрашиваю список у ComfyUI…');
  try {
    const res = await fetch('/api/available-loras');
    const data = await res.json();
    for (const f of data.files) {
      const opt = document.createElement('option');
      opt.value = f;
      datalist.appendChild(opt);
      datalist2.appendChild(opt.cloneNode(true));
      datalist3.appendChild(opt.cloneNode(true));
    }
    if (data.source === 'comfyui') {
      hint.textContent = t('от ComfyUI: {n} файлов LoRA', {n: data.files.length});
    } else if (!data.folder) {
      hint.textContent = t('ComfyUI недоступен и папка LoRA не задана — укажите её ниже, либо впишите путь к файлу вручную');
    } else if (!data.files.length) {
      hint.textContent = t('ComfyUI недоступен; в папке {folder} файлов LoRA не найдено', {folder: data.folder});
    } else {
      hint.textContent = t('ComfyUI недоступен, найдено сканированием папки: {n} ({folder})', {n: data.files.length, folder: data.folder});
    }
  } catch {
    hint.textContent = t('не удалось получить список — проверьте бэкенд');
  }
}

function renderDevLists() {
  renderStyleDevList();
  renderBoxDevList();
  renderAlwaysLoraDevList();
}

function renderStyleDevList() {
  const styleList = el('styleList');
  styleList.innerHTML = '';
  state.config.styles.forEach((style, idx) => {
    const item = document.createElement('div');
    item.className = 'devlist__item';
    const details = [];
    if (style.lora_file) details.push(`LoRA: ${style.lora_file} (${style.lora_strength})`);
    if (style.prompt_text) details.push(t(style.target === 'negative' ? 'негатив: {text}' : 'промпт: {text}', {text: style.prompt_text}));
    if (style.trigger_words) details.push(t('триггер: {text}', {text: style.trigger_words}));
    item.innerHTML = `<div>${style.label}<small>${details.join(' — ') || '—'}</small></div>`;

    const actions = document.createElement('div');
    actions.className = 'devlist__item-actions';
    const editBtn = document.createElement('button');
    editBtn.className = 'devlist__edit';
    editBtn.textContent = '✎';
    editBtn.title = t('Редактировать');
    editBtn.addEventListener('click', () => startEditStyle(idx));
    actions.appendChild(editBtn);
    const removeBtn = document.createElement('button');
    removeBtn.className = 'devlist__remove';
    removeBtn.textContent = '✕';
    removeBtn.title = t('Удалить');
    removeBtn.addEventListener('click', () => removeStyle(idx));
    actions.appendChild(removeBtn);
    item.appendChild(actions);

    styleList.appendChild(item);
  });
}

function renderBoxDevList() {
  const boxList = el('boxList');
  boxList.innerHTML = '';
  (state.config.boxes || []).forEach((node) => {
    boxList.appendChild(buildDevNodeElement(node));
  });

  // "Куда добавить" при создании нового узла -- только группы (только
  // они могут быть родителем), с отступом по глубине вложенности.
  const parentSelect = el('newNodeParent');
  const currentParentValue = parentSelect.value;
  parentSelect.innerHTML = '<option value="">' + t('— верхний уровень —') + '</option>';
  flattenGroups(state.config.boxes).forEach(({ id, label, depth }) => {
    const opt = document.createElement('option');
    opt.value = id;
    opt.textContent = '—'.repeat(depth) + ' ' + label;
    parentSelect.appendChild(opt);
  });
  if (Array.from(parentSelect.options).some((o) => o.value === currentParentValue)) {
    parentSelect.value = currentParentValue;
  }

  // "В какую категорию" для варианта -- только листовые категории, с
  // отступом по глубине, из любой точки дерева.
  const categorySelect = el('optionCategorySelect');
  const currentCategoryValue = categorySelect.value;
  categorySelect.innerHTML = '';
  flattenCategories(state.config.boxes).forEach(({ id, label, depth }) => {
    const opt = document.createElement('option');
    opt.value = id;
    opt.textContent = '—'.repeat(depth) + ' ' + label;
    categorySelect.appendChild(opt);
  });
  if (Array.from(categorySelect.options).some((o) => o.value === currentCategoryValue)) {
    categorySelect.value = currentCategoryValue;
  }
  // Пока редактируется вариант, категорию менять нельзя (см.
  // startEditOption/cancelEditOption) -- переприменяем блокировку
  // после каждой перерисовки списка, т.к. innerHTML пересоздаёт select.
  categorySelect.disabled = state.editingOption !== null;
}

// Рекурсивно строит элемент списка дев-режима для одного узла дерева --
// категория показывает свои варианты, группа показывает вложенные
// узлы (снова через этот же вызов, отсюда и произвольная глубина).
function buildDevNodeElement(node) {
  const boxDiv = document.createElement('div');
  boxDiv.className = 'devlist__box';

  const expanded = state.expandedBoxIds.has(node.id);
  const isGroup = node.type === 'group';

  const head = document.createElement('div');
  head.className = 'devlist__box-head';

  const toggleBtn = document.createElement('button');
  toggleBtn.className = 'devlist__box-toggle';
  toggleBtn.type = 'button';
  toggleBtn.textContent = expanded ? '▾' : '▸';
  toggleBtn.title = expanded ? t('Свернуть') : t('Развернуть');

  const headLabel = document.createElement('span');
  headLabel.className = 'devlist__box-headlabel';
  headLabel.textContent = isGroup
    ? `📁 ${node.label} (${node.children.length})`
    : `${node.label} (${node.options.length})`;

  const headMain = document.createElement('div');
  headMain.className = 'devlist__box-headmain';
  headMain.appendChild(toggleBtn);
  headMain.appendChild(headLabel);
  headMain.addEventListener('click', () => toggleBoxExpanded(node.id));
  head.appendChild(headMain);

  const headActions = document.createElement('div');
  headActions.className = 'devlist__item-actions';
  const editBtn = document.createElement('button');
  editBtn.className = 'devlist__edit';
  editBtn.textContent = '✎';
  editBtn.title = isGroup ? t('Переименовать группу') : t('Переименовать категорию');
  editBtn.addEventListener('click', (e) => { e.stopPropagation(); startEditNode(node.id); });
  headActions.appendChild(editBtn);
  const removeBtn = document.createElement('button');
  removeBtn.className = 'devlist__remove';
  removeBtn.textContent = isGroup ? t('✕ группа') : t('✕ категория');
  removeBtn.addEventListener('click', (e) => { e.stopPropagation(); removeNode(node.id); });
  headActions.appendChild(removeBtn);
  head.appendChild(headActions);
  boxDiv.appendChild(head);

  const bodyWrap = document.createElement('div');
  bodyWrap.className = 'devlist__box-options';
  bodyWrap.hidden = !expanded;

  if (isGroup) {
    node.children.forEach((child) => {
      bodyWrap.appendChild(buildDevNodeElement(child));
    });
  } else {
    node.options.forEach((option, optIdx) => {
      const item = document.createElement('div');
      item.className = 'devlist__item';
      const details = [];
      if (option.positive_text) details.push(t('промпт: {text}', {text: option.positive_text}));
      if (option.negative_text) details.push(t('негатив: {text}', {text: option.negative_text}));
      if (option.lora_file) details.push(`LoRA: ${option.lora_file} (${option.lora_strength})`);
      item.innerHTML = `<div>${option.label}<small>${details.join(' — ') || '—'}</small></div>`;

      const actions = document.createElement('div');
      actions.className = 'devlist__item-actions';
      const editOptBtn = document.createElement('button');
      editOptBtn.className = 'devlist__edit';
      editOptBtn.textContent = '✎';
      editOptBtn.title = t('Редактировать');
      editOptBtn.addEventListener('click', () => startEditOption(node.id, optIdx));
      actions.appendChild(editOptBtn);
      const removeOptBtn = document.createElement('button');
      removeOptBtn.className = 'devlist__remove';
      removeOptBtn.textContent = '✕';
      removeOptBtn.title = t('Удалить');
      removeOptBtn.addEventListener('click', () => removeOption(node.id, optIdx));
      actions.appendChild(removeOptBtn);
      item.appendChild(actions);

      bodyWrap.appendChild(item);
    });
  }

  boxDiv.appendChild(bodyWrap);
  return boxDiv;
}

function toggleBoxExpanded(nodeId) {
  if (state.expandedBoxIds.has(nodeId)) {
    state.expandedBoxIds.delete(nodeId);
  } else {
    state.expandedBoxIds.add(nodeId);
  }
  renderBoxDevList();
}

async function persistConfig() {
  await fetch('/api/config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(state.config),
  });
  renderStyleTileGrid();
  renderStyleBoxes();
  updateActiveSummary();
}

function slugify(text) {
  return text.toLowerCase().trim().replace(/[^a-z0-9а-яё]+/gi, '-').replace(/^-+|-+$/g, '') || `item-${Date.now()}`;
}

// ---------------------------------------------------------------- dev: Стили (плоские)

async function onStyleImageSelected(e) {
  const file = e.target.files[0];
  if (!file) return;
  const form = new FormData();
  form.append('file', file);
  const preview = el('newStyleImagePreview');
  try {
    const res = await fetch('/api/upload-image', { method: 'POST', body: form });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || t('Не удалось загрузить картинку'));
    }
    const data = await res.json();
    state.pendingImageUrl = data.url;
    preview.src = data.url;
    preview.hidden = false;
  } catch (err) {
    showError(err.message);
  }
}

function startEditStyle(idx) {
  const style = state.config.styles[idx];
  if (!style) return;
  state.editingStyleIndex = idx;

  el('newStyleLabel').value = style.label;
  el('newStyleTarget').value = style.target || 'positive';
  el('newStyleText').value = style.prompt_text || '';
  el('newStyleLoraFile').value = style.lora_file || '';
  el('newStyleStrength').value = style.lora_strength ?? 0.8;
  el('newStyleTrigger').value = style.trigger_words || '';

  state.pendingImageUrl = style.image || '';
  const preview = el('newStyleImagePreview');
  if (style.image) {
    preview.src = style.image;
    preview.hidden = false;
  } else {
    preview.hidden = true;
  }

  el('styleFormSubmit').textContent = t('Сохранить изменения');
  el('styleFormCancel').hidden = false;
  el('styleForm').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function cancelEditStyle() {
  state.editingStyleIndex = null;
  state.pendingImageUrl = '';
  el('styleForm').reset();
  el('newStyleImagePreview').hidden = true;
  el('newStyleStrength').value = '0.8';
  el('styleFormSubmit').textContent = t('Добавить стиль');
  el('styleFormCancel').hidden = true;
}

async function onAddStyle(e) {
  e.preventDefault();
  const label = el('newStyleLabel').value.trim();
  if (!label) return;
  const styleData = {
    label,
    image: state.pendingImageUrl,
    target: el('newStyleTarget').value,
    prompt_text: el('newStyleText').value.trim(),
    lora_file: el('newStyleLoraFile').value.trim(),
    lora_strength: parseFloat(el('newStyleStrength').value) || 0,
    trigger_words: el('newStyleTrigger').value.trim(),
  };

  if (state.editingStyleIndex !== null) {
    const existing = state.config.styles[state.editingStyleIndex];
    const wasActive = existing && existing.id === state.activeTileId;
    styleData.id = existing.id; // id (и его связь с активным выбором/LoRA-ключом) не меняется при редактировании
    state.config.styles[state.editingStyleIndex] = styleData;
    if (wasActive && state.selectedLoras.has(`tile:${styleData.id}`)) {
      // сила LoRA могла измениться при редактировании -- обновляем уже выбранную
      state.selectedLoras.set(`tile:${styleData.id}`, {
        file: styleData.lora_file,
        strength: styleData.lora_strength,
      });
    }
  } else {
    styleData.id = slugify(label);
    state.config.styles.push(styleData);
  }

  await persistConfig();
  cancelEditStyle();
  renderStyleDevList();
}

async function removeStyle(idx) {
  const style = state.config.styles[idx];
  if (style && style.id === state.activeTileId) {
    state.activeTileId = null;
    state.selectedLoras.delete(`tile:${style.id}`);
  }
  if (state.editingStyleIndex === idx) cancelEditStyle();
  state.config.styles.splice(idx, 1);
  await persistConfig();
  renderStyleDevList();
}

// ---------------------------------------------------------------- dev: Категории (дерево)

function startEditNode(id) {
  const node = findNode(state.config.boxes, id);
  if (!node) return;
  state.editingNodeId = id;

  el('newNodeType').value = node.type;
  el('newNodeType').disabled = true; // тип узла не меняется при редактировании
  el('newNodeParent').disabled = true; // как и родитель -- перенос между категориями не поддержан (см. optionForm)
  el('newNodeLabel').value = node.label;

  el('nodeFormSubmit').textContent = t('Сохранить название');
  el('nodeFormCancel').hidden = false;
  el('nodeForm').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function cancelEditNode() {
  state.editingNodeId = null;
  el('nodeForm').reset();
  el('newNodeType').disabled = false;
  el('newNodeParent').disabled = false;
  el('nodeFormSubmit').textContent = t('Добавить');
  el('nodeFormCancel').hidden = true;
}

async function onAddNode(e) {
  e.preventDefault();
  const label = el('newNodeLabel').value.trim();
  if (!label) return;

  if (state.editingNodeId !== null) {
    const node = findNode(state.config.boxes, state.editingNodeId);
    if (node) node.label = label;
    // id узла (и связанные с ним ключи активного выбора/LoRA)
    // сознательно не меняется при переименовании -- иначе текущий
    // активный выбор в этой категории "отвязался" бы молча.
  } else {
    const type = el('newNodeType').value;
    const parentId = el('newNodeParent').value;
    const newNode = type === 'group'
      ? { id: uniqueNodeId(label), label, type: 'group', children: [] }
      : { id: uniqueNodeId(label), label, type: 'category', options: [] };
    getChildrenArray(parentId).push(newNode);
  }

  await persistConfig();
  cancelEditNode();
  renderBoxDevList();
}

async function removeNode(id) {
  const found = findNodeWithParent(state.config.boxes, id);
  if (!found) return;
  const { node, array, index } = found;

  // Чистим активный выбор/LoRA для ВСЕХ категорий внутри удаляемого
  // узла -- он мог быть целой группой с вложенными категориями, а не
  // только одной категорией напрямую.
  const categoryIds = collectDescendantCategoryIds(node);
  for (const catId of categoryIds) {
    state.activeBoxSelections.delete(catId);
    state.selectedLoras.delete(`box:${catId}`);
  }
  state.expandedGroupIds.delete(id);
  state.expandedBoxIds.delete(id);

  if (state.editingNodeId === id) cancelEditNode();
  if (state.editingOption && categoryIds.includes(state.editingOption.categoryId)) cancelEditOption();

  array.splice(index, 1);
  await persistConfig();
  renderBoxDevList();
}

function startEditOption(categoryId, optIdx) {
  const category = findNode(state.config.boxes, categoryId);
  const option = category && category.options[optIdx];
  if (!option) return;
  state.editingOption = { categoryId, optIdx };

  el('optionCategorySelect').value = categoryId;
  el('optionCategorySelect').disabled = true; // редактирование не переносит вариант в другую категорию
  el('newOptionLabel').value = option.label;
  el('newOptionPositive').value = option.positive_text || '';
  el('newOptionNegative').value = option.negative_text || '';
  el('newOptionLoraFile').value = option.lora_file || '';
  el('newOptionStrength').value = option.lora_strength ?? 0.8;

  el('optionFormSubmit').textContent = t('Сохранить изменения');
  el('optionFormCancel').hidden = false;
  el('optionForm').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function cancelEditOption() {
  state.editingOption = null;
  el('optionForm').reset();
  el('optionCategorySelect').disabled = false;
  el('newOptionStrength').value = '0.8';
  el('optionFormSubmit').textContent = t('Добавить вариант');
  el('optionFormCancel').hidden = true;
}

async function onAddOption(e) {
  e.preventDefault();
  const categoryId = el('optionCategorySelect').value;
  const category = findNode(state.config.boxes, categoryId);
  if (!category || category.type !== 'category') return;
  const label = el('newOptionLabel').value.trim();
  if (!label) return;

  const optionData = {
    label,
    positive_text: el('newOptionPositive').value.trim(),
    negative_text: el('newOptionNegative').value.trim(),
    lora_file: el('newOptionLoraFile').value.trim(),
    lora_strength: parseFloat(el('newOptionStrength').value) || 0,
  };

  if (state.editingOption !== null) {
    const editCategory = findNode(state.config.boxes, state.editingOption.categoryId);
    const existing = editCategory.options[state.editingOption.optIdx];
    const wasActive = state.activeBoxSelections.get(editCategory.id) === existing.id;
    optionData.id = existing.id; // id не меняется при редактировании -- как и у узлов дерева выше
    editCategory.options[state.editingOption.optIdx] = optionData;
    if (wasActive && state.selectedLoras.has(`box:${editCategory.id}`)) {
      state.selectedLoras.set(`box:${editCategory.id}`, {
        file: optionData.lora_file,
        strength: optionData.lora_strength,
      });
    }
  } else {
    // Уникальность варианта нужна только в пределах своей категории
    // (в отличие от id узлов дерева, которые ключуются глобально).
    let id = slugify(label);
    let i = 2;
    while (category.options.some((o) => o.id === id)) {
      id = `${slugify(label)}-${i}`;
      i++;
    }
    optionData.id = id;
    category.options.push(optionData);
    state.expandedBoxIds.add(categoryId); // сразу видно результат добавления
  }

  await persistConfig();
  cancelEditOption();
  renderBoxDevList();
}

async function removeOption(categoryId, optIdx) {
  const category = findNode(state.config.boxes, categoryId);
  const option = category && category.options[optIdx];
  if (category && option && state.activeBoxSelections.get(categoryId) === option.id) {
    state.activeBoxSelections.delete(categoryId);
    state.selectedLoras.delete(`box:${categoryId}`);
  }
  if (state.editingOption && state.editingOption.categoryId === categoryId && state.editingOption.optIdx === optIdx) {
    cancelEditOption();
  }
  category.options.splice(optIdx, 1);
  await persistConfig();
  renderBoxDevList();
}

// ---------------------------------------------------------------- dev: Всегда LoRA

function renderAlwaysLoraDevList() {
  const list = el('alwaysLoraList');
  list.innerHTML = '';
  (state.config.always_loras || []).forEach((lora, idx) => {
    const item = document.createElement('div');
    item.className = 'devlist__item';
    const details = [t('файл: {text}', {text: lora.lora_file}), t('сила: {text}', {text: lora.lora_strength})];
    if (lora.trigger_words) details.push(t('триггер: {text}', {text: lora.trigger_words}));
    item.innerHTML = `<div>${lora.label}<small>${details.join(' — ')}</small></div>`;

    const actions = document.createElement('div');
    actions.className = 'devlist__item-actions';
    const editBtn = document.createElement('button');
    editBtn.className = 'devlist__edit';
    editBtn.textContent = '✎';
    editBtn.title = t('Редактировать');
    editBtn.addEventListener('click', () => startEditAlwaysLora(idx));
    actions.appendChild(editBtn);
    const removeBtn = document.createElement('button');
    removeBtn.className = 'devlist__remove';
    removeBtn.textContent = '✕';
    removeBtn.title = t('Удалить');
    removeBtn.addEventListener('click', () => removeAlwaysLora(idx));
    actions.appendChild(removeBtn);
    item.appendChild(actions);

    list.appendChild(item);
  });
}

function startEditAlwaysLora(idx) {
  const lora = state.config.always_loras[idx];
  if (!lora) return;
  state.editingAlwaysLoraIndex = idx;

  el('newAlwaysLoraLabel').value = lora.label;
  el('newAlwaysLoraFile').value = lora.lora_file;
  el('newAlwaysLoraStrength').value = lora.lora_strength;
  el('newAlwaysLoraTrigger').value = lora.trigger_words || '';

  el('alwaysLoraFormSubmit').textContent = t('Сохранить изменения');
  el('alwaysLoraFormCancel').hidden = false;
  el('alwaysLoraForm').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function cancelEditAlwaysLora() {
  state.editingAlwaysLoraIndex = null;
  el('alwaysLoraForm').reset();
  el('newAlwaysLoraStrength').value = '0.8';
  el('alwaysLoraFormSubmit').textContent = t('Добавить');
  el('alwaysLoraFormCancel').hidden = true;
}

async function onAddAlwaysLora(e) {
  e.preventDefault();
  const label = el('newAlwaysLoraLabel').value.trim();
  const file = el('newAlwaysLoraFile').value.trim();
  if (!label || !file) return;

  const loraData = {
    label,
    lora_file: file,
    lora_strength: parseFloat(el('newAlwaysLoraStrength').value) || 0,
    trigger_words: el('newAlwaysLoraTrigger').value.trim(),
  };

  if (state.editingAlwaysLoraIndex !== null) {
    state.config.always_loras[state.editingAlwaysLoraIndex] = loraData;
  } else {
    state.config.always_loras = state.config.always_loras || [];
    state.config.always_loras.push(loraData);
  }

  await persistConfig();
  cancelEditAlwaysLora();
  renderAlwaysLoraDevList();
}

async function removeAlwaysLora(idx) {
  if (state.editingAlwaysLoraIndex === idx) cancelEditAlwaysLora();
  state.config.always_loras.splice(idx, 1);
  await persistConfig();
  renderAlwaysLoraDevList();
}

// ---------------------------------------------------------------- dev: Подключение

async function onSaveConnection(e) {
  e.preventDefault();
  // ИЗМЕНЕНО: host/port больше не редактируются здесь -- Studio
  // передаёт их сама при каждом запуске (см. backend/main.py,
  // _seed_comfy_target_from_env(), и launcher/core/imagine_process.py)
  // и любое ручное значение всё равно было бы перезаписано при
  // следующем запуске из Studio; редактируемое, но не имеющее эффекта
  // поле было просто источником путаницы.
  state.config.comfyui.launch_bat_path = el('connBatPath').value.trim();
  state.config.comfyui.lora_folder = el('connLoraFolder').value.trim();
  await persistConfig();
  refreshComfyStatus();
  loadAvailableLoraFiles();
  await renderAspectRatios();
}

init();
