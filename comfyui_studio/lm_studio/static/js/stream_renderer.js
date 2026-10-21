/* Batch streamed deltas while keeping the message and reasoning disclosure alive. */
"use strict";
window.createStudioStreamRenderer = (article, answer, pane) => {
  const body = article.querySelector(".message__body");
  let timer = null, content = "", reasoning = "", details = null, reasoningText = null;
  let copy = null;
  function render() {
    timer = null;
    const nearBottom = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 140;
    if (content !== answer.content) {
      renderStudioMarkdown(body, answer.content);
      content = answer.content;
      if (!copy && content) {
        copy = document.createElement("button");
        copy.className = "btn-link"; copy.textContent = t("Копировать");
        copy.onclick = async () => {
          await copyStudioText(answer.content); copy.textContent = t("Скопировано");
        };
        article.querySelector(".message__actions").append(copy);
      }
    }
    if (reasoning !== answer.reasoning) {
      if (!details) {
        details = document.createElement("details"); details.className = "reasoning";
        details.open = true;
        const summary = document.createElement("summary"); summary.textContent = t("Рассуждение");
        reasoningText = document.createElement("pre");
        details.append(summary, reasoningText); article.insertBefore(details, body);
      }
      reasoningText.textContent = answer.reasoning; reasoning = answer.reasoning;
    }
    if (nearBottom) pane.scrollTop = pane.scrollHeight;
  }
  return {
    update() { if (timer === null) timer = setTimeout(render, 50); },
    finish() {
      if (timer !== null) clearTimeout(timer);
      render();
    },
  };
};
