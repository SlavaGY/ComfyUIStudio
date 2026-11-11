"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const code = fs.readFileSync(process.argv[2], "utf8");

function environment(hidden = false) {
  const timers = new Map(), listeners = new Map();
  let id = 0;
  const document = {
    hidden,
    addEventListener(name, fn) { listeners.set(name, fn); },
    removeEventListener(name, fn) { if (listeners.get(name) === fn) listeners.delete(name); },
  };
  const window = {};
  vm.runInNewContext(code, {
    window, document,
    setTimeout(fn, delay) { const key = ++id; timers.set(key, { fn, delay }); return key; },
    clearTimeout(key) { timers.delete(key); },
  });
  return {
    window, timers, document, listeners,
    delay() { assert.equal(timers.size, 1); return [...timers.values()][0].delay; },
    async tick() {
      assert.equal(timers.size, 1);
      const [key, timer] = [...timers][0]; timers.delete(key);
      await timer.fn();
    },
    async visible(value) {
      document.hidden = !value; listeners.get("visibilitychange")?.();
      await new Promise(resolve => setImmediate(resolve));
    },
  };
}

(async () => {
  const env = environment();
  let calls = 0, success = false;
  const poll = env.window.createBackgroundPoll(async () => { calls++; return success; }, 4000);
  assert.equal(env.delay(), 4000);
  for (const delay of [8000, 16000, 32000, 60000, 60000]) {
    await env.tick(); assert.equal(env.delay(), delay);
  }
  success = true;
  await env.tick(); assert.equal(env.delay(), 4000);
  await env.visible(false);
  assert.equal(env.timers.size, 0);
  const before = calls;
  await poll.refresh(); assert.equal(calls, before);
  await env.visible(true);
  assert.equal(calls, before + 1); assert.equal(env.delay(), 4000);
  poll.stop(); assert.equal(env.timers.size, 0); assert.equal(env.listeners.size, 0);

  // A visibility bounce/manual refresh cannot overlap an unresolved request.
  const slow = environment();
  let release, active = 0, peak = 0;
  const slowPoll = slow.window.createBackgroundPoll(async () => {
    active++; peak = Math.max(peak, active);
    await new Promise(resolve => { release = resolve; });
    active--;
  }, 5000);
  const pending = slow.tick();
  await slow.visible(false); await slow.visible(true); await slowPoll.refresh();
  assert.equal(peak, 1); assert.equal(active, 1); assert.equal(slow.timers.size, 0);
  release(); await pending;
  assert.equal(slow.delay(), 0); // refresh requested while in flight is coalesced
  slowPoll.stop();

  const stopped = environment();
  let finish;
  const stoppedPoll = stopped.window.createBackgroundPoll(() => new Promise(resolve => { finish = resolve; }), 5000);
  const stoppedRequest = stopped.tick();
  stoppedPoll.stop(); finish(); await stoppedRequest;
  assert.equal(stopped.timers.size, 0); assert.equal(stopped.listeners.size, 0);

  const hidden = environment(true);
  let hiddenCalls = 0;
  const hiddenPoll = hidden.window.createBackgroundPoll(() => { hiddenCalls++; }, 5000, { immediate: true });
  assert.equal(hidden.timers.size, 0);
  await hidden.visible(true); assert.equal(hiddenCalls, 1);
  hiddenPoll.stop();

  const failing = environment();
  failing.window.createBackgroundPoll(() => { throw Error("offline"); }, 10000);
  await failing.tick(); assert.equal(failing.delay(), 20000);
  await failing.visible(false); await failing.visible(true);
  assert.equal(failing.delay(), 20000); // reset backoff, then fail the immediate retry
  console.log("background polling PASSED");
})().catch(error => { console.error(error); process.exitCode = 1; });
