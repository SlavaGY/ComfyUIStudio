/* Cursor pagination; late responses from an older search never replace the list. */
"use strict";
window.createStudioChatHistory = (load, changed) => {
  let generation = 0;
  const state = {items: [], nextCursor: null, loading: false, query: ""};
  async function fetchPage(reset) {
    if (!reset && (state.loading || !state.nextCursor)) return;
    const current = reset ? ++generation : generation;
    const cursor = reset ? null : state.nextCursor;
    if (reset) { state.items = []; state.nextCursor = null; }
    state.loading = true; changed();
    try {
      const params = new URLSearchParams({limit: "50", search: state.query});
      if (cursor) params.set("cursor", cursor);
      const page = await load(params);
      if (current !== generation) return;
      const known = new Set(state.items.map(item => item.id));
      state.items = state.items.concat(page.chats.filter(item => !known.has(item.id)));
      state.nextCursor = page.next_cursor;
    } catch (error) {
      if (current === generation) throw error;
    } finally {
      if (current === generation) { state.loading = false; changed(); }
    }
  }
  return Object.assign(state, {
    reset(query = "") { state.query = query; return fetchPage(true); },
    loadMore() { return fetchPage(false); },
  });
};
