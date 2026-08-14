/* eslint-env serviceworker */
/**
 * OData V4 mock, in the browser.
 *
 * The Fiori app needs a live OData V4 service: it issues real $filter, $orderby,
 * $select, $expand, $top/$skip and $count, and wraps them in multipart $batch.
 * A static file host cannot answer that — query strings don't select a file. So
 * the service runs here instead, in a Service Worker, and the whole demo becomes
 * a folder of static files with no backend.
 *
 * Scope covers the app, and only requests under the service path are intercepted;
 * everything else falls through to the network untouched.
 *
 * Supports the subset sap.fe actually emits (captured from the running app):
 *   GET  $metadata
 *   GET  AdjudicationCase        + $filter $orderby $select $top $skip $count $expand $search
 *   GET  AdjudicationCase(<key>) + $select $expand
 *   GET  EvidenceItem            + the same options
 *   POST $batch                  multipart, read-only (no changesets)
 *
 * Unparseable filters fail open — all rows are returned and a warning is logged,
 * so an unfamiliar expression degrades to "no filter" instead of an empty table.
 */

const SERVICE_MARKER = "/odata/";
const CASES_FILE = "localService/mockdata/AdjudicationCase.json";
const EVIDENCE_FILE = "localService/mockdata/EvidenceItem.json";
const METADATA_FILE = "localService/metadata.xml";

const CASE_SET = "AdjudicationCase";
const EVIDENCE_SET = "EvidenceItem";
const CASE_KEY = "CaseUUID";
const EVIDENCE_KEY = "EvidenceUUID";
const EVIDENCE_NAV = "_EvidenceItems";

let store = null;

self.addEventListener("install", (e) => {
  e.waitUntil(loadStore().then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(self.clients.claim());
});

async function loadStore() {
  if (store) return store;
  const base = new URL("./", self.location).href;
  const [cases, evidence, metadata] = await Promise.all([
    fetch(base + CASES_FILE).then((r) => r.json()),
    fetch(base + EVIDENCE_FILE).then((r) => r.json()),
    fetch(base + METADATA_FILE).then((r) => r.text()),
  ]);
  store = { [CASE_SET]: cases, [EVIDENCE_SET]: evidence, metadata };
  return store;
}

/* ----------------------------------------------------------------- $filter */

/**
 * Tokenise and evaluate an OData V4 filter expression.
 *
 * Recursive descent over: or > and > comparison > primary, where primary is a
 * parenthesised expression or a function call. Deliberately small — it covers
 * what the saved views and the filter bar produce, not the whole grammar.
 */
function tokenize(expr) {
  const re = /\s*(\(|\)|,|'(?:[^']|'')*'|[A-Za-z_][A-Za-z0-9_./]*|-?\d+(?:\.\d+)?)\s*/g;
  const out = [];
  let m;
  while ((m = re.exec(expr)) !== null) {
    if (m[1] === undefined) break;
    out.push(m[1]);
  }
  return out;
}

const LOGICAL = new Set(["and", "or"]);
const COMPARISON = new Set(["eq", "ne", "gt", "ge", "lt", "le"]);
const FUNCTIONS = new Set(["contains", "startswith", "endswith", "tolower", "toupper"]);

function parseFilter(expr) {
  const tokens = tokenize(expr);
  let i = 0;

  const peek = () => tokens[i];
  const next = () => tokens[i++];

  function parseOr() {
    let left = parseAnd();
    while (peek() && peek().toLowerCase() === "or") {
      next();
      const right = parseAnd();
      const l = left, r = right;
      left = (row) => l(row) || r(row);
    }
    return left;
  }

  function parseAnd() {
    let left = parseComparison();
    while (peek() && peek().toLowerCase() === "and") {
      next();
      const right = parseComparison();
      const l = left, r = right;
      left = (row) => l(row) && r(row);
    }
    return left;
  }

  function parseComparison() {
    const left = parseOperand();
    const op = peek();
    if (op && COMPARISON.has(op.toLowerCase())) {
      next();
      const right = parseOperand();
      const o = op.toLowerCase();
      return (row) => compare(left(row), right(row), o);
    }
    // A bare function call such as contains(...) is already boolean.
    return (row) => Boolean(left(row));
  }

  /** Returns a function of row -> value. */
  function parseOperand() {
    const t = next();
    if (t === undefined) return () => undefined;

    if (t === "(") {
      const inner = parseOr();
      if (peek() === ")") next();
      return inner;
    }

    if (FUNCTIONS.has(t.toLowerCase()) && peek() === "(") {
      next(); // (
      const args = [];
      while (peek() && peek() !== ")") {
        args.push(parseOperand());
        if (peek() === ",") next();
      }
      if (peek() === ")") next();
      return makeFunction(t.toLowerCase(), args);
    }

    if (t.startsWith("'")) {
      const literal = t.slice(1, -1).replace(/''/g, "'");
      return () => literal;
    }

    if (/^-?\d+(\.\d+)?$/.test(t)) {
      const num = Number(t);
      return () => num;
    }

    if (t === "true") return () => true;
    if (t === "false") return () => false;
    if (t === "null") return () => null;

    // Otherwise a property path.
    return (row) => resolvePath(row, t);
  }

  const fn = parseOr();
  return fn;
}

function makeFunction(name, args) {
  const str = (v) => (v === null || v === undefined ? "" : String(v));
  switch (name) {
    case "contains":
      return (row) => str(args[0](row)).toLowerCase().includes(str(args[1](row)).toLowerCase());
    case "startswith":
      return (row) => str(args[0](row)).toLowerCase().startsWith(str(args[1](row)).toLowerCase());
    case "endswith":
      return (row) => str(args[0](row)).toLowerCase().endsWith(str(args[1](row)).toLowerCase());
    case "tolower":
      return (row) => str(args[0](row)).toLowerCase();
    case "toupper":
      return (row) => str(args[0](row)).toUpperCase();
    default:
      return () => true;
  }
}

function resolvePath(row, path) {
  return path.split("/").reduce((acc, part) => (acc == null ? acc : acc[part]), row);
}

function compare(a, b, op) {
  // Missing on either side never satisfies an equality-style test except ne.
  if (a === undefined || a === null) {
    if (op === "eq") return b === null || b === undefined || b === "";
    if (op === "ne") return !(b === null || b === undefined || b === "");
    return false;
  }
  let x = a, y = b;
  if (typeof x === "boolean" || typeof y === "boolean") { x = !!x; y = !!y; }
  else if (typeof x === "number" || typeof y === "number") { x = Number(x); y = Number(y); }
  else { x = String(x); y = String(y); }

  switch (op) {
    case "eq": return x === y;
    case "ne": return x !== y;
    case "gt": return x > y;
    case "ge": return x >= y;
    case "lt": return x < y;
    case "le": return x <= y;
    default: return true;
  }
}

function applyFilter(rows, expr) {
  if (!expr) return rows;
  try {
    const pred = parseFilter(expr);
    return rows.filter((r) => {
      try { return pred(r); } catch { return true; }
    });
  } catch (err) {
    console.warn("[odata-mock] could not parse $filter, returning all rows:", expr, err);
    return rows;
  }
}

/* ---------------------------------------------------- $orderby / $select etc */

function applyOrderby(rows, expr) {
  if (!expr) return rows;
  const terms = expr.split(",").map((t) => {
    const [field, dir] = t.trim().split(/\s+/);
    return { field, desc: (dir || "").toLowerCase() === "desc" };
  });
  return [...rows].sort((a, b) => {
    for (const { field, desc } of terms) {
      let x = resolvePath(a, field), y = resolvePath(b, field);
      if (x === undefined || x === null) x = "";
      if (y === undefined || y === null) y = "";
      let c;
      if (typeof x === "number" || typeof y === "number") c = Number(x) - Number(y);
      else c = String(x).localeCompare(String(y));
      if (c !== 0) return desc ? -c : c;
    }
    return 0;
  });
}

function applySelect(row, select) {
  if (!select) return { ...row };
  const fields = select.split(",").map((f) => f.trim()).filter(Boolean);
  if (fields.includes("*")) return { ...row };
  const out = {};
  for (const f of fields) if (f in row) out[f] = row[f];
  return out;
}

/** Parse $expand=_EvidenceItems($select=a,b;$orderby=c) into its inner options. */
function parseExpand(expand) {
  if (!expand) return null;
  const m = expand.match(/^([A-Za-z_][A-Za-z0-9_]*)\s*(?:\((.*)\))?$/s);
  if (!m) return null;
  const inner = {};
  if (m[2]) {
    for (const part of splitTopLevel(m[2], ";")) {
      const eq = part.indexOf("=");
      if (eq > 0) inner[part.slice(0, eq).trim()] = part.slice(eq + 1).trim();
    }
  }
  return { nav: m[1], options: inner };
}

/** Split on a separator that is not inside parentheses or quotes. */
function splitTopLevel(str, sep) {
  const out = [];
  let depth = 0, quoted = false, cur = "";
  for (const ch of str) {
    if (ch === "'") quoted = !quoted;
    if (!quoted && ch === "(") depth++;
    if (!quoted && ch === ")") depth--;
    if (!quoted && depth === 0 && ch === sep) { out.push(cur); cur = ""; continue; }
    cur += ch;
  }
  if (cur.trim()) out.push(cur);
  return out;
}

/* ------------------------------------------------------------- request handling */

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json;odata.metadata=minimal;charset=utf-8",
      "OData-Version": "4.0",
      "Cache-Control": "no-store",
    },
  });
}

function attachExpand(row, expandSpec) {
  if (!expandSpec || expandSpec.nav !== EVIDENCE_NAV) return row;
  let children = store[EVIDENCE_SET].filter((e) => e[CASE_KEY] === row[CASE_KEY]);
  const o = expandSpec.options || {};
  children = applyFilter(children, o.$filter);
  children = applyOrderby(children, o.$orderby || "SortOrder");
  row[EVIDENCE_NAV] = children.map((c) => applySelect(c, o.$select));
  return row;
}

/**
 * Answer one OData request described by a path + search params.
 * Shared by direct GETs and by the inner requests of a $batch.
 */
function handleOData(pathAfterService, params) {
  const path = pathAfterService.replace(/^\/+/, "");

  if (path === "$metadata" || path.startsWith("$metadata")) {
    return new Response(store.metadata, {
      status: 200,
      headers: { "Content-Type": "application/xml;charset=utf-8", "OData-Version": "4.0" },
    });
  }

  if (path === "" || path === "/") {
    return jsonResponse({
      "@odata.context": "$metadata",
      value: [
        { name: CASE_SET, kind: "EntitySet", url: CASE_SET },
        { name: EVIDENCE_SET, kind: "EntitySet", url: EVIDENCE_SET },
      ],
    });
  }

  // Keyed access: AdjudicationCase(<key>) with optional trailing segments:
  //   AdjudicationCase(<key>)                                  -> single entity
  //   AdjudicationCase(<key>)/                                 -> single entity
  //   AdjudicationCase(<key>)/_EvidenceItems                   -> navigation collection
  //   AdjudicationCase(<key>)/zpwc.gts.spl_adjudication.action -> bound action
  const keyedMatch = path.match(/^([A-Za-z_][A-Za-z0-9_]*)\(([^)]+)\)\/?(.*)$/);
  if (keyedMatch) {
    const set = keyedMatch[1];
    const tail = keyedMatch[3]; // everything after the closing )/ — may be empty
    if (!store[set]) return jsonResponse({ error: { message: `Unknown set ${set}` } }, 404);

    let key = keyedMatch[2].trim();
    key = key.replace(/^[A-Za-z_][A-Za-z0-9_]*\s*=\s*/, "").replace(/^guid'|'$/g, "").replace(/^'|'$/g, "");
    const keyField = set === CASE_SET ? CASE_KEY : EVIDENCE_KEY;
    const found = store[set].find((r) => String(r[keyField]).toLowerCase() === key.toLowerCase());
    if (!found) {
      return jsonResponse({ error: { code: "404", message: "Not Found" } }, 404);
    }

    // Bound actions: .../zpwc.gts.spl_adjudication.confirmRelease
    const actionSuffix = tail.match(/^zpwc\.gts\.spl_adjudication\.(\w+)$/);
    if (actionSuffix) {
      return handleAction(set, keyedMatch[2], actionSuffix[1], params);
    }

    // Navigation property: .../_EvidenceItems
    if (tail === EVIDENCE_NAV || tail === `${EVIDENCE_NAV}/`) {
      let children = store[EVIDENCE_SET].filter((e) => e[CASE_KEY] === found[CASE_KEY]);
      children = applyFilter(children, params.get("$filter"));
      children = applyOrderby(children, params.get("$orderby") || "SortOrder");
      const total = children.length;
      const skip = Number(params.get("$skip") || 0);
      const top = params.get("$top") ? Number(params.get("$top")) : undefined;
      children = children.slice(skip, top === undefined ? undefined : skip + top);
      const select = params.get("$select");
      const value = children.map((c) => applySelect(c, select));
      const body = { "@odata.context": `$metadata#${EVIDENCE_SET}` };
      if (params.get("$count") === "true") body["@odata.count"] = total;
      body.value = value;
      return jsonResponse(body);
    }

    // Plain keyed access (no tail, or just a trailing slash)
    let row = applySelect(found, params.get("$select"));
    row[keyField] = found[keyField];
    row = attachExpand(row, parseExpand(params.get("$expand")));
    return jsonResponse({ "@odata.context": `$metadata#${set}/$entity`, ...row });
  }

  // Collection access
  const set = path.split("/")[0];
  if (!store[set]) return jsonResponse({ error: { message: `Unknown set ${set}` } }, 404);

  let rows = store[set];
  rows = applyFilter(rows, params.get("$filter"));

  // Free-text $search across the string fields.
  const search = params.get("$search");
  if (search) {
    const needle = search.replace(/^"|"$/g, "").toLowerCase();
    rows = rows.filter((r) => Object.values(r).some(
      (v) => typeof v === "string" && v.toLowerCase().includes(needle)));
  }

  const total = rows.length;
  rows = applyOrderby(rows, params.get("$orderby"));

  const skip = Number(params.get("$skip") || 0);
  const top = params.get("$top") ? Number(params.get("$top")) : undefined;
  rows = rows.slice(skip, top === undefined ? undefined : skip + top);

  const select = params.get("$select");
  const expandSpec = parseExpand(params.get("$expand"));
  const keyField = set === CASE_SET ? CASE_KEY : EVIDENCE_KEY;

  const value = rows.map((r) => {
    let row = applySelect(r, select);
    row[keyField] = r[keyField];
    return attachExpand(row, expandSpec);
  });

  const body = { "@odata.context": `$metadata#${set}` };
  if (params.get("$count") === "true") body["@odata.count"] = total;
  body.value = value;
  return jsonResponse(body);
}

/* --------------------------------------------------------------- actions */

const ACTIONS = {
  confirmRelease: (row, body) => {
    row.HumanDecision = "Confirmed";
    row.HumanUser = "demo.reviewer@pwc.com";
    row.HumanComment = body?.Comment || "";
    row.DecisionTimestamp = new Date().toISOString();
    row.Status = "Released";
    row.StatusCriticality = 3; // Positive (green)
    row.ChangedAt = new Date().toISOString();
  },
  rejectRelease: (row, body) => {
    row.HumanDecision = "Rejected";
    row.HumanUser = "demo.reviewer@pwc.com";
    row.HumanComment = body?.Comment || "";
    row.DecisionTimestamp = new Date().toISOString();
    row.Status = "Rejected";
    row.StatusCriticality = 1; // Negative (red)
    row.ChangedAt = new Date().toISOString();
  },
  escalateCase: (row, body) => {
    row.HumanDecision = "Escalated";
    row.HumanUser = "demo.reviewer@pwc.com";
    row.HumanComment = body?.Comment || "";
    row.AssignedTo = body?.EscalateTo || "senior.reviewer@pwc.com";
    row.DecisionTimestamp = new Date().toISOString();
    row.Status = "Escalated";
    row.StatusCriticality = 2; // Critical (orange)
    row.Priority = "Critical";
    row.PriorityCriticality = 1;
    row.ChangedAt = new Date().toISOString();
  },
};

function handleAction(setName, rawKey, actionName, params) {
  if (!ACTIONS[actionName]) {
    return jsonResponse({ error: { message: `Unknown action: ${actionName}` } }, 400);
  }
  if (!store[setName]) {
    return jsonResponse({ error: { message: `Unknown set: ${setName}` } }, 404);
  }

  let key = rawKey.trim().replace(/^[A-Za-z_][A-Za-z0-9_]*\s*=\s*/, "")
    .replace(/^guid'|'$/g, "").replace(/^'|'$/g, "");
  const keyField = setName === CASE_SET ? CASE_KEY : EVIDENCE_KEY;
  const row = store[setName].find((r) => String(r[keyField]).toLowerCase() === key.toLowerCase());

  if (!row) return jsonResponse({ error: { code: "404", message: "Not Found" } }, 404);

  // The body carries the action parameters (Comment, EscalateTo, etc.)
  // For GET-style dispatch from $batch, body may already be parsed.
  ACTIONS[actionName](row, params.__actionBody || {});

  return jsonResponse({ "@odata.context": `$metadata#${setName}/$entity`, ...row });
}

/* ----------------------------------------------------------------- $batch */

async function handleBatch(request, serviceBase) {
  const contentType = request.headers.get("Content-Type") || "";
  const boundaryMatch = contentType.match(/boundary=("?)([^";]+)\1/i);
  const text = await request.text();

  // JSON batch is also legal in V4; handle it if it turns up.
  if (contentType.includes("application/json")) {
    const payload = JSON.parse(text);
    const responses = [];
    for (const req of payload.requests || []) {
      const res = await dispatchInner(req.method || "GET", req.url, serviceBase,
        req.body ? JSON.stringify(req.body) : null);
      responses.push({
        id: req.id,
        status: res.status,
        headers: { "content-type": res.headers.get("Content-Type") },
        body: JSON.parse(await res.text() || "null"),
      });
    }
    return jsonResponse({ responses });
  }

  if (!boundaryMatch) return jsonResponse({ error: { message: "No batch boundary" } }, 400);
  const boundary = boundaryMatch[2];
  const respBoundary = `batchresponse_${boundary}`;
  const parts = text.split(`--${boundary}`).filter(
    (p) => p.trim() && !p.trim().startsWith("--"));

  const chunks = [];
  for (const part of parts) {
    // Each part: part headers, blank line, request line + headers, blank, body.
    const requestLine = part.match(/^\s*(GET|POST|PATCH|PUT|DELETE|HEAD)\s+(\S+)/m);
    if (!requestLine) continue;
    // Extract body from the part: everything after the double blank line
    // (the first blank separates part-headers from the HTTP message, the second
    // separates HTTP headers from the body).
    const bodyMatch = part.split(/\r?\n\r?\n/);
    const innerBody = bodyMatch.length >= 3 ? bodyMatch.slice(2).join("\n\n").trim() : null;
    const res = await dispatchInner(requestLine[1], requestLine[2], serviceBase, innerBody);
    const body = await res.text();
    chunks.push(
      `--${respBoundary}\r\n` +
      "Content-Type: application/http\r\n" +
      "Content-Transfer-Encoding: binary\r\n\r\n" +
      `HTTP/1.1 ${res.status} ${res.status === 200 ? "OK" : "Error"}\r\n` +
      `Content-Type: ${res.headers.get("Content-Type")}\r\n` +
      "OData-Version: 4.0\r\n" +
      `Content-Length: ${new TextEncoder().encode(body).length}\r\n\r\n` +
      `${body}\r\n`,
    );
  }
  chunks.push(`--${respBoundary}--\r\n`);

  return new Response(chunks.join(""), {
    status: 200,
    headers: {
      "Content-Type": `multipart/mixed;boundary=${respBoundary}`,
      "OData-Version": "4.0",
      "Cache-Control": "no-store",
    },
  });
}

function dispatchInner(method, rawUrl, serviceBase, body) {
  // Inner URLs are relative to the batch's service root.
  const url = new URL(rawUrl, serviceBase);
  let tail = url.pathname;
  const idx = tail.indexOf(SERVICE_MARKER);
  if (idx >= 0) tail = tail.slice(idx + SERVICE_MARKER.length);
  else tail = tail.replace(/^\//, "");
  if (method === "HEAD") return Promise.resolve(new Response(null, { status: 200 }));
  // Pass any POST body to handleOData so bound actions can read their parameters.
  const params = url.searchParams;
  if (body && method === "POST") {
    try { params.__actionBody = JSON.parse(body); } catch { params.__actionBody = {}; }
  }
  return Promise.resolve(handleOData(tail, params));
}

/* ------------------------------------------------------------------- fetch */

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  const idx = url.pathname.indexOf(SERVICE_MARKER);
  if (idx < 0 || url.origin !== self.location.origin) return; // not ours

  const serviceBase = url.origin + url.pathname.slice(0, idx + SERVICE_MARKER.length);
  const tail = url.pathname.slice(idx + SERVICE_MARKER.length);

  event.respondWith((async () => {
    try {
      await loadStore();
      if (event.request.method === "HEAD") return new Response(null, { status: 200 });
      if (tail.replace(/^\/+/, "") === "$batch" || event.request.method === "POST") {
        return await handleBatch(event.request, serviceBase);
      }
      return handleOData(tail, url.searchParams);
    } catch (err) {
      console.error("[odata-mock] failed:", err);
      return jsonResponse({ error: { code: "500", message: String(err) } }, 500);
    }
  })());
});
