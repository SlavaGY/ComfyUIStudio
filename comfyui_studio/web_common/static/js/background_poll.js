// Optional UI checks only; generation progress has its own lifecycle.
(() => {
  "use strict";
  window.createBackgroundPoll = function (task, interval, { immediate = false } = {}) {
    let timer = null, running = false, stopped = false, failures = 0, wake = false;
    function clear() { if (timer !== null) clearTimeout(timer); timer = null; }
    function schedule(delay) {
      clear();
      if (!stopped && !document.hidden) timer = setTimeout(run, delay);
    }
    async function run() {
      clear();
      if (stopped || document.hidden) return;
      if (running) { wake = true; return; }
      running = true;
      try {
        failures = await task() === false ? Math.min(failures + 1, 6) : 0;
      } catch (_) { failures = Math.min(failures + 1, 6); }
      finally {
        running = false;
        const delay = wake ? 0 : Math.min(interval * 2 ** failures, 60000);
        wake = false;
        schedule(delay);
      }
    }
    function visibility() {
      clear();
      if (!document.hidden) { failures = 0; void run(); }
    }
    document.addEventListener("visibilitychange", visibility);
    schedule(immediate ? 0 : interval);
    return {
      refresh: run,
      stop() { stopped = true; clear(); document.removeEventListener("visibilitychange", visibility); },
    };
  };
})();
