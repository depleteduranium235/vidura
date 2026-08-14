/**
 * Load the running Fiori app in a real browser, report what actually rendered,
 * and capture screenshots.
 *
 * Fiori Elements builds the UI at runtime from annotations, so a bad
 * annotationPath produces a blank page or a silently missing column that no
 * server-side check can catch. This is the only way to know the app works.
 *
 * Drives the installed Edge via playwright-core, so nothing is downloaded.
 *
 *   node demo/verify_app.mjs <url> <out-prefix> [--shot-only]
 */

import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";
import { dirname } from "node:path";

const url = process.argv[2] ?? "http://localhost:8082/test/standalone.html";
const prefix = process.argv[3] ?? "demo/shots/app";
const shotOnly = process.argv.includes("--shot-only");

mkdirSync(dirname(prefix), { recursive: true });

const errors = [];
const failedRequests = [];

const widthArg = process.argv.find((a) => a.startsWith("--width="));
const width = widthArg ? Number(widthArg.split("=")[1]) : 1680;

const browser = await chromium.launch({ channel: "msedge", headless: true });
const context = await browser.newContext({
  viewport: { width, height: 1020 },
  deviceScaleFactor: 2,
});
const page = await context.newPage();

page.on("console", (m) => {
  if (m.type() === "error") errors.push(m.text().slice(0, 300));
});
page.on("pageerror", (e) => errors.push(`PAGEERROR ${e.message.slice(0, 300)}`));
page.on("requestfailed", (r) =>
  failedRequests.push(`${r.failure()?.errorText ?? "failed"} ${r.url().slice(0, 140)}`));
page.on("response", (r) => {
  if (r.status() >= 400) failedRequests.push(`HTTP ${r.status()} ${r.url().slice(0, 140)}`);
});

await page.goto(url, { waitUntil: "networkidle", timeout: 60_000 });

// sap.fe compiles templates after the model resolves; give it room, then settle.
await page.waitForTimeout(6000);
try {
  await page.waitForSelector('[role="columnheader"], .sapMMessagePage, .sapMMessageBox', {
    timeout: 20_000,
  });
} catch {
  /* fall through — the diagnostics below say what happened */
}
await page.waitForTimeout(1500);

const report = await page.evaluate(() => {
  const text = (el) => (el?.innerText ?? "").trim().replace(/\s+/g, " ");
  const all = (sel) => Array.from(document.querySelectorAll(sel));

  return {
    title: document.title,
    columnHeaders: all('[role="columnheader"]').map(text).filter(Boolean),
    // Multi-view List Report renders its variants as an IconTabBar.
    viewTabs: all(".sapMITBText, .sapMITBFilterText").map(text).filter(Boolean),
    dataRows: all('[role="row"]').filter((r) =>
      r.querySelector('[role="gridcell"], td')).length,
    // A blank canvas or an error page is the classic annotation failure.
    messagePage: text(document.querySelector(".sapMMessagePage")) || null,
    errorDialogs: all(".sapMMessageBox, .sapMDialog").map(text).filter(Boolean),
    bodyTextLength: (document.body.innerText || "").trim().length,
    firstRowCells: all('[role="row"]')
      .map((r) => Array.from(r.querySelectorAll('[role="gridcell"], td')).map(text))
      .find((cells) => cells.length > 0) ?? [],
  };
});

await page.screenshot({ path: `${prefix}-list.png`, fullPage: false });

// Drill into the first row to prove the object page and evidence ledger render.
let objectPage = null;
if (!shotOnly && report.dataRows > 0) {
  try {
    const firstLink = page.locator('[role="row"] a, [role="row"] .sapMLnk').first();
    if (await firstLink.count()) {
      await firstLink.click({ timeout: 10_000 });
    } else {
      await page.locator('[role="row"]').nth(1).click({ timeout: 10_000 });
    }
    await page.waitForTimeout(6000);
    objectPage = await page.evaluate(() => {
      const text = (el) => (el?.innerText ?? "").trim().replace(/\s+/g, " ");
      const all = (sel) => Array.from(document.querySelectorAll(sel));
      return {
        url: location.hash || location.pathname,
        headerTitle: text(document.querySelector(".sapUxAPObjectPageHeaderTitle, .sapMTitle")),
        sections: all(".sapUxAPObjectPageSectionTitle, .sapUxAPAnchorBarButton")
          .map(text).filter(Boolean),
        ledgerHeaders: all('[role="columnheader"]').map(text).filter(Boolean),
        ledgerRows: all('[role="row"]').filter((r) =>
          r.querySelector('[role="gridcell"], td')).length,
      };
    });
    await page.screenshot({ path: `${prefix}-object.png`, fullPage: false });
  } catch (e) {
    objectPage = { error: String(e).slice(0, 200) };
  }
}

console.log(JSON.stringify({
  url,
  report,
  objectPage,
  consoleErrors: [...new Set(errors)],
  failedRequests: [...new Set(failedRequests)],
  screenshots: [`${prefix}-list.png`, objectPage && !objectPage.error ? `${prefix}-object.png` : null]
    .filter(Boolean),
}, null, 2));

await browser.close();
