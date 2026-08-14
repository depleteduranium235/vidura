/**
 * Log every OData request the running Fiori app makes.
 *
 * A browser-side mock only has to answer the requests sap.fe actually issues, so
 * this enumerates them rather than guessing: initial load, each saved view, the
 * filter bar, and the drill-down to the object page with its expand.
 *
 *   node demo/capture_odata.mjs [baseUrl]
 */

import { chromium } from "playwright-core";

const base = process.argv[2] ?? "http://localhost:8082";
const MARKER = "/sap/opu/odata4/";
const seen = [];

const browser = await chromium.launch({ channel: "msedge", headless: true });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });

page.on("request", (r) => {
  const u = r.url();
  if (!u.includes(MARKER)) return;
  const tail = u.split("/0001")[1] ?? u;
  seen.push({ method: r.method(), path: decodeURIComponent(tail), postData: r.postData() });
});

await page.goto(`${base}/test/standalone.html`, { waitUntil: "networkidle", timeout: 60_000 });
await page.waitForTimeout(8000);
await page.waitForSelector('[role="columnheader"]', { timeout: 30_000 });

const mark = (label) => seen.push({ method: "--", path: `### ${label}`, postData: null });

mark("after initial load");

for (const tab of ["Escalated", "Ready to Clear", "High Value at Risk"]) {
  const t = page.locator(".sapMITBFilter, .sapMITBItem", { hasText: tab }).first();
  if (await t.count()) {
    await t.click();
    await page.waitForTimeout(3500);
    mark(`after switching to "${tab}"`);
  }
}

// Sort by a column to see $orderby change.
const header = page.locator('[role="columnheader"]', { hasText: "Business Partner" }).first();
if (await header.count()) {
  await header.click().catch(() => {});
  await page.waitForTimeout(2500);
  mark("after clicking a column header");
}

// Drill into a case: object page + $expand of the evidence ledger.
const row = page.locator('[role="row"]').nth(1);
await row.locator(".sapMLnk, a").first().click({ timeout: 10_000 })
  .catch(() => row.click({ timeout: 10_000 }));
await page.waitForTimeout(7000);
mark("after opening a case");

const distinct = [];
const keys = new Set();
for (const s of seen) {
  const k = `${s.method} ${s.path}`;
  if (s.method === "--") { distinct.push(s); continue; }
  if (keys.has(k)) continue;
  keys.add(k);
  distinct.push(s);
}

console.log("=".repeat(78));
for (const s of distinct) {
  if (s.method === "--") { console.log(`\n${s.path}`); continue; }
  console.log(`  ${s.method} ${s.path}`);
  if (s.postData) console.log(`      body: ${s.postData.slice(0, 400)}`);
}
console.log("\n" + "=".repeat(78));
console.log("uses $batch      :", seen.some((s) => s.path.includes("$batch")));
console.log("distinct requests:", keys.size);
console.log("query options    :", [...new Set(
  seen.flatMap((s) => [...s.path.matchAll(/[?&](\$?[a-zA-Z]+)=/g)].map((m) => m[1])),
)].join(", "));

await browser.close();
