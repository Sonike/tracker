// node --test tests/site/check-page.test.mjs: the repo checker's page (site/check/index.html) and the part of
// check.mjs that shows and hides its sections. check.mjs needs a browser, so its function is run on stand-in elements.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

const read = (name) => fs.readFileSync(new URL(`../../site/check/${name}`, import.meta.url), "utf8");
const TOOL = "https://github.com/wheelgap/wheelgap";

test("the line under a result links the tool's repo and its GitHub Action, as static text", () => {
  const html = read("index.html");
  const line = html.match(/<p class="note star-line" id="star-line" hidden>(.*?)<\/p>/s);
  assert.ok(line, "the line is in the page, hidden until a result is shown");
  const out = html.slice(html.indexOf('<main class="wrap" id="out" hidden>'), html.indexOf("</main>"));
  assert.ok(out.lastIndexOf("</section>") < out.indexOf('id="star-line"'), "it comes after the result's sections");
  const links = [...line[1].matchAll(/<a href="([^"]+)">(.*?)<\/a>/g)].map((m) => [m[1], m[2]]);
  assert.deepEqual(links, [
    [`${TOOL}/blob/main/docs/ci.md#github-action`, "GitHub Action"],
    [TOOL, '<span aria-hidden="true">★</span> star the project on GitHub'],
  ]);
  assert.match(line[1], /^wheelgap is open source\. /);
  // Plain links: the page's Content-Security-Policy allows no remote images or frames, so no star-count widget.
  assert.doesNotMatch(line[1], /<(img|iframe|script)\b/);
  assert.doesNotMatch(read("check.mjs"), /star-line[^\n]*innerHTML/);
});

test("the line shows with a result (gaps or none) and hides when there is nothing to show", () => {
  const source = read("check.mjs").match(/^function hideResultSections\(.*?^}$/ms);
  assert.ok(source, "hideResultSections is in check.mjs");
  const made = new Map();
  const element = (key) => {
    if (!made.has(key)) made.set(key, { hidden: null, classList: { remove() {} }, closest: () => element(`${key} section`) });
    return made.get(key);
  };
  const hideResultSections = new Function("$", `${source[0]}; return hideResultSections;`)(element);
  hideResultSections(false);        // render(): a result, whether it found gaps or every wheel
  assert.equal(element("star-line").hidden, false);
  assert.equal(element("gaps-title section").hidden, false);
  hideResultSections(true);         // no lockfile found
  assert.equal(element("star-line").hidden, true);
  hideResultSections(true, true);   // a lockfile the browser can't check
  assert.equal(element("star-line").hidden, true);
  assert.equal(element("setup-title section").hidden, false);
});
