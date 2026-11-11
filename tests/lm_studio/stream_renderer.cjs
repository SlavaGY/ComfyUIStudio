/* Deterministic streaming regression test; run with node, no browser packages. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.textContent = ""; }
  append(...children) { this.children.push(...children); }
  insertBefore(child, before) { this.children.splice(this.children.indexOf(before), 0, child); }
  querySelector(selector) { return this.children.find(child => "." + child.className === selector); }
}
const article = new Element("article"), body = new Element("div"), actions = new Element("div");
body.className = "message__body"; actions.className = "message__actions"; article.append(body, actions);
const timers = new Map(); let nextId = 0, renders = 0, copied, lastDelay;
const context = {
  window: {}, document: {createElement: tag => new Element(tag)},
  t: text => text,
  createStudioMarkdownRenderer: element => text => { renders++; element.textContent = text; },
  copyStudioText: async text => { copied = text; },
  setTimeout: (callback, delay) => { lastDelay = delay; timers.set(++nextId, callback); return nextId; },
  clearTimeout: id => timers.delete(id),
};
vm.runInNewContext(fs.readFileSync(process.argv[2], "utf8"), context);
const answer = {content: "", reasoning: ""};
const pane = {scrollHeight: 500, scrollTop: 400, clientHeight: 100};
const renderer = context.window.createStudioStreamRenderer(article, answer, pane);
for (let i = 0; i < 100; i++) { answer.content += "x"; renderer.update(); }
assert.equal(timers.size, 1); assert.equal(renders, 0);
assert.equal(lastDelay, 50);
function tick() { const callbacks = [...timers.values()]; timers.clear(); callbacks.forEach(fn => fn()); }
tick(); assert.equal(renders, 1); assert.equal(body.textContent.length, 100);
answer.reasoning = "thinking"; renderer.update(); tick();
assert.equal(renders, 1, "reasoning-only updates must not parse Markdown");
const details = article.querySelector(".reasoning"); details.open = false;
pane.scrollTop = 0;
answer.content += "last"; answer.reasoning += " more"; renderer.update();
renderer.finish();
assert.equal(timers.size, 0); assert.equal(renders, 2);
assert.equal(article.querySelector(".message__body"), body);
assert.equal(article.querySelector(".reasoning"), details);
assert.equal(details.open, false, "keep the user's collapsed reasoning");
assert.equal(pane.scrollTop, 0, "do not scroll a reader away from earlier messages");
assert.equal(details.children[1].textContent, "thinking more");
assert.equal(body.textContent, answer.content);
answer.content += "x".repeat(200000); renderer.update();
assert.equal(lastDelay, 250); renderer.finish(); assert.equal(timers.size, 0);
(async () => { await actions.children[0].onclick(); assert.equal(copied, answer.content); })().catch(error => {
  console.error(error); process.exitCode = 1;
});
