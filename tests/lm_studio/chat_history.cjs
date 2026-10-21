const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const context = {window: {}, URLSearchParams};
vm.runInNewContext(fs.readFileSync(process.argv[2], "utf8"), context);
async function test() {
  const requests = [];
  const pager = context.window.createStudioChatHistory(params => new Promise((resolve, reject) => {
    requests.push({params, resolve, reject});
  }), () => {});
  const old = pager.reset("old"), latest = pager.reset("ёжик");
  requests[1].resolve({chats: [{id: "a"}], next_cursor: "next"}); await latest;
  requests[0].resolve({chats: [{id: "old"}], next_cursor: "wrong"}); await old;
  assert.equal(pager.items[0].id, "a"); assert.equal(pager.nextCursor, "next");
  assert.equal(requests[1].params.get("search"), "ёжик");
  const more = pager.loadMore(); await pager.loadMore();
  assert.equal(requests.length, 3); assert.equal(requests[2].params.get("cursor"), "next");
  requests[2].resolve({chats: [{id: "a"}, {id: "b"}], next_cursor: "last"}); await more;
  assert.equal(pager.items.map(item => item.id).join(","), "a,b");
  const failed = pager.loadMore(); requests[3].reject(Error("offline"));
  await assert.rejects(failed, /offline/);
  assert.equal(pager.loading, false); assert.equal(pager.nextCursor, "last");
  const retry = pager.loadMore(); requests[4].resolve({chats: [{id: "c"}], next_cursor: null}); await retry;
  assert.equal(pager.items.length, 3); assert.equal(pager.nextCursor, null);
  await pager.loadMore(); assert.equal(requests.length, 5);
}
test().catch(error => { console.error(error); process.exitCode = 1; });
