/**
 * Assemble a fully static, fully interactive build of the Fiori app.
 *
 *   node demo/build_static_demo.mjs
 *
 * Output: demo/static-demo/  — a folder of plain files. No Node, no mock server,
 * no SAP system. Serve it from anything that serves files over https (or from
 * http://localhost) and the real app runs, with real filtering, sorting, search,
 * saved views and drill-down, against demo data.
 *
 * Two changes are made to the app for this build, and only two:
 *
 *   1. dataSources.mainService.uri becomes the relative path "odata/".
 *      The dev config uses an absolute /sap/opu/odata4/... path, which would
 *      resolve to the host root and fall outside the Service Worker's scope when
 *      the demo is hosted in a subfolder. Relative keeps it inside scope, so the
 *      bundle works at any URL depth.
 *
 *   2. index.html bootstraps SAPUI5 from ui5.sap.com and registers the worker
 *      before starting the component, instead of loading /resources/ from the
 *      ui5 tooling dev server.
 *
 * Everything else — Component.js, the annotations in metadata.xml, the mock data,
 * i18n — is copied verbatim, so this is the same app, not a reimplementation.
 */

import { readFileSync, writeFileSync, mkdirSync, cpSync, rmSync, existsSync, statSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";

const ROOT = process.cwd();
const WEBAPP = join(ROOT, "webapp");
const SRC = join(ROOT, "demo", "static-src");
const OUT = join(ROOT, "demo", "static-demo");

const SERVICE_URI = "odata/";

if (!existsSync(WEBAPP)) {
  console.error("Run this from the project root (webapp/ not found).");
  process.exit(1);
}

rmSync(OUT, { recursive: true, force: true });
mkdirSync(OUT, { recursive: true });

// ---------------------------------------------------------------- app files
for (const entry of ["Component.js", "i18n", "localService", "annotations", "ext"]) {
  const from = join(WEBAPP, entry);
  if (existsSync(from)) cpSync(from, join(OUT, entry), { recursive: true });
}

// ------------------------------------------------------------ manifest.json
const manifest = JSON.parse(readFileSync(join(WEBAPP, "manifest.json"), "utf8"));

const ds = manifest["sap.app"].dataSources.mainService;
const originalUri = ds.uri;
ds.uri = SERVICE_URI;

// flexEnabled drives requests to /sap/bc/lrep/... which 404 on a static host.
// Harmless (UI5 continues) but it puts red errors in the console of a demo, so
// turn variant persistence off for this build.
manifest["sap.ui5"].flexEnabled = false;

writeFileSync(join(OUT, "manifest.json"), JSON.stringify(manifest, null, 2), "utf8");

// ------------------------------------------------------- index.html + worker
cpSync(join(SRC, "index.html"), join(OUT, "index.html"));
cpSync(join(SRC, "sw.js"), join(OUT, "sw.js"));

// The worker's marker must match the service URI actually used.
let sw = readFileSync(join(OUT, "sw.js"), "utf8");
sw = sw.replace('const SERVICE_MARKER = "/odata/";',
                `const SERVICE_MARKER = "/${SERVICE_URI.replace(/\/$/, "")}/";`);
writeFileSync(join(OUT, "sw.js"), sw, "utf8");

// ------------------------------------------------------------------- README
writeFileSync(join(OUT, "README.txt"), `SPL Hit Adjudication - interactive demo
=======================================

The real Fiori application, running on demo data. Filtering, sorting, search,
the saved views and the drill-down to the evidence ledger all work.

There is no server and no SAP system behind it: an in-browser Service Worker
answers the OData V4 requests from the JSON files in localService/mockdata.

IMPORTANT - it must be served over http(s), not opened from disk
---------------------------------------------------------------
Service Workers are blocked on file:// URLs, so double-clicking index.html
will show an explanatory message instead of the app.

To run it locally, from inside this folder:

    npx serve .                     (then open the URL it prints)
  or
    python -m http.server 8080      (then open http://localhost:8080)

To host it, upload this whole folder to any static https host and open
index.html. It works at any path depth. It needs internet access for the
SAPUI5 runtime, which loads from https://ui5.sap.com.

Known-unsuitable host: SharePoint document libraries generally serve HTML as a
download rather than running it, and block Service Workers. Use a proper static
web host instead.

The data is illustrative - invented business partners and SPL entries, not real
screening results.
`, "utf8");

// -------------------------------------------------------------------- report
function walk(dir) {
  let files = 0, bytes = 0;
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) { const r = walk(p); files += r.files; bytes += r.bytes; }
    else { files++; bytes += statSync(p).size; }
  }
  return { files, bytes };
}
const { files, bytes } = walk(OUT);

console.log("demo/static-demo/");
console.log(`  ${files} files, ${(bytes / 1024).toFixed(0)} KB`);
console.log(`  service uri : ${originalUri}`);
console.log(`             -> ${SERVICE_URI}  (relative, inside the worker scope)`);
console.log(`  flexEnabled : ${manifest["sap.ui5"].flexEnabled}`);
console.log("\nServe it (Service Workers need http, not file://):");
console.log("  cd demo/static-demo && npx serve .");
