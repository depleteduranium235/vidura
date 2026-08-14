/**
 * Capture a walkthrough of the running Fiori app for sharing.
 *
 * Screenshots the real app driven by the real mock service — no mock-ups, no
 * replicas. Uses the installed Edge via playwright-core.
 *
 *   node demo/capture_demo.mjs [baseUrl]
 *
 * Writes demo/shots/NN-*.png and prints a manifest the packager consumes.
 */

import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";

const base = process.argv[2] ?? "http://localhost:8082";
const url = `${base}/test/standalone.html`;
const dir = "demo/shots";
mkdirSync(dir, { recursive: true });

const shots = [];
const browser = await chromium.launch({ channel: "msedge", headless: true });
const page = await browser.newPage({
  viewport: { width: 1920, height: 1080 },
  deviceScaleFactor: 2,
});

const settle = (ms = 2500) => page.waitForTimeout(ms);

async function shot(name, caption) {
  const file = `${dir}/${name}.png`;
  await page.screenshot({ path: file });
  shots.push({ file, caption });
  console.error(`  captured ${file}`);
}

/** Click an Object Page anchor-bar tab by its visible label. */
async function openSection(label) {
  const tab = page.locator(".sapUxAPAnchorBarButton", { hasText: label }).first();
  if (await tab.count()) {
    await tab.click();
    await settle(2000);
    return true;
  }
  return false;
}

/** Open a case from the worklist by its case ID. */
async function openCase(caseId) {
  const row = page.locator('[role="row"]', { hasText: caseId }).first();
  await row.locator(".sapMLnk, a").first().click({ timeout: 10_000 }).catch(async () => {
    await row.click({ timeout: 10_000 });
  });
  await settle(5000);
}

await page.goto(url, { waitUntil: "networkidle", timeout: 60_000 });
await settle(7000);
await page.waitForSelector('[role="columnheader"]', { timeout: 30_000 });

await shot("01-worklist",
  "The worklist. Every blocked partner-to-entry pair the agent has assessed, with " +
  "its proposed disposition, status and priority. The tabs are saved views — " +
  "Escalated, Ready to Clear, High Value at Risk, Aged Over 5 Days.");

// A confirmed-looking match: the agent escalates rather than clears.
await openCase("ADJ-2026-000003");
await shot("02-case-escalate",
  "A case the agent escalated. Petrochemical Supplies FZE was blocked against " +
  "PETRO SUPPLIES TRADING FZE. The header carries the disposition, and the " +
  "rationale explains the reasoning in full — including what would change the " +
  "assessment.");

if (await openSection("Evidence Ledger")) {
  await shot("03-evidence-ledger",
    "The evidence ledger for that case: every element compared, each classified " +
    "on a fixed scale from dispositive exclusion to dispositive confirmation, " +
    "each with a written justification. The disposition is derived from these " +
    "rows by deterministic rules — the model never emits a score.");
}

// The everyday case: a false positive on a very common surname.
await page.goBack();
await settle(5000);
await openCase("ADJ-2026-000002");
await shot("04-case-propose-clear",
  "The everyday case. Schmidt & Weber GmbH was blocked against an individual " +
  "named SCHMIDT — one of the commonest surnames in Germany. The agent proposes " +
  "clearing it and documents why, so a reviewer confirms a decision instead of " +
  "reconstructing it.");

if (await openSection("Evidence Ledger")) {
  await shot("05-evidence-ledger-clear",
    "Its ledger. Entity type is a dispositive exclusion: the listing is a natural " +
    "person, the partner is a registered company. Note the unavailable rows — " +
    "missing data is recorded as neutral and can never clear a hit on its own.");
}

console.log(JSON.stringify({ shots }, null, 2));
await browser.close();
