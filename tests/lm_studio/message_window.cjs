const assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm");
class Element {
  constructor() { this.children = []; this.parent = null; this.hidden = false; this.scrollTop = 0; this.clientHeight = 300; }
  get lastChild() { return this.children.at(-1); }
  get scrollHeight() { return this.children.reduce((sum, node) => sum + (node.hidden ? 0 : 100), 0); }
  remove() { if (this.parent) { this.parent.children.splice(this.parent.children.indexOf(this), 1); this.parent = null; } }
  insertBefore(child, before) {
    child.remove();
    const index = before ? this.children.indexOf(before) : this.children.length;
    assert.ok(index >= 0); this.children.splice(index, 0, child); child.parent = this;
  }
  replaceChildren(...nodes) { for (const node of [...this.children]) node.remove(); for (const node of nodes) this.insertBefore(node, null); }
  replaceWith(node) { const parent = this.parent; parent.insertBefore(node, this); this.remove(); }
}
const context = { window: {}, document: { createElement: () => new Element() }, t: text => text };
vm.runInNewContext(fs.readFileSync(process.argv[2], "utf8"), context);
const pane = new Element(); let created = 0, changed = 0;
const renderer = context.window.createStudioMessageWindow(pane, (message, index) => {
  created++; const element = new Element(); element.index = index; element.content = message.content; return element;
}, () => new Element(), () => changed++);
const chat = { id: "large", messages: Array.from({ length: 2000 }, (_, i) => ({ role: "user", content: String(i) })) };
renderer.render(chat, { forceBottom: true });
assert.equal(created, 40); assert.equal(pane.children.length, 41);
assert.equal(pane.children[1].index, 1960); assert.equal(pane.lastChild.index, 1999);
const last = pane.lastChild;
const initialCreated = created;
chat.messages.push({ role: "user", content: "new" }); renderer.render(chat);
assert.equal(pane.children.length, 41); assert.equal(created, initialCreated + 1);
assert.equal(pane.children[1].index, 1961); assert.equal(pane.children.at(-2), last);
chat.messages.pop(); renderer.render(chat);
pane.scrollTop = 50; const oldHeight = pane.scrollHeight;
pane.children[0].onclick();
assert.equal(created, 82); assert.equal(changed, 1);
assert.equal(pane.children[1].index, 1920); assert.equal(pane.lastChild, last);
assert.equal(pane.scrollTop, 50 + pane.scrollHeight - oldHeight);

chat.messages.push({ role: "assistant", content: "", status: "streaming" });
renderer.render(chat, { forceBottom: true });
const live = pane.lastChild;
chat.messages.at(-1).content = "streaming answer";
pane.children[0].onclick();
assert.equal(pane.lastChild, live, "history paging must not replace the active stream article");
const snapshot = JSON.parse(JSON.stringify(chat)); snapshot.messages.at(-1).status = "completed";
const previous = pane.children.at(-2), count = created;
pane.scrollTop = 0; renderer.render(snapshot);
assert.equal(created, count + 1); assert.equal(pane.children.at(-2), previous);
assert.equal(pane.scrollTop, 0); assert.notEqual(pane.lastChild, live);

const short = { id: "short", messages: chat.messages.slice(0, 45) };
renderer.render(short); assert.equal(pane.children[1].index, 5);
pane.scrollTop = 20; pane.children[0].onclick();
assert.equal(pane.children[1].index, 0); assert.equal(pane.children[0].hidden, true);
const visible = created; renderer.render(short, { invalidate: true });
assert.equal(created - visible, 45, "language changes rebuild only the visible messages");
short.messages.splice(3); renderer.render(short);
assert.equal(pane.children.length, 4); assert.equal(pane.lastChild.index, 2);
renderer.render({ id: "empty", messages: [] }); assert.equal(pane.children.length, 1);
console.log("message window PASSED: bounded initial render, prepend anchoring, append, truncate, stream ownership");
