/**
 * Prove the static build is genuinely interactive, not just rendering.
 *
 * Each check exercises something that can only work if the in-browser OData
 * service is really answering queries: switching a saved view must change the
 * row set ($filter), sorting must reorder it ($orderby), search must narrow it,
 * and opening a case must load its evidence ledger ($expand).
 *
 *   node demo/verify_static_demo.mjs [baseUrl]
 */

import { chromium } from "playwright-core";

const base = process.argv[2] ?? "http://127.0.0.1:8090";
const results = [];
const errors = [];

const browser = await chromium.launch({ channel: "msedge", headless: true });
const context = await browser.newContext({
  viewport: { width: 1920, height: 1080 },
  deviceScaleFactor: 2,
});
const page = await context.newPage();

page.on("pageerror", (e) => errors.push(`PAGEERROR ${e.message.slice(0, 200)}`));
page.on("console", (m) => {
  if (m.type() !== "error") return;
  const t = m.text();
  // Variant persistence is switched off in this build; ignore any residue.
  if (/lrep|flex/i.test(t)) return;
  errors.push(t.slice(0, 200));
});
page.on("requestfailed", (r) => errors.push(`REQFAIL ${r.url().slice(-70)}`));
page.on("response", (r) => {
  if (r.status() >= 400) errors.push(`HTTP ${r.status()} ${r.url().slice(-70)}`);
});

const check = (name, pass, detail) => {
  results.push({ name, pass, detail });
  console.log(`  ${pass ? "PASS" : "FAIL"}  ${name}${detail ? " — " + detail : ""}`);
};

async function rowIds() {
  return page.evaluate(() => Array.from(document.querySelectorAll('[role="row"]'))
    .map((r) => (r.innerText || "").match(/ADJ-\d{4}-\d{6}/)?.[0])
    .filter(Boolean));
}

async function clickTab(label) {
  const tab = page.locator(".sapMITBFilter, .sapMITBItem, .sapMITBText",
    { hasText: new RegExp(`^${label}$`) }).first();
  if (!(await tab.count())) return false;
  await tab.click({ force: true });
  await page.waitForTimeout(3000);
  return true;
}

// ---------------------------------------------------------------- boot
await page.goto(base + "/", { waitUntil: "load", timeout: 60_000 });
await page.waitForTimeout(3000);
// A cold first visit may reload once while the worker takes control.
await page.waitForSelector('[role="columnheader"]', { timeout: 60_000 }).catch(() => {});
await page.waitForTimeout(3000);

const controlled = await page.evaluate(() => !!navigator.serviceWorker.controller);
check("Service Worker controls the page", controlled);

const headers = await page.evaluate(() => Array.from(
  document.querySelectorAll('[role="columnheader"]')).map((h) => h.innerText.trim()).filter(Boolean));
check("List Report rendered", headers.length >= 5, `${headers.length} columns`);

const all = await rowIds();
check("All 5 demo cases loaded from the in-browser service",
  all.length === 5, all.join(", ") || "none");

const tabs = await page.evaluate(() => Array.from(
  document.querySelectorAll(".sapMITBText, .sapMITBFilterText")).map((t) => t.innerText.trim()));
check("Saved views present", tabs.length >= 5, tabs.join(" | "));

// ------------------------------------------------- $filter via a saved view
if (await clickTab("Escalated")) {
  const rows = await rowIds();
  const only3 = rows.length === 3;
  check("$filter works — 'Escalated' narrows 5 rows to 3", only3, rows.join(", "));
} else {
  check("$filter works — 'Escalated' tab", false, "tab not found");
}

if (await clickTab("Ready to Clear")) {
  const rows = await rowIds();
  check("$filter works — 'Ready to Clear' returns the Propose Clear case",
    rows.length === 1 && rows[0] === "ADJ-2026-000002", rows.join(", "));
}

if (await clickTab("High Value at Risk")) {
  const rows = await rowIds();
  check("$filter works — 'High Value at Risk' filters on OrderValue",
    rows.length >= 1 && rows.length < 5, rows.join(", "));
}

await clickTab("All Cases");
const backToAll = await rowIds();
check("Returning to 'All Cases' restores every row", backToAll.length === 5,
  `${backToAll.length} rows`);

// ------------------------------------------------------ $orderby via sorting
const before = await rowIds();
const bpHeader = page.locator('[role="columnheader"]', { hasText: "Business Partner" }).first();
if (await bpHeader.count()) {
  await bpHeader.click({ force: true });
  await page.waitForTimeout(1200);
  const sortAsc = page.locator(".sapMPopover .sapMLIB, .sapMMenu li",
    { hasText: /Sort Ascending|ascending/i }).first();
  if (await sortAsc.count()) { await sortAsc.click(); await page.waitForTimeout(3000); }
  const after = await rowIds();
  check("$orderby works — sorting reorders the rows",
    after.length === before.length && after.join() !== before.join(),
    `${before.join(",")}  ->  ${after.join(",")}`);
} else {
  check("$orderby works — sorting", false, "column header not found");
}

// ----------------------------------------------------------------- $search
const search = page.locator('input[type="search"], .sapMSFI input').first();
if (await search.count()) {
  await search.fill("Volga");
  await search.press("Enter");
  await page.waitForTimeout(3500);
  const rows = await rowIds();
  check("$search works — searching 'Volga' narrows the list",
    rows.length === 1 && rows[0] === "ADJ-2026-000005", rows.join(", "));
  await search.fill("");
  await search.press("Enter");
  await page.waitForTimeout(3000);
} else {
  check("$search works", false, "search field not found");
}

// ------------------------------------------- $expand via the evidence ledger
const target = page.locator('[role="row"]', { hasText: "ADJ-2026-000002" }).first();
if (await target.count()) {
  await target.locator(".sapMLnk, a").first().click({ timeout: 10_000 })
    .catch(() => target.click({ timeout: 10_000 }));
  await page.waitForTimeout(6000);

  const op = await page.evaluate(() => ({
    title: document.querySelector(".sapUxAPObjectPageHeaderTitle .sapMTitle, .sapUxAPObjectPageHeaderTitleText")?.innerText?.trim()
      || document.body.innerText.match(/ADJ-\d{4}-\d{6}/)?.[0],
    sections: Array.from(document.querySelectorAll(".sapUxAPAnchorBarButton"))
      .map((b) => b.innerText.trim()).filter(Boolean),
    hasRationale: /registered German limited liability company|Rationale/i.test(document.body.innerText),
  }));
  check("Drill-down works — Object Page opened", /ADJ-2026-000002/.test(op.title || ""), op.title);
  check("Object Page sections rendered", op.sections.length >= 5, op.sections.join(" | "));

  const ledgerTab = page.locator(".sapUxAPAnchorBarButton", { hasText: "Evidence Ledger" }).first();
  if (await ledgerTab.count()) {
    await ledgerTab.click();
    await page.waitForTimeout(3000);
  }
  const ledger = await page.evaluate(() => {
    const txt = document.body.innerText;
    return {
      categories: ["Dispositive Exclusion", "Strong Discriminator", "Weak Discriminator", "Neutral"]
        .filter((c) => txt.includes(c)),
      rows: Array.from(document.querySelectorAll('[role="row"]'))
        .filter((r) => /Dispositive|Discriminator|Neutral|Corroborator/.test(r.innerText || "")).length,
    };
  });
  check("$expand works — evidence ledger loaded with its classifications",
    ledger.rows >= 5 && ledger.categories.length >= 3,
    `${ledger.rows} rows, categories: ${ledger.categories.join(", ")}`);

  await page.screenshot({ path: "demo/shots/static-object.png" });
  await page.goBack();
  await page.waitForTimeout(4000);
} else {
  check("Drill-down works", false, "target row not found");
}

await page.screenshot({ path: "demo/shots/static-list.png" });

// --------------------------------------------------------- reload resilience
await page.reload({ waitUntil: "load" });
await page.waitForTimeout(6000);
const afterReload = await rowIds();
check("Survives a reload (worker already installed)", afterReload.length === 5,
  `${afterReload.length} rows`);

console.log("\n" + "=".repeat(70));
const failed = results.filter((r) => !r.pass);
console.log(`${results.length - failed.length}/${results.length} checks passed`);
console.log("unexpected console/network errors:", errors.length ? [...new Set(errors)] : "none");
await browser.close();
process.exit(failed.length ? 1 : 0);
