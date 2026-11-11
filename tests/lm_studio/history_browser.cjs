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
    assert.equal(await page.locator(".message").count(), 40);
    assert.equal(await page.locator(".message__body").first().textContent(), "Message 200\n");
    await page.evaluate(() => {
      window.previousLastMessage = document.querySelector(".message:last-child");
      window.previousFirstMessage = document.querySelector(".message");
      document.getElementById("messages").scrollTop = 0;
    });
    const beforeTop = await page.evaluate(() => window.previousFirstMessage.getBoundingClientRect().top);
    await page.locator(".message-earlier").click();
    assert.equal(await page.locator(".message").count(), 80);
    assert.ok(await page.evaluate(() => window.previousLastMessage === document.querySelector(".message:last-child")));
    const afterTop = await page.evaluate(() => window.previousFirstMessage.getBoundingClientRect().top);
    assert.ok(Math.abs(beforeTop - afterTop) < 2, "prepending older messages preserves the reading position");
    await page.locator(".message-edit").first().click();
    assert.equal(await page.locator("#prompt").inputValue(), "Message 160");
    await page.locator("#cancelEdit").click();
    await page.locator("#chatTitle").fill("Another title");
    await page.locator("#chatTitle").press("Tab");
    await page.waitForFunction(() => document.querySelectorAll(".chat-item").length === 0 &&
      document.getElementById("chatList").textContent.includes("не найдены"));
    await page.locator("#chatTitle").fill("ЁЖИК restored");
    await page.locator("#chatTitle").press("Tab");
    await page.waitForFunction(() => document.querySelectorAll(".chat-item").length === 1 &&
      document.querySelector(".chat-item").textContent.includes("restored"));
    await require("./richtext_browser.cjs")(page);
    assert.deepEqual(errors, []);
    console.log("Real page: 50 -> 100 -> 120 chats; Unicode search and renaming passed");
  } finally { await browser.close(); }
}
test().catch(error => { console.error(error); process.exitCode = 1; });
