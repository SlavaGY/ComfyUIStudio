/* Full page + real local API; no models, original LM Studio or user profiles. */
const assert = require("node:assert/strict");
const {chromium} = require(process.env.STUDIO_PLAYWRIGHT_MODULE);
async function test() {
  const browser = await chromium.launch({channel: "chrome", headless: true});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(process.argv[2]);
    await page.waitForFunction(() => document.querySelectorAll(".chat-item").length === 50 &&
      !document.getElementById("moreChats").disabled);
    await page.locator("#moreChats").click();
    await page.waitForFunction(() => document.querySelectorAll(".chat-item").length === 100);
    await page.locator("#moreChats").click();
    await page.waitForFunction(() => document.querySelectorAll(".chat-item").length === 120);
    assert.equal(await page.locator("#moreChats").isVisible(), false);
    await page.locator("#chatSearch").fill("ёжик");
    await page.waitForFunction(() => document.querySelectorAll(".chat-item").length === 1 &&
      document.querySelector(".chat-item").textContent.includes("target"));
    await page.locator(".chat-item").click();
    await page.waitForFunction(() => document.getElementById("chatTitle").value.includes("target") &&
      !document.getElementById("chatTitle").disabled);
    await page.locator("#chatTitle").fill("Another title");
    await page.locator("#chatTitle").press("Tab");
    await page.waitForFunction(() => document.querySelectorAll(".chat-item").length === 0 &&
      document.getElementById("chatList").textContent.includes("не найдены"));
    await page.locator("#chatTitle").fill("ЁЖИК restored");
    await page.locator("#chatTitle").press("Tab");
    await page.waitForFunction(() => document.querySelectorAll(".chat-item").length === 1 &&
      document.querySelector(".chat-item").textContent.includes("restored"));
    assert.deepEqual(errors, []);
    console.log("Real page: 50 -> 100 -> 120 chats; Unicode search and renaming passed");
  } finally { await browser.close(); }
}
test().catch(error => { console.error(error); process.exitCode = 1; });
