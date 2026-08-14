/**
 * Inline the screenshots into a single self-contained page for sharing.
 *
 *   node demo/build_share_page.mjs
 *
 * Reads  demo/walkthrough.source.html   <- edit this; images stay external so it
 *                                          opens and renders straight from disk
 * Writes demo/fiori-app-walkthrough.html <- every ./shots/*.png embedded as a
 *                                          data URI; one file, no dependencies,
 *                                          safe to attach or drop in SharePoint
 *
 * The only transformation is src="shots/x.png" -> src="data:image/png;base64,…".
 * Keeping it to that means the source file is the single place content lives,
 * and the shared copy cannot drift from what you reviewed.
 */

import { readFileSync, writeFileSync, existsSync, statSync } from "node:fs";
import { resolve, dirname, join } from "node:path";

const SOURCE = "demo/walkthrough.source.html";
const OUTPUT = "demo/fiori-app-walkthrough.html";

if (!existsSync(SOURCE)) {
  console.error(`Source not found: ${SOURCE}`);
  process.exit(1);
}

const srcDir = dirname(resolve(SOURCE));
let html = readFileSync(SOURCE, "utf8");

const MIME = { png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg",
               gif: "image/gif", svg: "image/svg+xml", webp: "image/webp" };

const embedded = [];
const missing = [];

// Only rewrite relative srcs. An absolute URL or an already-inlined data URI is
// left alone so re-running this is harmless.
html = html.replace(
  /src="(?!https?:|data:|\/\/)([^"]+\.(png|jpe?g|gif|svg|webp))"/gi,
  (match, relPath, ext) => {
    const abs = join(srcDir, relPath);
    if (!existsSync(abs)) {
      missing.push(relPath);
      return match;
    }
    const bytes = readFileSync(abs);
    embedded.push({ path: relPath, kb: bytes.length / 1024 });
    const mime = MIME[ext.toLowerCase()] ?? "application/octet-stream";
    return `src="data:${mime};base64,${bytes.toString("base64")}"`;
  },
);

if (missing.length) {
  console.error("Missing image(s) — run `node demo/capture_demo.mjs` first:");
  for (const m of missing) console.error(`  ${m}`);
  process.exit(1);
}

if (!embedded.length) {
  console.error("No relative images found to inline. Nothing written.");
  process.exit(1);
}

writeFileSync(OUTPUT, html, "utf8");

const outKb = statSync(OUTPUT).size / 1024;
console.log(`${OUTPUT}`);
console.log(`  ${embedded.length} image(s) inlined, ${outKb.toFixed(0)} KB total`);
for (const e of embedded) console.log(`    ${e.path}  ${e.kb.toFixed(0)} KB`);
