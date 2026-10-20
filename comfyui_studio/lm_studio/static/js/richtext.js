/* Locally bundled Markdown/KaTeX. Model HTML never runs in the page. */
"use strict";
(() => {
  const escape = text => text.replace(/[&<>"']/g, char => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[char]));
  const math = [];
  function extension(name, level, regex, start) {
    return {name, level, start: src => src.search(start), tokenizer(src) {
      const match = src.match(regex);
      if (match) return {type: name, raw: match[0], text: match[1] ?? match[2], display: level === "block"};
    }, renderer(token) {
      const index = math.push({text: token.text, display: token.display}) - 1;
      return `<span class="math-slot" data-math="${index}"></span>`;
    }};
  }
  marked.use({gfm: true, breaks: true, renderer: {
    html: token => escape(token.text),
    image: token => escape(token.text || ""),
  }, extensions: [
    extension("blockMath", "block", /^(?:\\\[([\s\S]*?)\\\]|\$\$([\s\S]*?)\$\$)(?:\n|$)?/, /\\\[|\$\$/),
    extension("inlineMath", "inline", /^(?:\\\(([\s\S]*?)\\\)|\$([^$\n]+?)\$)/, /\\\(|\$/),
  ]});
  window.copyStudioText = async text => {
    try {
      if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(text); return; }
    } catch (_) { /* Older embedded Chromium can deny the async API. */ }
    const input = document.createElement("textarea"), active = document.activeElement;
    input.value = text; input.style.cssText = "position:fixed;left:-9999px;top:0";
    document.body.append(input); input.focus(); input.select();
    const copied = document.execCommand("copy"); input.remove(); active?.focus();
    if (!copied) throw Error(t("Не удалось скопировать текст"));
  };
  window.renderStudioMarkdown = (element, text) => {
    math.length = 0;
    element.innerHTML = DOMPurify.sanitize(marked.parse(text), {USE_PROFILES: {html: true}, ADD_ATTR: ["data-math"], FORBID_TAGS: ["style", "input", "form", "button"]});
    element.querySelectorAll(".math-slot").forEach(slot => {
      const formula = math[Number(slot.dataset.math)];
      if (formula) katex.render(formula.text, slot, {displayMode: formula.display, throwOnError: false, trust: false, strict: "ignore", maxExpand: 1000, maxSize: 20});
    });
    element.querySelectorAll("a").forEach(link => { link.target = "_blank"; link.rel = "noopener noreferrer"; });
    element.querySelectorAll("pre > code").forEach(code => {
      const button = document.createElement("button"); button.className = "btn-link code-copy"; button.textContent = t("Копировать");
      button.onclick = async () => {
        try { await copyStudioText(code.textContent); button.textContent = t("Скопировано"); }
        catch (error) { button.textContent = error.message; }
      };
      code.parentElement.prepend(button);
    });
  };
})();
