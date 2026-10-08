// Imagine Pony — фронтенд. Ванильный JS, без сборки (как у Imagine).
//
// Три панели повторяют интерфейсы узлов расширения character_search_ui:
//   • «Персонажи»   — CharacterSearchUI (поиск по имени/тегу, случайный
//                     выбор, вкл/выкл отдельных тегов, LoRA персонажей);
//   • «Билдер»      — PromptBuilderNode (плашки категорий, рандом с
//                     включением/выключением категорий, вес сцены,
//                     пресеты и вкл/выкл тегов негатива);
//   • «LoRA»        — ручные слоты MultiLoraLoader.
// Вся бизнес-логика (сборка тегов, рандом, валидация) остаётся в
// расширении на стороне ComfyUI -- здесь только отображение, события и
// вызовы API (так же устроены web/*.js самого расширения).
//
// ВАЖНО: пути в fetch() ОТНОСИТЕЛЬНЫЕ (без ведущего "/") -- страница может
// открываться через reverse-proxy Remote, см. шапку imagine/static/js/app.js.

'use strict';

const LORA_SLOTS = 5;       // MultiLoraLoader.MAX_MANUAL
const CHAR_LIST_LIMIT = 200; // сколько строк списка персонажей рисуем за раз

const state = {
  config: null,
  loraFiles: [],
  loras: Array.from({ length: LORA_SLOTS }, () => ({ file: '', strength: 1 })),
  activeGenerations: 0,
  saved: {},          // то, что пришло из GET api/state при загрузке
  loaded: { chars: false, builder: false, loras: false, frame: false },
};

// Персонажи (CharacterSearchUI)
const C = {
  db: {},
  keys: [],
  selected: new Set(),
  excluded: new Set(),
  mode: 'name',        // 'name' | 'tag' | 'random'
  loraOnly: false,
  preview: null,       // ответ /character_search/preview
  seq: 0,
};

// Билдер (PromptBuilderNode)
const B = {
  cfg: null,
  state: {},           // тот же формат, что state_json узла
  excludedNeg: new Set(),
  cats: [],            // категории, доступные для рандома [{id,label}]
  randEnabled: null,   // null = все, иначе Set id
  open: new Set(),     // раскрытые секции (переживают перерисовку)
  summaries: [],       // [{el, text()}] -- подписи выбранного в шапках секций
  preview: null,
  seq: 0,
};

const el = (id) => document.getElementById(id);

const lightboxDrag = { active: false, moved: false, startX: 0, startY: 0, startScrollLeft: 0, startScrollTop: 0 };

// ---------------------------------------------------------------- утилиты

function h(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch { /* тело не JSON */ }
  if (!res.ok) throw new Error((data && data.detail) || `HTTP ${res.status}`);
  return data;
}

function postJson(path, body) {
  return api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

function debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

// navigator.clipboard недоступен вне безопасного контекста (страница по
// http://<ip-пк>:порт через Remote) -- запасной путь через execCommand.
async function copyText(btn, text) {
  if (!text) return;
  let ok = false;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      ok = true;
    }
  } catch { /* пробуем запасной путь */ }
  if (!ok) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { ok = document.execCommand('copy'); } catch { ok = false; }
    ta.remove();
  }
  const old = btn.textContent;
  btn.textContent = ok ? t('✓ скопировано') : t('не удалось');
  setTimeout(() => { btn.textContent = old; }, 1500);
}

// ---------------------------------------------------------------- сохранение состояния (на сервере)

const scheduleSave = debounce(saveUiState, 500);

function frameValues() {
  return {
    aspect_ratio: el('aspectRatio').value,
    megapixels: el('megapixels').value,
    batch_size: el('batchSize').value,
    steps_min: el('stepsMin').value,
    steps_max: el('stepsMax').value,
    cfg: el('cfg').value,
    sampler_name: el('samplerName').value,
    checkpoint: el('checkpoint').value,
    seed: el('seed').value,
    randomize_seed: el('randomizeSeed').checked,
  };
}

async function saveUiState() {
  // Пока соответствующая часть не загрузилась, её сохранённое значение не
  // перезаписываем пустышкой.
  const out = { ...state.saved };
  if (state.loaded.chars) {
    out.chars = {
      selected: [...C.selected],
      excluded: [...C.excluded],
      mode: C.mode,
      loraOnly: C.loraOnly,
      randTags: el('charRandTags').value,
      randCount: el('charRandCount').value,
    };
  }
  if (state.loaded.builder) out.builder = { state: syncBuilderMeta() };
  out.loras = state.loras;
  if (state.loaded.frame) out.frame = frameValues();
  state.saved = out;
  try {
    await api('api/state', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(out),
    });
  } catch (e) {
    console.warn('Не удалось сохранить состояние:', e);
  }
}

// ================================================================
// Персонажи — CharacterSearchUI
// ================================================================

function getEntry(key) {
  const e = C.db[key];
  if (!e) return { tags: '', lora: '', strength: 1 };
  if (typeof e === 'string') return { tags: e, lora: '', strength: 1 };
  return { tags: e.tags || '', lora: e.lora || '', strength: Number(e.strength ?? 1) };
}

function highlight(text, query) {
  const frag = document.createDocumentFragment();
  const idx = query ? text.toLowerCase().indexOf(query.toLowerCase()) : -1;
  if (idx === -1) {
    frag.appendChild(document.createTextNode(text));
    return frag;
  }
  frag.appendChild(document.createTextNode(text.slice(0, idx)));
  frag.appendChild(h('mark', null, text.slice(idx, idx + query.length)));
  frag.appendChild(document.createTextNode(text.slice(idx + query.length)));
  return frag;
}

// Стабильный цвет персонажа по его позиции среди выбранных -- чтобы теги
// разных персонажей в превью различались (в самом узле то же самое).
function charTagStyle(charKey) {
  const idx = Math.max(0, [...C.selected].indexOf(charKey));
  const hue = (idx * 67 + 205) % 360;
  return `background:hsla(${hue},55%,48%,0.18);border-color:hsla(${hue},60%,58%,0.75)`;
}

async function loadCharacters() {
  try {
    C.db = await api('api/characters/db');
    C.keys = Object.keys(C.db);
    state.loaded.chars = true;
    const saved = state.saved.chars || {};
    C.selected = new Set((saved.selected || []).filter((k) => C.db[k]));
    C.excluded = new Set(saved.excluded || []);
    C.loraOnly = !!saved.loraOnly;
    if (saved.randTags) el('charRandTags').value = saved.randTags;
    if (saved.randCount) el('charRandCount').value = saved.randCount;
    el('charLoraOnly').classList.toggle('on', C.loraOnly);
    setCharMode(saved.mode || 'name', true);
    renderCharChips();
    await refreshCharPreview();
  } catch (e) {
    el('charList').replaceChildren(h('div', 'charlist__empty', t('Не удалось загрузить базу персонажей:') + ' ' + e.message));
  }
}

function renderCharList() {
  const box = el('charList');
  if (C.mode === 'random') {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const q = el('charSearch').value.trim().toLowerCase();
  let matches = C.keys;
  if (q) {
    matches = C.mode === 'name'
      ? C.keys.filter((k) => k.toLowerCase().includes(q))
      : C.keys.filter((k) => getEntry(k).tags.toLowerCase().includes(q));
  }
  if (C.loraOnly) matches = matches.filter((k) => getEntry(k).lora);

  const scroll = box.scrollTop;
  const frag = document.createDocumentFragment();
  if (!matches.length) {
    frag.appendChild(h('div', 'charlist__empty', t('Ничего не найдено')));
  }
  for (const key of matches.slice(0, CHAR_LIST_LIMIT)) {
    const { lora, tags } = getEntry(key);
    const row = h('div', 'charrow' + (C.selected.has(key) ? ' selected' : ''));
    const top = h('div', 'charrow__top');
    top.appendChild(h('div', 'charrow__check', C.selected.has(key) ? '✓' : ''));
    const name = h('span', 'charrow__name');
    name.appendChild(C.mode === 'name' && q ? highlight(key, q) : document.createTextNode(key));
    top.appendChild(name);
    if (lora) top.appendChild(h('span', 'badge', 'LoRA'));
    row.appendChild(top);
    if (C.mode === 'tag' && q && tags) {
      const line = h('div', 'charrow__tags');
      line.appendChild(highlight(tags, q));
      row.appendChild(line);
    }
    row.addEventListener('click', () => toggleCharacter(key));
    frag.appendChild(row);
  }
  if (matches.length > CHAR_LIST_LIMIT) {
    frag.appendChild(h('div', 'charlist__more',
      t('показано {shown} из {total} — уточните поиск', { shown: CHAR_LIST_LIMIT, total: matches.length })));
  }
  box.replaceChildren(frag);
  box.scrollTop = scroll;
}

function renderCharChips() {
  const n = C.selected.size;
  el('charCounter').textContent = n ? t('{n} выбр.', { n }) : '';
  el('charClear').hidden = n === 0;
  el('charChipsWrap').hidden = n === 0;
  const box = el('charChips');
  box.replaceChildren();
  for (const key of C.selected) {
    const chip = h('div', 'chip' + (getEntry(key).lora ? ' chip--lora' : ''));
    chip.appendChild(h('span', null, key));
    const x = h('span', 'chip__x', '×');
    x.addEventListener('click', () => toggleCharacter(key));
    chip.appendChild(x);
    box.appendChild(chip);
  }
}

function toggleCharacter(key) {
  if (C.selected.has(key)) C.selected.delete(key);
  else C.selected.add(key);
  onCharsChanged();
}

function onCharsChanged() {
  renderCharList();
  renderCharChips();
  scheduleSave();
  refreshCharPreview();
}

function setCharMode(mode, silent) {
  C.mode = mode;
  document.querySelectorAll('#charModes button').forEach((b) => b.classList.toggle('active', b.dataset.mode === mode));
  const search = el('charSearch');
  search.placeholder = mode === 'tag' ? t('Поиск по тегу…') : t('Поиск по имени…');
  search.hidden = mode === 'random';
  el('charRandom').hidden = mode !== 'random';
  renderCharList();
  if (!silent) scheduleSave();
}

async function pickRandomCharacters() {
  const btn = el('charRandPick');
  const status = el('charRandStatus');
  const count = Math.max(1, Math.min(50, parseInt(el('charRandCount').value, 10) || 1));
  btn.disabled = true;
  status.textContent = t('выбираю…');
  try {
    const data = await postJson('api/characters/random', {
      tag_query: el('charRandTags').value.trim(),
      count,
      lora_only: C.loraOnly,
    });
    if (!data.selected || !data.selected.length) {
      status.textContent = t('пул пуст (0 персонажей)');
      return;
    }
    // Как в узле: случайный выбор ЗАМЕНЯЕТ текущий набор, а не дополняет.
    C.selected = new Set(data.selected);
    C.excluded.clear();
    status.textContent = t('выбрано {n} из {total} подходящих', { n: data.selected.length, total: data.total_pool });
    renderCharChips();
    scheduleSave();
    await refreshCharPreview();
  } catch (e) {
    status.textContent = t('ошибка запроса');
    console.error(e);
  } finally {
    btn.disabled = false;
  }
}

async function refreshCharPreview() {
  const seq = ++C.seq;
  if (!C.selected.size) {
    C.preview = null;
    renderCharPreview();
    renderLoraEffective();
    refreshBuilderPreview();
    return;
  }
  try {
    const data = await postJson('api/characters/preview', { selected: [...C.selected], excluded: [...C.excluded] });
    if (seq !== C.seq) return;
    C.preview = data;
  } catch (e) {
    if (seq !== C.seq) return;
    console.error('character preview:', e);
    C.preview = null;
  }
  renderCharPreview();
  renderLoraEffective();
  refreshBuilderPreview();
}

function renderCharPreview() {
  const wrap = el('charTagsWrap');
  const data = C.preview;
  if (!data || !(data.all_tags || []).length) {
    wrap.hidden = true;
    return;
  }
  wrap.hidden = false;
  const box = el('charTags');
  box.replaceChildren();
  for (const tag of data.all_tags) {
    const isEx = C.excluded.has(tag);
    const pill = h('span', 'tag' + (isEx ? ' excluded' : ''), tag);
    if (!isEx) pill.style.cssText = charTagStyle((data.tag_char_map || {})[tag] || '');
    pill.title = isEx ? t('{tag} — убран (клик, чтобы вернуть)', { tag }) : t('{tag} — клик, чтобы убрать', { tag });
    pill.addEventListener('click', () => {
      if (C.excluded.has(tag)) C.excluded.delete(tag);
      else C.excluded.add(tag);
      scheduleSave();
      refreshCharPreview();
    });
    box.appendChild(pill);
  }
  const n = data.excluded_count || 0;
  el('charExcludedCount').hidden = n === 0;
  el('charExcludedCount').textContent = t('−{n} убрано', { n });
  el('charRestore').hidden = n === 0;
  el('charTagsHint').hidden = n > 0;
}

function wireCharacters() {
  document.querySelectorAll('#charModes button').forEach((b) => b.addEventListener('click', () => setCharMode(b.dataset.mode)));
  el('charSearch').addEventListener('input', renderCharList);
  el('charLoraOnly').addEventListener('click', () => {
    C.loraOnly = !C.loraOnly;
    el('charLoraOnly').classList.toggle('on', C.loraOnly);
    renderCharList();
    scheduleSave();
  });
  el('charRandPick').addEventListener('click', pickRandomCharacters);
  el('charRandTags').addEventListener('input', scheduleSave);
  el('charRandCount').addEventListener('input', scheduleSave);
  el('charRestore').addEventListener('click', () => {
    C.excluded.clear();
    scheduleSave();
    refreshCharPreview();
  });
  el('charCopy').addEventListener('click', (e) => copyText(e.currentTarget, C.preview ? C.preview.combined_tags : ''));
  el('charClear').addEventListener('click', () => {
    C.selected.clear();
    C.excluded.clear();
    el('charRandStatus').textContent = '';
    onCharsChanged();
  });
}

// ================================================================
// Билдер — PromptBuilderNode
// ================================================================

function defaultBuilderState() {
  const cfg = B.cfg || {};
  return {
    quality_prefix: (cfg.quality_prefix && cfg.quality_prefix.default) || '',
    source: (cfg.source && cfg.source.default) || '',
    negative_preset: cfg.negative_default || 'Standard',
  };
}

// Служебные ключи state (то же делает JS узла): какие категории участвуют
// в рандоме и какие теги негатива исключены.
function syncBuilderMeta() {
  B.state.__random_cats__ = B.randEnabled === null ? null : [...B.randEnabled];
  B.state.__excluded_negative__ = JSON.stringify([...B.excludedNeg]);
  return B.state;
}

function effectiveNegPreset() {
  return B.state.negative_preset || (B.cfg && B.cfg.negative_default) || 'Standard';
}

async function loadBuilder() {
  const sections = el('bSections');
  try {
    B.cfg = await api('api/builder/config');
    try {
      B.cats = (await api('api/builder/random-cats')).cats || [];
    } catch { B.cats = []; }
    const saved = (state.saved.builder && state.saved.builder.state) || null;
    B.state = saved && Object.keys(saved).length ? saved : defaultBuilderState();
    B.randEnabled = Array.isArray(B.state.__random_cats__) ? new Set(B.state.__random_cats__) : null;
    B.excludedNeg = new Set();
    try {
      const ex = JSON.parse(B.state.__excluded_negative__ || '[]');
      if (Array.isArray(ex)) ex.forEach((x) => B.excludedNeg.add(x));
    } catch { /* пустой набор */ }
    state.loaded.builder = true;
    renderBuilder();
    await refreshBuilderPreview.now();
  } catch (e) {
    sections.replaceChildren(h('p', 'warn-line', t('Не удалось загрузить конфиг билдера:') + ' ' + e.message));
  }
}

function renderBuilder() {
  renderBuilderSections();
  renderNegativeSection();
  renderRandChips();
  const row = el('bSceneRow');
  row.hidden = false;
  el('bSceneWeight').value = B.state.scene_weight || '';
}

// -- секции ---------------------------------------------------------

function makeSection(id, title, depth, extraClass) {
  const wrap = h('div', 'bsec' + (extraClass ? ' ' + extraClass : '') + (B.open.has(id) ? '' : ' collapsed'));
  const head = h('div', 'bsec__head');
  const left = h('span');
  left.appendChild(document.createTextNode(title));
  const sel = h('span', 'bsec__sel');
  left.appendChild(sel);
  head.appendChild(left);
  head.appendChild(h('span', 'bsec__arrow', '▾'));
  const body = h('div', 'bsec__body');
  head.addEventListener('click', () => {
    const collapsed = wrap.classList.toggle('collapsed');
    if (collapsed) B.open.delete(id);
    else B.open.add(id);
  });
  wrap.appendChild(head);
  wrap.appendChild(body);
  return { wrap, body, sel };
}

function updateSummaries() {
  for (const s of B.summaries) s.el.textContent = s.text() ? '· ' + s.text() : '';
}

function renderPills(container, options, isActive, onPick, extraClass) {
  const box = h('div', 'pills');
  const restyle = () => box.querySelectorAll('.pill').forEach((p) => p.classList.toggle('active', isActive(p.dataset.label)));
  for (const opt of options) {
    const label = typeof opt === 'string' ? opt : opt.label;
    const hasLora = typeof opt !== 'string' && ((opt.loras && opt.loras.length) || opt.lora);
    const pill = h('span', 'pill' + (hasLora ? ' has-lora' : '') + (extraClass ? ' ' + extraClass : ''), label + (hasLora ? ' ✦' : ''));
    pill.dataset.label = label;
    if (typeof opt !== 'string' && opt.tags) pill.title = opt.tags;
    pill.addEventListener('click', () => {
      onPick(label);
      restyle();
      updateSummaries();
      scheduleSave();
      refreshBuilderPreview();
    });
    box.appendChild(pill);
  }
  restyle();
  container.appendChild(box);
}

function renderCategory(cat, body) {
  const id = cat.id;
  if (cat.type === 'free_text') {
    const ta = document.createElement('textarea');
    ta.placeholder = cat.placeholder || '';
    ta.value = B.state[id] || '';
    ta.addEventListener('input', () => {
      B.state[id] = ta.value;
      updateSummaries();
      scheduleSave();
      refreshBuilderPreview();
    });
    body.appendChild(ta);
    return () => (B.state[id] || '').trim().slice(0, 30);
  }
  if (cat.type === 'single_select') {
    // Как на сервере (get_selections): пустое значение = default категории.
    const eff = () => B.state[id] || cat.default || '';
    renderPills(body, cat.options || [], (lbl) => eff() === lbl, (lbl) => {
      B.state[id] = B.state[id] === lbl ? '' : lbl;
    });
    return eff;
  }
  if (cat.type === 'multi_select') {
    const cur = () => (Array.isArray(B.state[id]) ? B.state[id] : (B.state[id] ? [B.state[id]] : []));
    renderPills(body, cat.options || [], (lbl) => cur().includes(lbl), (lbl) => {
      const set = new Set(cur());
      if (set.has(lbl)) set.delete(lbl);
      else set.add(lbl);
      B.state[id] = [...set];
    });
    return () => cur().join(', ');
  }
  return () => '';
}

function renderCats(cats, container, depth) {
  for (const cat of cats || []) {
    const { wrap, body, sel } = makeSection('cat:' + (cat.id || cat.label), cat.label, depth);
    if (cat.type === 'group') {
      renderCats(cat.children || cat.categories || [], body, depth + 1);
    } else {
      const text = renderCategory(cat, body);
      B.summaries.push({ el: sel, text });
    }
    container.appendChild(wrap);
  }
}

function renderBuilderSections() {
  const box = el('bSections');
  box.replaceChildren();
  B.summaries = [];
  const cfg = B.cfg;

  for (const [key, title] of [['quality_prefix', t('Качество')], ['source', t('Источник')]]) {
    const block = cfg[key];
    if (!block || !block.presets) continue;
    const { wrap, body, sel } = makeSection('top:' + key, title, 0);
    const eff = () => B.state[key] || block.default || '';
    renderPills(body, Object.keys(block.presets), (lbl) => eff() === lbl, (lbl) => { B.state[key] = lbl; });
    B.summaries.push({ el: sel, text: eff });
    box.appendChild(wrap);
  }
  renderCats(cfg.categories || [], box, 0);
  updateSummaries();
}

function renderNegativeSection() {
  const box = el('bNegSection');
  box.replaceChildren();
  const presets = (B.cfg && B.cfg.negative_presets) || {};
  const { wrap, body, sel } = makeSection('neg', t('Негативный промпт'), 0, 'bsec--neg');
  renderPills(body, Object.keys(presets), (lbl) => effectiveNegPreset() === lbl, (lbl) => {
    B.state.negative_preset = lbl;
  }, 'pill--neg');
  B.summaries.push({ el: sel, text: effectiveNegPreset });
  body.appendChild(h('div', 'mini-label', t('Дополнительно:')));
  const ta = document.createElement('textarea');
  ta.placeholder = t('доп. негативные теги…');
  ta.value = B.state.extra_negative || '';
  ta.addEventListener('input', () => {
    B.state.extra_negative = ta.value;
    scheduleSave();
    refreshBuilderPreview();
  });
  body.appendChild(ta);
  box.appendChild(wrap);
  updateSummaries();
}

// -- случайный выбор --------------------------------------------------

function renderRandChips() {
  const box = el('bRandChips');
  box.replaceChildren();
  for (const { id, label } of B.cats) {
    const on = B.randEnabled === null || B.randEnabled.has(id);
    const chip = h('span', 'pill pill--chip' + (on ? ' active' : ''), (on ? '● ' : '○ ') + label);
    chip.addEventListener('click', () => {
      if (B.randEnabled === null) B.randEnabled = new Set(B.cats.map((c) => c.id));
      if (B.randEnabled.has(id)) B.randEnabled.delete(id);
      else B.randEnabled.add(id);
      renderRandChips();
      scheduleSave();
    });
    box.appendChild(chip);
  }
}

async function builderRandomize() {
  if (!B.cfg) return;
  const btn = el('bRandom');
  btn.disabled = true;
  try {
    const data = await postJson('api/builder/randomize', {
      state: syncBuilderMeta(),
      enabled_ids: B.randEnabled === null ? null : [...B.randEnabled],
    });
    B.state = data.state;
    el('bRandTouched').textContent = '↺ ' + (data.touched || []).join(', ');
    renderBuilder();
    scheduleSave();
    refreshBuilderPreview();
  } catch (e) {
    el('bRandTouched').textContent = t('ошибка запроса');
    console.error(e);
  } finally {
    btn.disabled = false;
  }
}

function builderReset() {
  if (!B.cfg) return;
  const keepCats = B.state.__random_cats__;
  B.state = defaultBuilderState();
  B.state.__random_cats__ = keepCats;
  B.excludedNeg.clear();
  el('bRandTouched').textContent = '';
  renderBuilder();
  scheduleSave();
  refreshBuilderPreview();
}

// -- предпросмотр -------------------------------------------------------

async function doBuilderPreview() {
  if (!B.cfg) return;
  const seq = ++B.seq;
  const st = { ...syncBuilderMeta(), negative_preset: effectiveNegPreset(), extra_negative: B.state.extra_negative || '' };
  try {
    const data = await postJson('api/builder/preview', {
      state: st,
      // Теги персонажей идут в предпросмотр, чтобы видеть ИТОГОВЫЙ промпт
      // (узел при выполнении склеивает их с билдером точно так же).
      char_tags: C.preview ? C.preview.combined_tags || '' : '',
      excluded_negative: [...B.excludedNeg],
    });
    if (seq !== B.seq) return;
    B.preview = data;
  } catch (e) {
    if (seq !== B.seq) return;
    B.preview = null;
    el('bPositive').textContent = t('Ошибка предпросмотра:') + ' ' + e.message;
    return;
  }
  renderBuilderPreview();
  renderLoraEffective();
}

const refreshBuilderPreview = debounce(doBuilderPreview, 150);
refreshBuilderPreview.now = doBuilderPreview;

function renderBuilderPreview() {
  const data = B.preview;
  el('bPositive').textContent = data ? data.positive_text || '' : '';

  const issues = el('bIssues');
  const list = (data && data.issues) || [];
  issues.hidden = !list.length;
  issues.textContent = list.join(' · ');

  const wrap = el('bNegWrap');
  const all = (data && data.all_negative_tags) || [];
  wrap.hidden = !all.length;
  if (!all.length) return;
  const box = el('bNegTags');
  box.replaceChildren();
  for (const tag of all) {
    const isEx = B.excludedNeg.has(tag);
    const pill = h('span', 'tag' + (isEx ? ' excluded' : ''), tag);
    pill.title = isEx ? t('{tag} — убран (клик, чтобы вернуть)', { tag }) : t('{tag} — клик, чтобы убрать', { tag });
    pill.addEventListener('click', () => {
      if (B.excludedNeg.has(tag)) B.excludedNeg.delete(tag);
      else B.excludedNeg.add(tag);
      scheduleSave();
      refreshBuilderPreview();
    });
    box.appendChild(pill);
  }
  const n = all.filter((x) => B.excludedNeg.has(x)).length;
  el('bNegExcludedCount').hidden = n === 0;
  el('bNegExcludedCount').textContent = t('−{n} убрано', { n });
  el('bNegRestore').hidden = n === 0;
  el('bNegHint').hidden = n > 0;
}

function wireBuilder() {
  el('bRandom').addEventListener('click', builderRandomize);
  el('bReset').addEventListener('click', builderReset);
  el('bRandSettings').addEventListener('click', () => {
    const panel = el('bRandPanel');
    panel.hidden = !panel.hidden;
    el('bRandSettings').classList.toggle('on', !panel.hidden);
  });
  el('bRandAll').addEventListener('click', () => { B.randEnabled = null; renderRandChips(); scheduleSave(); });
  el('bRandNone').addEventListener('click', () => { B.randEnabled = new Set(); renderRandChips(); scheduleSave(); });
  el('bSceneWeight').addEventListener('input', (e) => {
    B.state.scene_weight = e.target.value;
    scheduleSave();
    refreshBuilderPreview();
  });
  el('bPosCopy').addEventListener('click', (e) => copyText(e.currentTarget, B.preview ? B.preview.positive_text : ''));
  el('bNegCopy').addEventListener('click', (e) => copyText(e.currentTarget, B.preview ? B.preview.negative_text : ''));
  el('bNegRestore').addEventListener('click', () => {
    B.excludedNeg.clear();
    scheduleSave();
    refreshBuilderPreview();
  });
}

// ================================================================
// LoRA — MultiLoraLoader (ручные слоты)
// ================================================================

async function loadLoras() {
  try {
    state.loraFiles = (await api('api/loras')).files || [];
    state.loaded.loras = true;
  } catch (e) {
    console.warn('LoRA-список недоступен:', e);
  }
  const dl = el('loraFileOptions');
  dl.replaceChildren(...state.loraFiles.map((f) => {
    const o = document.createElement('option');
    o.value = f;
    return o;
  }));
  const saved = state.saved.loras;
  if (Array.isArray(saved)) {
    saved.slice(0, LORA_SLOTS).forEach((s, i) => {
      state.loras[i] = { file: s.file || '', strength: Number.isFinite(+s.strength) ? +s.strength : 1 };
    });
  }
  renderLoraSlots();
  renderLoraEffective();
}

function validateLoraInput(input, value) {
  input.classList.toggle('invalid', !!value && state.loraFiles.length > 0 && !state.loraFiles.includes(value));
}

function renderLoraSlots() {
  const box = el('loraSlots');
  box.replaceChildren();
  state.loras.forEach((slot, i) => {
    const row = h('div', 'loraslot');
    const file = document.createElement('input');
    file.type = 'text';
    file.setAttribute('list', 'loraFileOptions');
    file.placeholder = t('— нет — (слот {n})', { n: i + 1 });
    file.value = slot.file;
    validateLoraInput(file, slot.file);
    file.addEventListener('input', () => {
      slot.file = file.value.trim();
      validateLoraInput(file, slot.file);
      scheduleSave();
      renderLoraEffective();
    });
    const str = document.createElement('input');
    str.type = 'number';
    str.step = '0.05';
    str.min = '-10';
    str.max = '10';
    str.value = slot.strength;
    str.title = t('Сила LoRA (можно отрицательную)');
    str.addEventListener('input', () => {
      const v = parseFloat(str.value);
      slot.strength = Number.isFinite(v) ? v : 1;
      scheduleSave();
      renderLoraEffective();
    });
    const clear = h('button', 'loraslot__clear', '✕');
    clear.type = 'button';
    clear.title = t('Очистить слот');
    clear.addEventListener('click', () => {
      slot.file = '';
      slot.strength = 1;
      file.value = '';
      str.value = 1;
      validateLoraInput(file, '');
      scheduleSave();
      renderLoraEffective();
    });
    row.append(file, str, clear);
    box.appendChild(row);
  });
}

// Что реально получит MultiLoraLoader: LoRA персонажей -> поверх них LoRA
// билдера -> поверх всего ручные слоты (перезаписывают совпадающее имя).
function renderLoraEffective() {
  const merged = new Map();
  for (const l of (C.preview && C.preview.lora_list) || []) {
    merged.set(l.filename, { strength: l.strength, source: t('персонаж') });
  }
  for (const l of (B.preview && B.preview.loras) || []) {
    merged.set(l.filename, { strength: l.strength, source: t('билдер') });
  }
  for (const s of state.loras) {
    if (!s.file) continue;
    const prev = merged.get(s.file);
    merged.set(s.file, { strength: s.strength, source: t('вручную'), replaced: prev || null });
  }
  const box = el('loraEffective');
  box.hidden = merged.size === 0;
  if (!merged.size) return;
  const frag = document.createDocumentFragment();
  frag.appendChild(document.createTextNode(t('Будет загружено:')));
  for (const [file, info] of merged) {
    frag.appendChild(document.createElement('br'));
    frag.appendChild(document.createTextNode('\u00a0\u00a0' + file + ' → '));
    frag.appendChild(h('b', null, Number(info.strength).toFixed(2)));
    let note = ' (' + info.source;
    if (info.replaced) note += ', ' + t('вместо') + ' ' + info.replaced.source + ' ' + Number(info.replaced.strength).toFixed(2);
    frag.appendChild(document.createTextNode(note + ')'));
  }
  box.replaceChildren(frag);
}

// ================================================================
// Кадр и технические параметры
// ================================================================

function fillSelect(sel, options, value) {
  sel.replaceChildren(...options.map((o) => {
    const opt = document.createElement('option');
    opt.value = o;
    opt.textContent = o;
    return opt;
  }));
  if (value && options.includes(value)) sel.value = value;
}

async function loadFrame() {
  const gen = state.config.generation;
  const saved = state.saved.frame || {};
  const pick = async (path, fallback) => {
    try { return (await api(path)).options || fallback; } catch { return fallback; }
  };
  const [aspects, samplers, ckpts] = await Promise.all([
    pick('api/aspect-ratios', gen.fallback_aspect_ratios),
    pick('api/samplers', gen.fallback_samplers),
    pick('api/checkpoints', []),
  ]);
  fillSelect(el('aspectRatio'), aspects, saved.aspect_ratio || gen.default_aspect_ratio);
  fillSelect(el('samplerName'), samplers, saved.sampler_name || gen.default_sampler);
  // Пустой список моделей -- ComfyUI недоступен: оставляем пункт «из шаблона»,
  // и бэкенд не трогает ckpt_name.
  fillSelect(el('checkpoint'), ckpts.length ? ckpts : [''], saved.checkpoint);
  if (!ckpts.length) el('checkpoint').options[0].textContent = t('как в шаблоне');

  const mp = el('megapixels');
  mp.replaceChildren(...gen.megapixel_options.map((v) => {
    const o = document.createElement('option');
    o.value = v;
    o.textContent = `${v} MP`;
    return o;
  }));
  mp.value = saved.megapixels || gen.default_megapixels;

  el('batchSize').value = saved.batch_size || gen.default_batch_size;
  el('stepsMin').value = saved.steps_min || gen.default_steps_min;
  el('stepsMax').value = saved.steps_max || gen.default_steps_max;
  el('cfg').value = saved.cfg !== undefined && saved.cfg !== '' ? saved.cfg : gen.default_cfg;
  el('seed').value = saved.seed || '';
  el('randomizeSeed').checked = saved.randomize_seed !== undefined ? !!saved.randomize_seed : true;
  state.loaded.frame = true;
}

// ---------------------------------------------------------------- вспомогательные куски Imagine (галерея, лайтбокс)

function appendPlate(url) {
  const plate = document.createElement('div');
  plate.className = 'plate';
  const img = document.createElement('img');
  img.src = url;
  img.alt = 'generated';
  plate.appendChild(img);
  plate.addEventListener('click', () => openLightbox(url));
  el('galleryGrid').appendChild(plate);
  return plate;
}

function hideLoadingScreen() {
  const screen = el('loadingScreen');
  if (!screen) return;
  screen.classList.add('loading-screen--hidden');
  screen.addEventListener('transitionend', () => { screen.hidden = true; }, { once: true });
}


function showError(msg) {
  const line = el('errorLine');
  line.textContent = msg;
  line.hidden = false;
  el('infoLine').hidden = true;
}
function clearError() {
  el('errorLine').hidden = true;
}
function showInfo(msg, ms = 4000) {
  const line = el('infoLine');
  line.textContent = msg;
  line.hidden = false;
  el('errorLine').hidden = true;
  setTimeout(() => { line.hidden = true; }, ms);
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
  plate.innerHTML =
    '<div class="tray-ripple"></div>' +
    '<span>' + t('генерируется…') + '</span>' +
    '<button type="button" class="plate-stop-btn" hidden ' +
    'title="' + t('Останавливает то, что ComfyUI выполняет прямо сейчас (не только эту генерацию, если их несколько в очереди)') + '">' +
    t('Остановить') + '</button>';
  el('galleryGrid').prepend(plate);
  return plate;
}

// Этап 7 дорожной карты: единственное реально доступное в проекте
// действие с очередью -- POST .../interrupt, который останавливает то,
// что ComfyUI выполняет ПРЯМО СЕЙЧАС, а не конкретно ту генерацию, чей
// prompt_id передан в URL (сам параметр бэкендом не используется, см.
// docstring interrupt() в imagine/backend/main.py) -- отмены именно
// ОЖИДАЮЩЕГО задания в очереди в проекте нет нигде. Поэтому кнопка
// показывается только на плашке в состоянии "running" (см.
// pollGeneration ниже) -- там клик по ней однозначно останавливает
// именно её, а не что-то чужое.
async function interruptGeneration(promptId, plate, stopBtn) {
  stopBtn.disabled = true;
  stopBtn.textContent = t('останавливается…');
  // Отмечаем плашку ДО ответа сервера (сам interrupt -- fire-and-forget,
  // ComfyUI не подтверждает какую генерацию он остановил) -- нужно,
  // чтобы pollGeneration ниже показал честное "остановлено", а не
  // "ошибка выполнения", когда ComfyUI зарегистрирует прерывание в
  // истории как status.completed === False.
  plate.dataset.userStopped = '1';
  try {
    await fetch(`api/generate/${promptId}/interrupt`, { method: 'POST' });
  } catch (e) {
    // Сеть моргнула -- следующий тик pollGeneration сам разберётся
    // (либо статус подтянется, либо покажет "потеряна связь"); кнопку
    // намеренно оставляем отключённой, чтобы не долбить interrupt
    // повторно в рамках уже начатой попытки остановки.
  }
}

async function pollGeneration(promptId, pendingPlate) {
  try {
    const res = await fetch(`api/generate/${promptId}/status`);
    const data = await res.json();

    const stopBtn = pendingPlate.querySelector('.plate-stop-btn');
    if (data.state === 'running') {
      pendingPlate.querySelector('span').textContent = t('генерируется…');
      if (stopBtn && !pendingPlate.dataset.userStopped) {
        stopBtn.hidden = false;
        if (!stopBtn.dataset.bound) {
          stopBtn.dataset.bound = '1';
          stopBtn.addEventListener('click', () => interruptGeneration(promptId, pendingPlate, stopBtn));
        }
      }
    } else if (data.state === 'pending') {
      pendingPlate.querySelector('span').textContent = t('в очереди (#{n})', {n: data.queue_position});
      if (stopBtn) stopBtn.hidden = true;
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
      // Пользователь сам остановил именно эту генерацию (см.
      // interruptGeneration выше) -- честно показываем это отдельно от
      // настоящей ошибки выполнения, обычным info-баннером, а не
      // тревожным error-баннером.
      if (pendingPlate.dataset.userStopped) {
        showInfo(t('Генерация остановлена'));
      } else {
        showError(t('ComfyUI сообщил об ошибке выполнения'));
      }
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


function wireLightbox() {
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

}

// ================================================================
// Генерация
// ================================================================

function collectRequest() {
  const num = (id) => parseFloat(el(id).value);
  const int = (id) => parseInt(el(id).value, 10);
  const stepsMin = int('stepsMin');
  const stepsMax = int('stepsMax');
  return {
    characters: { selected: [...C.selected], excluded: [...C.excluded] },
    builder: {
      state: syncBuilderMeta(),
      negative_preset: effectiveNegPreset(),
      extra_negative: B.state.extra_negative || '',
      excluded_negative: [...B.excludedNeg],
    },
    loras: state.loras.filter((l) => l.file).map((l) => ({ file: l.file, strength: l.strength })),
    checkpoint: el('checkpoint').value || null,
    aspect_ratio: el('aspectRatio').value,
    megapixels: num('megapixels'),
    batch_size: int('batchSize'),
    steps_min: Number.isFinite(stepsMin) ? stepsMin : 20,
    steps_max: Number.isFinite(stepsMax) ? stepsMax : (Number.isFinite(stepsMin) ? stepsMin : 40),
    cfg: Number.isFinite(num('cfg')) ? num('cfg') : null,
    sampler_name: el('samplerName').value || null,
    seed: el('seed').value ? int('seed') : null,
    randomize_seed: el('randomizeSeed').checked,
  };
}

async function onGenerate() {
  clearError();
  if (!B.cfg) {
    showError(t('Билдер промпта не загружен — проверьте ComfyUI и расширение character_search_ui'));
    return;
  }
  const payload = collectRequest();

  // Кнопка НЕ блокируется -- каждое нажатие ставит своё задание в очередь
  // ComfyUI и получает свою плашку (как в Imagine).
  state.activeGenerations = (state.activeGenerations || 0) + 1;
  updateGenerateBtnLabel();
  const pendingPlate = addPendingPlate();

  try {
    const data = await postJson('api/generate', payload);
    pollGeneration(data.prompt_id, pendingPlate);
  } catch (e) {
    showError(e.message || t('Ошибка генерации'));
    pendingPlate.remove();
    finishGeneration();
  }
}

// ================================================================
// Статус ComfyUI, проверка узлов, память
// ================================================================

async function refreshComfyStatus() {
  try {
    const data = await api('api/comfyui/status');
    const dot = el('statusDot');
    const wasAlive = dot.classList.contains('alive');
    dot.className = 'status-dot ' + (data.alive ? 'alive' : 'dead');
    el('statusText').textContent = data.alive ? t('ComfyUI на связи') : t('ComfyUI недоступен');
    // ComfyUI мог подняться уже после открытия страницы -- догружаем то,
    // что не удалось получить, без ручного обновления.
    if (data.alive && !wasAlive) reloadMissing();
  } catch {
    el('statusDot').className = 'status-dot dead';
    el('statusText').textContent = t('нет связи с бэкендом');
  }
}

async function checkNodes() {
  const warn = el('nodesWarning');
  try {
    const nodes = await api('api/nodes');
    const missing = Object.keys(nodes).filter((k) => !nodes[k]);
    warn.hidden = missing.length === 0;
    if (missing.length) {
      warn.textContent = t('В ComfyUI не найдены узлы: {list}. Проверьте установку расширения character_search_ui.', { list: missing.join(', ') });
    }
  } catch {
    warn.hidden = true; // ComfyUI недоступен -- об этом уже говорит статус
  }
}

async function reloadMissing() {
  await Promise.all([
    state.loaded.chars ? null : loadCharacters(),
    state.loaded.builder ? null : loadBuilder(),
    state.loaded.loras ? null : loadLoras(),
    loadFrame(),
  ]);
  checkNodes();
}

async function onFreeMemory() {
  const btn = el('freeMemoryBtn');
  btn.disabled = true;
  clearError();
  try {
    await api('api/comfyui/free-memory', { method: 'POST' });
    showInfo(t('Модели выгружены из памяти'));
  } catch (e) {
    showError(e.message || t('Не удалось выгрузить модели'));
  } finally {
    btn.disabled = false;
  }
}

// ================================================================
// init
// ================================================================

async function initRecentGallery() {
  const promptId = new URLSearchParams(location.search).get('prompt_id');
  let items = [];
  try {
    items = (await api('api/generate/recent?limit=20')).items || [];
  } catch { /* ComfyUI недоступен -- галерея просто пустая */ }
  let found = !promptId;
  if (items.length) {
    el('galleryEmpty').hidden = true;
    for (const item of items) {
      if (promptId && item.prompt_id === promptId) found = true;
      for (const img of item.images) appendPlate(img.url);
    }
  }
  if (promptId && !found) {
    const plate = addPendingPlate();
    state.activeGenerations += 1;
    updateGenerateBtnLabel();
    pollGeneration(promptId, plate);
  }
}

function wireStaticHandlers() {
  el('generateBtn').addEventListener('click', onGenerate);
  el('freeMemoryBtn').addEventListener('click', onFreeMemory);
  ['aspectRatio', 'megapixels', 'batchSize', 'stepsMin', 'stepsMax', 'cfg', 'samplerName', 'checkpoint', 'seed', 'randomizeSeed']
    .forEach((id) => el(id).addEventListener('change', scheduleSave));
  wireCharacters();
  wireBuilder();
  wireLightbox();
}

async function init() {
  await I18N.init();
  try {
    state.config = await api('api/config');
    try { state.saved = await api('api/state'); } catch { state.saved = {}; }
    wireStaticHandlers();
    // Каждая часть грузится независимо: сбой одной (например ComfyUI ещё
    // не поднялся) не должен ломать остальные.
    await Promise.all([loadFrame(), loadLoras(), loadCharacters(), loadBuilder()]);
    await refreshComfyStatus();
    checkNodes();
    await initRecentGallery();
  } catch (e) {
    console.error('Ошибка инициализации:', e);
  } finally {
    hideLoadingScreen();
  }
  setInterval(refreshComfyStatus, 6000);
}

document.addEventListener('DOMContentLoaded', init);
// Смена языка из Studio -- перерисовываем то, что собирается из JS.
document.addEventListener('imagine:languagechange', () => {
  if (state.loaded.chars) { renderCharList(); renderCharChips(); renderCharPreview(); }
  if (state.loaded.builder) { renderBuilder(); renderBuilderPreview(); }
  renderLoraSlots();
  renderLoraEffective();
  updateGenerateBtnLabel();
});
