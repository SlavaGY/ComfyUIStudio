/* Render a recent page first; keep existing articles when the chat changes. */
"use strict";
window.createStudioMessageWindow = (pane, createMessage, createWelcome, changed = () => {}) => {
  const pageSize = 40, records = new Map();
  let chatId = null, current = null, start = 0, expanded = false;
  const earlier = document.createElement("button");
  earlier.className = "btn-link message-earlier";
  earlier.onclick = () => {
    const height = pane.scrollHeight, top = pane.scrollTop;
    start = Math.max(0, start - pageSize); expanded = true;
    render(current, { preserve: true });
    pane.scrollTop = top + pane.scrollHeight - height;
    changed();
  };
  function render(chat, { reset = false, invalidate = false, forceBottom = false, preserve = false } = {}) {
    const nearBottom = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 140;
    const messages = chat?.messages || [];
    if (reset || chatId !== chat?.id) {
      records.clear(); pane.replaceChildren(); expanded = false;
    }
    current = chat; chatId = chat?.id;
    start = expanded ? Math.min(start, Math.max(0, messages.length - pageSize)) : Math.max(0, messages.length - pageSize);
    if (!messages.length) {
      records.clear(); pane.replaceChildren(createWelcome()); return;
    }
    earlier.textContent = t("Показать предыдущие сообщения"); earlier.hidden = start === 0;
    const desired = [earlier];
    for (const [index, record] of records) {
      if (index < start || index >= messages.length || invalidate) { record.element.remove(); records.delete(index); }
    }
    for (let index = start; index < messages.length; index++) {
      const message = messages[index], signature = JSON.stringify(message);
      let record = records.get(index);
      // The stream renderer owns this live article, including its disclosure state.
      const streaming = record?.message === message && message.status === "streaming";
      if (!record || !streaming && record.signature !== signature) {
        const element = createMessage(message, index);
        if (record) record.element.replaceWith(element);
        record = { element, message, signature }; records.set(index, record);
      }
      desired.push(record.element);
    }
    // Insert/move only differing nodes. Existing articles stay attached.
    for (let index = 0; index < desired.length; index++) {
      if (pane.children[index] !== desired[index]) pane.insertBefore(desired[index], pane.children[index] || null);
    }
    while (pane.children.length > desired.length) pane.lastChild.remove();
    if (!preserve && (forceBottom || nearBottom)) pane.scrollTop = pane.scrollHeight;
  }
  return { render };
};
