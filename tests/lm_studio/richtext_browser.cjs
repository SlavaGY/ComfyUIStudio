const assert = require("node:assert/strict");

module.exports = async page => {
  const result = await page.evaluate(() => {
    const fixtures = [
      "# Heading\n\nParagraph **bold** and _italic_.\n\nSecond paragraph.",
      "[Earlier reference][ref]\n\nTail\n\n[ref]: https://example.com/path \"Title\"\n",
      "Text\n---\n\n| A | B |\n|---|---|\n| one | two |\n",
      "- first\n- second\n  - nested\n\n  paragraph inside list\n\nAfter list",
      "> quote\n>\n> - item\n> - next\n\nFollowing text\n",
      "```js\nconst a = '<script>';\n```\n\nMore `code` and https://example.com.\n",
      "<script>window.injected = 1</script>\n\n[x](javascript:alert(1))\n\n![hidden](https://example.com/image.png)",
      "Math $x^2$ and \\(y+1\\).\n\n$$\\frac{a}{b}$$\n\nTail math $z$.",
      "[ref] and [other]\n\n[ref]: /first\n\n[other]: /second\n\nEnd",
      "[ref] before list\n\n- item\n\n  [ref]: https://example.com/nested\n",
    ];
    function normalize(element) {
      const clone = element.cloneNode(true);
      clone.querySelectorAll("[data-math]").forEach(node => node.removeAttribute("data-math"));
      return clone.innerHTML;
    }
    let comparisons = 0;
    for (const text of fixtures) {
      for (let split = 0; split <= text.length; split++) {
        const actual = document.createElement("div"), expected = document.createElement("div");
        const update = createStudioMarkdownRenderer(actual);
        for (const part of [text.slice(0, split), text, text.slice(0, split), ""]) {
          update(part); renderStudioMarkdown(expected, part);
          if (normalize(actual) !== normalize(expected)) throw Error("Markdown mismatch at " + split + ": " + JSON.stringify(part));
          comparisons++;
        }
      }
    }
    const body = document.createElement("div"), update = createStudioMarkdownRenderer(body);
    const prefix = "Stable $x^2$.\n\n```js\nconst x = 1;\n```\n\n";
    update(prefix + "Tail");
    const paragraph = body.querySelector("p"), formula = body.querySelector(".katex"), copy = body.querySelector(".code-copy");
    let sanitized = 0;
    const original = DOMPurify.sanitize;
    DOMPurify.sanitize = (...args) => { sanitized++; return original(...args); };
    try {
      for (let index = 0; index < 50; index++) update(prefix + "Tail " + index);
    } finally { DOMPurify.sanitize = original; }
    if (body.querySelector("p") !== paragraph || body.querySelector(".katex") !== formula || body.querySelector(".code-copy") !== copy) {
      throw Error("Stable Markdown blocks were replaced");
    }
    if (sanitized !== 50) throw Error("Expected only 50 tail sanitizations, got " + sanitized);
    const codeBody = document.createElement("div"), codeUpdate = createStudioMarkdownRenderer(codeBody);
    codeUpdate("```js\nconst x = 1;");
    const codeNode = codeBody.querySelector("code"), codeCopy = codeBody.querySelector(".code-copy");
    let codeSanitized = 0;
    DOMPurify.sanitize = (...args) => { codeSanitized++; return original(...args); };
    try {
      for (let index = 0; index < 50; index++) codeUpdate("```js\nconst x = 1;\n" + "// tail\n".repeat(index + 1));
    } finally { DOMPurify.sanitize = original; }
    if (codeSanitized !== 0 || codeNode !== codeBody.querySelector("code") || codeCopy !== codeBody.querySelector(".code-copy")) {
      throw Error("Growing code should update plain text without rebuilding its DOM");
    }
    let copied;
    const originalCopy = window.copyStudioText;
    window.copyStudioText = text => { copied = text; };
    codeCopy.click();
    window.copyStudioText = originalCopy;
    if (copied !== codeNode.textContent) throw Error("Code copy must use the latest streamed content");
    return { comparisons, sanitized, injected: window.injected, images: body.querySelectorAll("img").length };
  });
  assert.ok(result.comparisons > 1000);
  assert.equal(result.sanitized, 50);
  assert.equal(result.injected, undefined);
  console.log(`Markdown: ${result.comparisons} full/incremental comparisons; stable blocks preserved`);
};
