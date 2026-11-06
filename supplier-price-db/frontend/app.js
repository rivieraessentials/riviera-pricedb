/* Riviera Price DB — frontend (Step B).
 *
 * A faithful port of the prototype's seven screens onto the FastAPI backend. Differences
 * from the prototype are all forced by a decision (noted in the design/build-plan docs):
 *   - every byte of data lives on the SERVER (the API), never in localStorage;
 *   - NO CRM button / link anywhere;
 *   - prices are per-piece and 2dp (the server rounds; per-box is divided by pcs/box first);
 *   - brand is chosen in the upload form (single, or multi matched by EAN) — never read from
 *     a brand column, so the prototype's brand-approval step is gone;
 *   - customer requests store only EAN + target + currency (+ optional company/brand); the
 *     uploaded file is never kept and the result is recomputed live, never stored;
 *   - offer type is the structured Regular/Stock choice; a stock offer can be ended.
 *
 * SECURITY: no inline event handlers and no inline <script> exist (CSP forbids them); all
 * wiring is event delegation below. EVERY value that originates from a supplier file or the
 * user — descriptions, brand names, supplier names, notes — is written with esc()/textContent,
 * never as raw HTML, so a description like `<img src=x onerror=...>` renders as literal text.
 * localStorage is used ONLY for the grid zoom level (a per-viewer display preference).
 */
"use strict";

// ---------- escaping (the one defence that must never be skipped) ----------
function esc(v) {
  if (v === null || v === undefined) return "";
  return String(v)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// ---------- API client (same-origin JSON; the server checks Origin + Content-Type) ----------
async function api(method, path, body) {
  const opts = { method, headers: {}, credentials: "same-origin" };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  if (r.status === 204) return null;
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = text; }
  if (!r.ok) {
    const detail = (data && data.detail) ? (typeof data.detail === "string" ? data.detail
                   : JSON.stringify(data.detail)) : ("HTTP " + r.status);
    throw new Error(detail);
  }
  return data;
}
const apiGet = (p) => api("GET", p);

// ---------- display helpers ----------
const symBase = { EUR: "€", GBP: "£", USD: "$", SGD: "S$", CAD: "C$", HKD: "HK$" };
function sym(c) { return symBase[c] || (c + " "); }
function rateOf(cur) {
  if (cur === "EUR") return 1;
  const r = parseFloat((store.fx.rates || {})[cur]);
  return isFinite(r) && r > 0 ? r : 1;
}
function conv(eur) { return (parseFloat(eur) || 0) * rateOf(display); }
function fmt(eur) {
  if (eur === null || eur === undefined || eur === "") return "—";
  return sym(display) + conv(eur).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(String(iso).length <= 10 ? iso + "T00:00:00" : iso);
  if (isNaN(d)) return esc(iso);
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
}

// ---------- parsing helpers (ported from the prototype; brand column intentionally unused) ----------
function toNum(v) {
  if (v == null || v === "") return null;
  const n = parseFloat(String(v).replace(/[^0-9.,-]/g, "").replace(",", "."));
  return isNaN(n) ? null : n;
}
function eanCanon(v) {
  if (v == null) return null;
  let s = String(v).trim().replace(/\s|-/g, "");
  if (s.endsWith(".0")) s = s.slice(0, -2);
  if (!/^\d+$/.test(s)) return null;
  if (s.length === 12) s = "0" + s;
  return s;
}
function eanValid(code) {
  if (!code || !/^\d+$/.test(code) || (code.length !== 13 && code.length !== 8)) return false;
  const d = [...code].map(Number), chk = d.pop(); let s = 0;
  if (code.length === 13) d.forEach((x, i) => s += x * (i % 2 === 0 ? 1 : 3));
  else d.forEach((x, i) => s += x * (i % 2 === 0 ? 3 : 1));
  return (10 - s % 10) % 10 === chk;
}
function looksEan(v) { const c = eanCanon(v); return !!(c && (c.length === 8 || c.length === 12 || c.length === 13)); }
// A size is a number followed by a unit (100 ml, 15 Gr, 6 x 50ml, "20 ml + Stick 2 g"); the
// product description is mainly text. A value is a size when, after dropping numbers, punctuation
// and known unit words, nothing is left — so the size column is never mistaken for the description.
// a unit token not glued to other letters (so "200ml" and "50g" count, but "Gel"/"Lait" don't).
const SIZE_UNITS = /(?<![a-zà-ÿ])(ml|cl|dl|mg|kg|grs|gr|oz|lb|pcs|pces|pce|pc|caps|cap|tabs|tab|sticks|stick|st|pz|pezzi|pieces?|pièces?|stuck|stück|unidades?|un|g|l|u|x)(?![a-zà-ÿ])/gi;
function looksSize(v) {
  const s = (v == null ? "" : String(v)).trim();
  if (!s || !/\d/.test(s)) return false;                 // a size always carries a number
  const rest = s.toLowerCase().replace(SIZE_UNITS, " ").replace(/[\d.,+x×*/&()[\]\s-]/g, "");
  return rest.length === 0;                              // only numbers + units were present
}
// description-like: real text (a run of letters), and not a bare size value
function isTexty(v) { const s = (v == null ? "" : String(v)).trim(); return !!s && !looksSize(v) && /[A-Za-zÀ-ÿ]{3,}/.test(s); }
// Choose the product-description column: the one with the most text (never size/EAN/price columns).
function pickDescCol(sample, ncol, exclude) {
  let bc = -1, bn = -1;
  for (let c = 0; c < ncol; c++) {
    if (exclude.includes(c)) continue;
    let n = 0; sample.forEach(r => { if (isTexty(r && r[c])) n++; });
    if (n > bn) { bn = n; bc = c; }
  }
  return bn >= Math.max(1, sample.length * 0.3) ? bc : -1;
}
// true when the column we already picked for description is actually full of sizes
function colIsMostlySize(sample, c) {
  let sz = 0, tot = 0;
  sample.forEach(r => { const v = r && r[c]; if (v != null && String(v).trim()) { tot++; if (looksSize(v)) sz++; } });
  return tot > 0 && sz >= tot * 0.6;
}
function detectCols(hdr) {
  const L = hdr.map(c => (c == null ? "" : String(c)).trim().toLowerCase());
  let ean = -1, desc = -1, size = -1, ppb = -1; const prices = [];
  const inc = (l, ...n) => n.some(x => l.includes(x));
  L.forEach((l, i) => { if (ean < 0 && l && inc(l, "ean", "upc", "gtin", "barcode", "product code", "product-code", "cod. a barre", "codice a barre") || (ean < 0 && (l === "code" || l === "ref" || l === "reference" || l === "référence" || l === "product code"))) ean = i; });
  const descGroups = [["description", "descrizione", "designation", "désignation", "libell"], ["name", "nom"], ["sku", "article", "denomin", "item", "label"], ["product"]];
  for (const grp of descGroups) { if (desc >= 0) break; L.forEach((l, i) => { if (desc < 0 && i !== ean && l && !l.includes("code") && grp.some(x => l === x || l.includes(x))) desc = i; }); }
  L.forEach((l, i) => { if (size < 0 && l && (l === "size" || l === "ml" || l === "taille" || inc(l, "pack size", "format", "volume", "capacity"))) size = i; });
  L.forEach((l, i) => { if (ppb < 0 && l && inc(l, "case pack", "prepack", "outer pack", "pcs/box", "pcs / box", "per box", "colisage", "units/box", "pieces per", "qty/box", "pcb")) ppb = i; });
  let qty = -1;
  L.forEach((l, i) => { if (qty < 0 && i !== ean && l && (l === "qty" || l === "quantity" || l === "stock" || l === "q.ty" || inc(l, "available qty", "qty available", "available quantity", "in stock", "disponib", "dispo", "stock qty", "quantité", "quantity available"))) qty = i; });
  const PK = ["price", "eur", "usd", "gbp", "sgd", "cad", "hkd", "srp", "euro", "prix", "cost", "wholesale", "retail", "pvp", "net", "prezzo", "preis"];
  const EX = ["total", "order", "qty", "quantity", "stock", "available", "moq", "tariff", "code", "pack", "pieces", "pcs", "margin", "discount", "vat"];
  L.forEach((l, i) => { if (i === ean || i === qty) return; if (l && PK.some(k => l.includes(k)) && !EX.some(x => l.includes(x))) prices.push(i); });
  // brand column (used only for multi-brand imports; absent on many files)
  let brand = -1;
  L.forEach((l, i) => { if (brand < 0 && i !== ean && i !== desc && l && (l === "brand" || l === "marque" || l === "marca" || l === "marke" || l === "marchio" || l === "brand name" || inc(l, "brand name"))) brand = i; });
  return { ean, desc, size, ppb, qty, brand, prices };
}
function parseSheetRows(rows) {
  if (!rows.length) return { rows: [], bad: 0, ok: false };
  let hi = 0, bestScore = -1, cols = null;
  for (let i = 0; i < Math.min(rows.length, 40); i++) { const c = detectCols(rows[i] || []); const sc = (c.ean >= 0 ? 2 : 0) + (c.prices.length ? 1 : 0) + (c.desc >= 0 ? 1 : 0); if (sc > bestScore) { bestScore = sc; hi = i; cols = c; } }
  const hasHeader = bestScore > 0;
  if (!hasHeader) { hi = 0; cols = { ean: -1, desc: -1, size: -1, ppb: -1, qty: -1, brand: -1, prices: [] }; }
  cols = cols || { ean: -1, desc: -1, size: -1, ppb: -1, qty: -1, brand: -1, prices: [] };
  const dataStart = hasHeader ? hi + 1 : 0;
  const ncol = Math.max(0, ...rows.slice(hi, hi + 60).map(r => r ? r.length : 0));
  const sample = rows.slice(dataStart, dataStart + 60).filter(r => r && r.some(x => x != null && x !== ""));
  if (cols.ean < 0) { let bc = -1, bn = -1; for (let c = 0; c < ncol; c++) { let n = 0; sample.forEach(r => { if (looksEan(r[c])) n++; }); if (n > bn) { bn = n; bc = c; } } if (bn >= Math.max(1, sample.length * 0.3)) cols.ean = bc; }
  if (!cols.prices.length) { const cand = []; for (let c = 0; c < ncol; c++) { if (c === cols.ean) continue; let num = 0, dec = 0; sample.forEach(r => { const v = toNum(r[c]); if (v != null && v > 0) { num++; if (/\d[.,]\d/.test(String(r[c]))) dec++; } }); if (num >= Math.max(2, sample.length * 0.4) && dec >= 1) cand.push(c); } cols.prices = cand; }
  { const byText = pickDescCol(sample, ncol, [cols.ean, cols.qty, ...cols.prices]);
    // use the text column when the header gave none, or when the header's pick is really sizes
    if (cols.desc < 0) cols.desc = byText;
    else if (byText >= 0 && byText !== cols.desc && colIsMostlySize(sample, cols.desc)) cols.desc = byText; }
  if (cols.ean < 0 || !cols.prices.length) return { rows: [], bad: 0, ok: false };
  const out = []; let bad = 0;
  for (let r = dataStart; r < rows.length; r++) {
    const row = rows[r] || []; const code = eanCanon(row[cols.ean]); const ps = cols.prices.map(i => toNum(row[i])).filter(v => v != null && v > 0);
    if (!code && !ps.length) continue; if (!code || !ps.length) { bad++; continue; }
    out.push({ ean: code, desc: cols.desc >= 0 && row[cols.desc] != null ? String(row[cols.desc]).trim() : "",
      ppb: cols.ppb >= 0 && toNum(row[cols.ppb]) > 0 ? Math.round(toNum(row[cols.ppb])) : null,
      brand: cols.brand >= 0 && row[cols.brand] != null ? String(row[cols.brand]).trim() : "",
      price: Math.round(Math.min(...ps) * 100) / 100, ok: eanValid(code) });
  }
  return { rows: out, bad, ok: true };
}
function parseWorkbook(buf) {
  const wb = XLSX.read(buf, { type: "array" }); let all = [], bad = 0, sheets = 0;
  wb.SheetNames.forEach(n => { const rows = XLSX.utils.sheet_to_json(wb.Sheets[n], { header: 1, raw: true }); const r = parseSheetRows(rows); if (r.ok) { all = all.concat(r.rows); bad += r.bad; sheets++; } });
  return { rows: all, bad, sheets };
}
// Customer-request file: an EAN column and an OPTIONAL price column. The target comes only from
// a detected price column (never stray numbers in a description), so an EAN+description file with
// no price yields EAN-only lines (target = null).
function parseRequestWorkbook(buf) {
  const wb = XLSX.read(buf, { type: "array" });
  const out = [];
  wb.SheetNames.forEach(n => {
    const rows = XLSX.utils.sheet_to_json(wb.Sheets[n], { header: 1, raw: true });
    if (!rows.length) return;
    let hi = 0, bestScore = -1, cols = null;
    for (let i = 0; i < Math.min(rows.length, 40); i++) { const c = detectCols(rows[i] || []); const sc = (c.ean >= 0 ? 2 : 0) + (c.prices.length ? 1 : 0); if (sc > bestScore) { bestScore = sc; hi = i; cols = c; } }
    const hasHeader = bestScore > 0;
    cols = cols || { ean: -1, prices: [] };
    const dataStart = hasHeader ? hi + 1 : 0;
    const sample = rows.slice(dataStart, dataStart + 60).filter(r => r && r.some(x => x != null && x !== ""));
    const ncol = Math.max(0, ...rows.slice(hi, hi + 60).map(r => r ? r.length : 0));
    if (cols.ean < 0) { let bc = -1, bn = -1; for (let c = 0; c < ncol; c++) { let nn = 0; sample.forEach(r => { if (looksEan(r[c])) nn++; }); if (nn > bn) { bn = nn; bc = c; } } if (bn >= Math.max(1, sample.length * 0.3)) cols.ean = bc; }
    if (cols.ean < 0) return;
    // description column: real text, never the size column (a number + ml/l/g)
    { const byText = pickDescCol(sample, ncol, [cols.ean, cols.qty, ...(cols.prices || [])]);
      if (cols.desc < 0) cols.desc = byText;
      else if (byText >= 0 && byText !== cols.desc && colIsMostlySize(sample, cols.desc)) cols.desc = byText; }
    for (let r = dataStart; r < rows.length; r++) {
      const row = rows[r] || []; const code = eanCanon(row[cols.ean]);
      if (!code || (code.length !== 13 && code.length !== 8)) continue;
      const ps = (cols.prices || []).map(i => toNum(row[i])).filter(v => v != null && v > 0);
      const desc = cols.desc >= 0 && row[cols.desc] != null ? String(row[cols.desc]).trim() : "";
      out.push({ ean: code, target: ps.length ? Math.round(Math.min(...ps) * 100) / 100 : null, desc });
    }
  });
  return out;
}

// ---------- state + store ----------
let display = "EUR", view = "rankings", activeBrand = null, bsBrand = null;
let sortCol = null, sortDir = 1, searchQ = "", terrFilter = "all", curFilter = "all", cmpA = "", cmpB = "";
let reqDescriptions = {};   // EAN -> description from the last uploaded request file (not persisted client-side)
let reqBrand = "All", reqCust = "All", lastResult = null, brandLetter = null;
const colOrder = {};   // in-memory per-brand column order (ephemeral; not persisted)
let _gridRows = {}, _curCols = [];
let gridZoom = (() => { try { return parseFloat(localStorage.getItem("pdbZoom")) || 1; } catch (e) { return 1; } })();
const store = { brands: [], suppliers: [], fx: { rate_date: "", rates: {} }, customers: [], currencies: ["EUR"] };
let _pendingFile = null, _pendingBytes = null, _focusId = null, _confirmCb = null, _brandApproveResolve = null, _importGuardResolve = null;
const _comboOptions = {};   // {inputId: [names]} for the searchable comboboxes

function applyZoom() { const r = document.documentElement.style; r.setProperty("--gridfs", (11 * gridZoom).toFixed(1) + "px"); r.setProperty("--gridpad", (5 * gridZoom).toFixed(1) + "px"); r.setProperty("--ean-w", Math.round(102 * gridZoom) + "px"); r.setProperty("--prod-w", Math.round(170 * gridZoom) + "px"); }
function setZoom(d) { gridZoom = Math.min(1.5, Math.max(0.6, Math.round((gridZoom + d * 0.1) * 10) / 10)); try { localStorage.setItem("pdbZoom", gridZoom); } catch (e) {} applyZoom(); render(); }
function brandList() { return store.brands.slice().sort((a, b) => a.name.localeCompare(b.name)); }
function letterOf(n) { const ch = (n || "#").trim().charAt(0).toUpperCase(); return /[A-Z]/.test(ch) ? ch : "#"; }
function curOrder() { return ["EUR", ...Object.keys(store.fx.rates || {}).filter(c => c !== "EUR")]; }

async function loadStore() {
  const [brands, suppliers, fx, customers, currencies] = await Promise.all([
    apiGet("/brands"), apiGet("/suppliers"), apiGet("/fx/rates"), apiGet("/customers"), apiGet("/currencies"),
  ]);
  store.brands = brands || []; store.suppliers = suppliers || [];
  store.fx = fx || { rate_date: "", rates: {} }; store.customers = customers || [];
  store.currencies = (currencies && currencies.currencies && currencies.currencies.length) ? currencies.currencies : ["EUR"];
}

// ---------- currency selector + nav ----------
function buildCurSelect() {
  const s = document.getElementById("cur"); s.innerHTML = "";
  curOrder().forEach(c => { const o = document.createElement("option"); o.value = c; o.textContent = c + (c === "EUR" ? " (base)" : ""); s.appendChild(o); });
  s.value = display;
}
function setSort(col) { if (sortCol === col) sortDir = -sortDir; else { sortCol = col; sortDir = -1; } render(); }
function sortArrow(col) { return sortCol === col ? (sortDir > 0 ? " ▲" : " ▼") : ' <span class="muted" style="opacity:.4">↕</span>'; }
function queueFocus(id) { _focusId = id; }

const TITLES = { rankings: "Offers", bestsellers: "Bestsellers", requests: "Customer requests", suppliers: "Suppliers", import: "Import log", review: "Review queue", settings: "Currency & FX" };
const VIEWS = {};

async function render() {
  document.getElementById("ptitle").textContent = TITLES[view];
  document.getElementById("foot-stats").textContent = store.brands.length + " brands · " + store.suppliers.length + " suppliers";
  buildCurSelect(); applyZoom();
  try { const rv = await apiGet("/review"); document.getElementById("nav-review").textContent = (rv || []).length; } catch (e) {}
  const c = document.getElementById("content"); c.innerHTML = '<div class="muted" style="padding:20px">Loading…</div>';
  try { await VIEWS[view](c); } catch (e) { c.innerHTML = '<div class="alert alert-amber">Could not load: ' + esc(e.message) + "</div>"; }
  if (_focusId) { const el = document.getElementById(_focusId); if (el) { el.focus(); try { const n = el.value.length; el.setSelectionRange(n, n); } catch (e) {} } _focusId = null; }
}

// ======================= OFFERS =======================
VIEWS.rankings = async function (c, _retried) {
  const UNK = "__unknown__";
  let brands = brandList();
  if (activeBrand !== UNK && (!activeBrand || !brands.some(b => b.name === activeBrand)))
    activeBrand = brands.length ? brands[0].name : UNK;
  const unknown = activeBrand === UNK;
  const terr = terrFilter !== "all" ? "&territory=" + terrFilter : "";
  const ccy = curFilter !== "all" ? "&currency=" + encodeURIComponent(curFilter) : "";
  let data;
  try {
    data = await apiGet((unknown ? "/grid?unknown=1" : "/grid?brand=" + encodeURIComponent(activeBrand)) + terr + ccy);
  } catch (e) {
    // the active brand may have disappeared (e.g. the data was reset in another session).
    // refresh the store and fall back to a brand that still exists (or Unknown), then re-render.
    if (_retried) throw e;
    await loadStore();
    brands = brandList();
    activeBrand = brands.length ? brands[0].name : UNK;
    brandLetter = null; cmpA = cmpB = ""; sortCol = null;
    return VIEWS.rankings(c, true);
  }
  _gridRows = {}; data.rows.forEach(r => { _gridRows[r.ean] = r; });
  if (curFilter !== "all" && !(data.currencies || []).includes(curFilter)) { curFilter = "all"; return VIEWS.rankings(c, _retried); }
  let bs = data.batches.slice();
  if (colOrder[activeBrand]) { const ord = colOrder[activeBrand]; bs.sort((a, b) => (ord.indexOf(a.batch_id) < 0 ? 999 : ord.indexOf(a.batch_id)) - (ord.indexOf(b.batch_id) < 0 ? 999 : ord.indexOf(b.batch_id))); }
  const showDiff = cmpA && cmpB && bs.some(b => b.batch_id == cmpA) && bs.some(b => b.batch_id == cmpB);

  let h = toolbarHTML(data.currencies || []);
  // Compact A–Z index: a letter bar, expanding into that letter's brands when clicked.
  const letters = [...new Set(brands.map(b => letterOf(b.name)))].sort();
  if (!unknown && activeBrand && brandLetter === null) brandLetter = letterOf(activeBrand);
  if (brandLetter === null || !letters.includes(brandLetter)) brandLetter = letters[0] || null;
  const letterBrands = brands.filter(b => letterOf(b.name) === brandLetter);
  h += '<div class="pills">' + letters.map(L => `<div class="pill ${L === brandLetter && !unknown ? "active" : ""}" data-act="brandletter" data-letter="${L}" style="min-width:32px;text-align:center;padding:6px 9px;font-weight:700">${L}</div>`).join("") +
    `<div class="pill ${unknown ? "active" : ""}" data-act="brand" data-brand="${UNK}" style="margin-left:6px" title="products whose brand isn't known yet — they move to a brand's tab when a branded offer for that EAN is imported">❓ Unknown</div>` + "</div>";
  if (letterBrands.length) h += '<div class="pills">' + letterBrands.map(b => `<div class="pill ${b.name === activeBrand ? "active" : ""}" data-act="brand" data-brand="${esc(b.name)}">${esc(b.name)}</div>`).join("") + "</div>";
  h += `<div class="cmprow"><b>Compare →</b> tick the ☆ in two column headers to show a Δ% column. ${(cmpA || cmpB) ? '<button class="sm" data-act="cmpclear">clear</button>' : '<span class="muted">(then the grid narrows to those two)</span>'}</div>`;

  let prods = data.rows.slice();
  if (searchQ.trim()) { const q = searchQ.toLowerCase(); prods = prods.filter(p => p.ean.includes(q) || (p.description || "").toLowerCase().includes(q)); }
  const priceOf = (row, bid) => (row.prices[bid] != null ? parseFloat(row.prices[bid]) : null);
  if (showDiff && !sortCol) { const pctOf = (p) => { const a = priceOf(p, cmpA), b = priceOf(p, cmpB); return (a != null && b != null && a > 0) ? (b - a) / a * 100 : -1e9; }; prods.sort((a, b) => pctOf(b) - pctOf(a)); }
  else if (!sortCol) prods.sort((a, b) => (a.description || "~").localeCompare(b.description || "~"));
  else { const BIG = 1e18; const val = (p) => { if (sortCol === "ean") return parseFloat(p.ean) || 0; if (sortCol === "prod") return (p.description || "").toLowerCase(); if (sortCol === "best") { const m = p.best_eur == null ? null : parseFloat(p.best_eur); return m == null ? -BIG : m; } if (sortCol[0] === "b") { const v = priceOf(p, +sortCol.slice(1)); return v == null ? -BIG : v; } return 0; }; prods = prods.slice().sort((a, b) => { const x = val(a), y = val(b); return (x < y ? -1 : x > y ? 1 : 0) * sortDir; }); }

  let cols = bs; if (showDiff) cols = [bs.find(b => b.batch_id == cmpA), bs.find(b => b.batch_id == cmpB)];
  _curCols = bs.map(b => b.batch_id);
  if (!prods.length) {
    c.innerHTML = h + `<div class="empty"><div class="ei">▦</div><div>${unknown
      ? "No unknown-brand products. Multi-brand imports drop any EAN with no known brand here; it moves to a brand's tab once a branded offer for that EAN is imported."
      : "No products in this tab" + (searchQ ? " match your search." : " yet.")}</div></div>`;
    return;
  }
  h += `<div class="muted" style="margin-bottom:8px">${prods.length} products · live offers only · ◀▶ move a column · ☆ compares · click a supplier header for its price list · click a product to edit.</div>`;
  h += '<div class="grid-wrap"><table class="grid"><thead><tr><th class="c-star"></th>' +
    `<th class="c-ean lx" style="cursor:pointer" data-act="sort" data-col="ean">EAN${sortArrow("ean")}</th>` +
    `<th class="c-prod lx" style="cursor:pointer" data-act="sort" data-col="prod">Product${sortArrow("prod")}</th>` +
    (showDiff ? "" : `<th style="cursor:pointer" data-act="sort" data-col="best">Best ${esc(display)}${sortArrow("best")}</th>`) +
    cols.map(b => {
      const sel = b.batch_id == cmpA ? "a" : b.batch_id == cmpB ? "b" : ""; const tag = sel === "a" ? " colA" : sel === "b" ? " colB" : "";
      const cstar = `<span class="cmpstar ${sel}" title="tick to compare" data-act="cmp" data-bid="${b.batch_id}">${sel ? "★" : "☆"}</span>`;
      const arrows = showDiff ? "" : `<span class="hbtn" title="move left" data-act="movecol" data-bid="${b.batch_id}" data-dir="-1">◀</span><span class="hbtn" title="move right" data-act="movecol" data-bid="${b.batch_id}" data-dir="1">▶</span>`;
      const ended = b.ended_at ? ' <span class="badge badge-gray" style="padding:0 6px">ended</span>' : "";
      return `<th class="suphdr" style="cursor:pointer" title="Click for offer details" data-act="offer" data-bid="${b.batch_id}">
        <div class="s1">${cstar}${arrows}<span data-act="sort" data-col="b${b.batch_id}" title="sort" style="cursor:pointer">${sortArrow("b" + b.batch_id)}</span> ${esc(b.supplier)}</div>
        <div class="s2">${esc(fmtDate(b.received_date))} · ${esc(b.kind_label || b.kind || "")}${ended}</div></th>`;
    }).join("") +
    (showDiff ? '<th class="diffcell">Δ% (B vs A)</th>' : "") + "</tr></thead><tbody>";

  prods.forEach(p => {
    const best = p.best_eur == null ? null : parseFloat(p.best_eur);
    h += `<tr class="${p.is_bestseller ? "best-row" : ""}">
      <td class="c-star"><span class="star ${p.is_bestseller ? "on" : ""}" data-act="best" data-ean="${esc(p.ean)}">${p.is_bestseller ? "★" : "☆"}</span></td>
      <td class="c-ean lx">${esc(p.ean)}</td>
      <td class="c-prod lx" title="click to edit" data-act="prodedit" data-ean="${esc(p.ean)}">${p.description ? esc(p.description) : '<span class="muted">⚠ no description</span>'}</td>`;
    if (!showDiff) h += `<td class="best-euro">${best != null ? fmt(best) : "—"}</td>`;
    cols.forEach(b => { const v = priceOf(p, b.batch_id); const tag = b.batch_id == cmpA ? " colA" : b.batch_id == cmpB ? " colB" : "";
      if (v == null) { h += `<td class="pricecell${tag}"><span class="dlt">—</span></td>`; return; }
      const o = p.orig ? p.orig[b.batch_id] : null;
      const origLine = (o != null && b.currency && b.currency !== "EUR")
        ? `<div class="muted" style="font-weight:400">${esc(sym(b.currency))}${(parseFloat(o)).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>` : "";
      h += `<td class="pricecell ${(b.batch_id == p.best_batch_id && !showDiff) ? "best" : ""}${tag}">${fmt(v)}${origLine}</td>`; });
    if (showDiff) { const a = priceOf(p, cmpA), b = priceOf(p, cmpB);
      if (a != null && b != null && a > 0) { const pct = (b - a) / a * 100; h += `<td class="diffcell ${pct > 0 ? "up" : "dn"}">${pct > 0 ? "+" : ""}${pct.toFixed(1)}%</td>`; }
      else h += '<td class="diffcell"><span class="dlt">—</span></td>'; }
    h += "</tr>";
  });
  h += "</tbody></table></div>"; c.innerHTML = h;
};
function toolbarHTML(currencies) {
  currencies = currencies || [];
  const showCcy = currencies.length && !(currencies.length === 1 && currencies[0] === "EUR");
  const ccySeg = showCcy
    ? `<span class="seg" title="filter by currency"><button class="${curFilter === "all" ? "on" : ""}" data-act="curf" data-cur="all">All ccy</button>` +
      currencies.map(c => `<button class="${curFilter === c ? "on" : ""}" data-act="curf" data-cur="${esc(c)}">${esc(c)}</button>`).join("") + "</span>"
    : "";
  return `<div class="toolbar" style="justify-content:center">
    <button class="primary" data-act="addfile" data-drop="offer" title="click, or drag an .xlsx onto this button">⬆ Add file <span class="muted" style="font-weight:400;font-size:11px">or drop here</span></button>
    <button data-act="addline">＋ Add line</button>
    <input id="offSearch" placeholder="🔍 search EAN or description…" value="${esc(searchQ)}" style="width:260px" data-act="search"/>
    <span class="seg"><button class="${terrFilter === "all" ? "on" : ""}" data-act="terr" data-terr="all">All</button>
      <button class="${terrFilter === "T2" ? "on" : ""}" data-act="terr" data-terr="T2">T2</button>
      <button class="${terrFilter === "T1" ? "on" : ""}" data-act="terr" data-terr="T1">T1</button></span>
    ${ccySeg}
    <span class="seg" title="zoom the table"><button data-act="zoom" data-dir="-1">A−</button><button data-act="zoom" data-dir="1">A+</button></span></div>`;
}
function wireNothing() {}

// ======================= BESTSELLERS =======================
VIEWS.bestsellers = async function (c) {
  if (bsBrand === null) bsBrand = "All";
  const q = bsBrand !== "All" ? "?brand=" + encodeURIComponent(bsBrand) : "";
  const best = await apiGet("/bestsellers" + q);
  let h = '<div class="alert alert-blue">Flag bestsellers with ★ in Offers. Pick a brand — the <b>3 cheapest live suppliers</b> for each, low→high.</div>';
  h += '<div class="pills"><div class="pill ' + (bsBrand === "All" ? "active" : "") + '" data-act="bsbrand" data-brand="All">All brands</div>' +
    brandList().map(b => `<div class="pill ${bsBrand === b.name ? "active" : ""}" data-act="bsbrand" data-brand="${esc(b.name)}">${esc(b.name)}</div>`).join("") + "</div>";
  h += '<div class="card"><table class="tbl"><thead><tr><th>★</th><th>Brand</th><th>EAN</th><th>Product</th><th>① Cheapest</th><th>② 2nd</th><th>③ 3rd</th></tr></thead><tbody>';
  const cell = o => o ? `<b class="best-euro">${fmt(o.unit_price_eur)}</b> <span class="muted">${esc(o.supplier || "")}</span>` : '<span class="dlt">—</span>';
  best.forEach(p => { const os = p.offers || [];
    h += `<tr><td style="color:#e0a800">★</td><td><span class="badge badge-gray">${esc(p.brand || "")}</span></td><td class="muted">${esc(p.ean)}</td><td>${esc(p.description || "—")}</td><td>${cell(os[0])}</td><td>${cell(os[1])}</td><td>${cell(os[2])}</td></tr>`; });
  if (!best.length) h += `<tr><td colspan="7"><div class="empty">No bestsellers flagged${bsBrand !== "All" ? " in " + esc(bsBrand) : ""} — tick ★ in Offers.</div></td></tr>`;
  c.innerHTML = h + "</tbody></table></div>";
};

// ======================= CUSTOMER REQUESTS =======================
VIEWS.requests = async function (c) {
  const reqs = await apiGet("/requests");
  const custOpts = store.customers.map(cu => `<option value="${esc(cu)}">`).join("");
  const knownBrands = [...new Set([...brandList().map(b => b.name), ...reqs.map(r => r.brand).filter(Boolean)])]
    .sort((a, b) => a.localeCompare(b));
  // "Multi-brand" is always offered first (a request can span brands); matching is by EAN regardless
  _comboOptions["req-brand"] = ["Multi-brand", ...knownBrands.filter(b => b.toLowerCase() !== "multi-brand")];
  const custs = [...new Set(reqs.map(r => r.customer).filter(Boolean))];

  let hist = "";
  if (reqs.length) {
    let rows = reqs.slice();
    if (reqBrand !== "All") rows = rows.filter(r => r.brand === reqBrand);
    if (reqCust !== "All") rows = rows.filter(r => r.customer === reqCust);
    // arranged per customer, then per brand, newest first within a group
    rows.sort((a, b) => {
      const ca = (a.customer || "~~").toLowerCase(), cb = (b.customer || "~~").toLowerCase();
      if (ca !== cb) return ca < cb ? -1 : 1;
      const ba = (a.brand || "~~").toLowerCase(), bb = (b.brand || "~~").toLowerCase();
      if (ba !== bb) return ba < bb ? -1 : 1;
      return (b.request_date || "").localeCompare(a.request_date || "");
    });
    let body = "", lastCust = null;
    rows.forEach(r => {
      const cust = r.customer || "— no customer —";
      if (cust !== lastCust) { body += `<tr><td colspan="7" style="background:var(--bg3);font-weight:700">${esc(cust)}</td></tr>`; lastCust = cust; }
      body += `<tr class="click" data-act="openreq" data-id="${r.request_id}"><td class="muted">${esc(fmtDate(r.request_date))}</td><td class="muted">${esc(cust)}</td><td><span class="badge badge-gray">${esc(r.brand || "any")}</span></td><td>${r.count}</td><td><span class="badge badge-green">${r.ok}</span></td><td class="muted">${esc(marginPct(r.margin))}%</td><td class="num"><button class="danger sm" data-act="delreq" data-id="${r.request_id}">🗑</button></td></tr>`;
    });
    if (!rows.length) body = '<tr><td colspan=7><div class="empty">No requests match the filter.</div></td></tr>';
    hist = `<div class="card"><div class="card-title">Request history <span class="muted" style="font-weight:400">· grouped by customer, then brand</span>
        <span style="margin-left:auto;display:flex;gap:8px">
          <select data-act="reqbrand"><option ${reqBrand === "All" ? "selected" : ""}>All</option>${knownBrands.map(b => `<option ${reqBrand === b ? "selected" : ""}>${esc(b)}</option>`).join("")}</select>
          <select data-act="reqcust"><option ${reqCust === "All" ? "selected" : ""}>All</option>${custs.map(cu => `<option ${reqCust === cu ? "selected" : ""}>${esc(cu)}</option>`).join("")}</select>
        </span></div>
      <table class="tbl"><thead><tr><th>Date</th><th>Customer</th><th>Brand</th><th>Lines</th><th>Can supply</th><th>Margin</th><th></th></tr></thead><tbody>${body}</tbody></table></div>`;
  }
  c.innerHTML = `<div class="alert alert-blue"><b>EAN is required; the target price is optional.</b> With a target we check the margin; <b>without one we just list every supplier that can supply the EAN, cheapest first</b>. Other columns are ignored and matched by EAN. Requests keep inputs only and are recomputed live against current and future offers.</div>
    <div class="card"><div class="card-title">New request <button class="sm" style="margin-left:auto" data-act="reqfile">⬆ Upload Excel</button></div>
      <div class="grid2" style="margin-bottom:12px">
        <div><div class="fl">Customer company <span class="muted">(optional)</span></div><input id="req-cust" list="custlist" placeholder="company name"/><datalist id="custlist">${custOpts}</datalist></div>
        <div><div class="fl">Brand <span class="muted">(optional — type to search, or enter a new one)</span></div>${comboField("req-brand", "— any — type to search or add a brand", "")}</div>
        <div><div class="fl">Request date</div><input id="req-date" type="date" value="${esc(today())}"/></div>
        <div><div class="fl">Min margin % <span class="muted">(used only with a target)</span></div><input id="margin" type="number" value="7" step="0.5"/></div>
        <div><div class="fl">Target currency</div><select id="req-cur">${store.currencies.map(cu => `<option>${esc(cu)}</option>`).join("")}</select></div></div>
      <div><div class="fl">Request lines (one EAN per line; optionally a &lt;tab&gt; target price)</div><textarea id="reqin" placeholder="5901234123457&#10;5060150185335&#9;12.50&#10;… one EAN per line, target price optional"></textarea></div>
      <div style="text-align:right;margin-top:10px"><button class="primary" data-act="runsrc">Find suppliers</button></div></div>
    <div id="srcout">${lastResult ? requestResultHTML(lastResult) : ""}</div>${hist}`;
};
function marginPct(m) { const n = parseFloat(m); return isFinite(n) ? (Math.round(n * 1000) / 10) : m; }
function requestResultHTML(req) {
  const results = req.results || [];
  const hasTarget = results.some(r => r.target_eur != null);
  const head = `${esc(req.customer || "—")} · ${esc(fmtDate(req.request_date))}${req.brand ? " · " + esc(req.brand) : ""}`;
  if (!hasTarget) {
    // supply overview: list every supplier that can supply each EAN, cheapest first
    const rows = results.map(r => {
      if (r.status === "unknown") return `<tr><td class="muted">${esc(r.ean)}</td><td>${esc(r.description || "")}</td><td><span class="badge badge-amber">EAN not in database</span></td></tr>`;
      const sl = r.suppliers || [];
      if (!sl.length) return `<tr><td class="muted">${esc(r.ean)}</td><td>${esc(r.description || "")}</td><td><span class="badge badge-red">no live supplier</span></td></tr>`;
      const list = sl.map((s, i) => `<span class="${i === 0 ? "best-euro" : "muted"}" style="margin-right:14px;white-space:nowrap">${fmt(s.unit_price_eur)} ${esc(s.supplier || "")}</span>`).join("");
      return `<tr><td class="muted">${esc(r.ean)}</td><td>${esc(r.description || "")}</td><td>${list}</td></tr>`;
    }).join("");
    return `<div class="stat-grid"><div class="stat green"><div class="stat-label">Can supply</div><div class="stat-value">${req.ok}</div></div>
        <div class="stat"><div class="stat-label">No supplier</div><div class="stat-value">${req.no}</div></div>
        <div class="stat"><div class="stat-label">Unknown EAN</div><div class="stat-value">${req.unknown}</div></div></div>
       <div class="card"><div class="card-title">Result — ${head} <span class="muted" style="font-weight:400">· no target — suppliers cheapest first</span></div>
        <table class="tbl"><thead><tr><th>EAN</th><th>Product (from DB)</th><th>Suppliers — cheapest first (best in green)</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }
  const rows = results.map(r => {
    if (r.status === "unknown") return `<tr><td class="muted">${esc(r.ean)}</td><td>${esc(r.description || "")}</td><td class="num">—</td><td colspan="3"><span class="badge badge-amber">EAN not in database</span></td></tr>`;
    if (r.status === "ok") return `<tr><td class="muted">${esc(r.ean)}</td><td>${esc(r.description || "")}</td><td class="num">${fmt(r.target_eur)}</td><td>${esc(r.supplier || "")}</td><td class="num best-euro">${fmt(r.buy_eur)}</td><td class="num"><span class="badge badge-green">+${esc(r.margin_pct)}%</span></td></tr>`;
    return `<tr><td class="muted">${esc(r.ean)}</td><td>${esc(r.description || "")}</td><td class="num">${fmt(r.target_eur)}</td><td colspan="2"><span class="badge badge-red">no live supplier meets margin</span> <span class="muted">cheapest ${r.buy_eur != null ? fmt(r.buy_eur) + " / " + esc(r.supplier || "") : "—"}</span></td><td></td></tr>`;
  }).join("");
  return `<div class="stat-grid"><div class="stat green"><div class="stat-label">Can supply</div><div class="stat-value">${req.ok}</div></div>
      <div class="stat"><div class="stat-label">No margin</div><div class="stat-value">${req.no}</div></div>
      <div class="stat"><div class="stat-label">Unknown EAN</div><div class="stat-value">${req.unknown}</div></div></div>
     <div class="card"><div class="card-title">Result — ${head}</div>
      <table class="tbl"><thead><tr><th>EAN</th><th>Product (from DB)</th><th class="num">Target</th><th>Best supplier</th><th class="num">Buy</th><th class="num">Margin</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

// ======================= SUPPLIERS =======================
VIEWS.suppliers = async function (c) {
  const sups = store.suppliers;
  let h = '<div class="toolbar"><button class="primary" data-act="newsup">＋ New supplier</button><span class="sp"></span><span class="muted">Territory is per offer (set at import), not per supplier.</span></div>';
  h += '<div class="card"><table class="tbl"><thead><tr><th style="width:300px">Supplier</th><th>Country</th><th>Brands</th><th class="num">Offers</th></tr></thead><tbody>';
  sups.forEach(s => {
    h += `<tr><td><input value="${esc(s.name)}" data-act="renamesup" data-id="${s.supplier_id}" style="font-weight:600;width:100%"/></td>
      <td><input value="${esc(s.country || "")}" data-act="setcountry" data-id="${s.supplier_id}" placeholder="country" style="width:100%"/></td>
      <td>${(s.brands || []).map(b => `<span class="badge badge-gray">${esc(b)}</span>`).join(" ")}</td><td class="num">${s.offers}</td></tr>`;
  });
  c.innerHTML = h + "</tbody></table></div>";
};

// ======================= IMPORT LOG =======================
VIEWS.import = async function (c) {
  const batches = await apiGet("/batches");
  let h = '<div class="alert alert-blue">Drag an Excel file anywhere (or “Add file” in Offers). The importer auto-maps columns, resolves the brand from the upload form (single brand, or multi-brand matched by EAN), and adds a live column — the EAN is the key. New EANs are added; duplicates are never created.</div>';
  h += '<div class="drop" data-act="addfile">⬆ Click or drag an .xlsx here to import</div><div style="height:14px"></div>';
  h += '<div class="card"><div class="card-title">Imported batches</div><table class="tbl"><thead><tr><th>File</th><th>Brand</th><th>Supplier</th><th>Date</th><th>Terr.</th><th>Type</th><th>Lead</th><th>MOQ</th><th class="num">Rows</th><th></th></tr></thead><tbody>';
  batches.forEach(b => { h += `<tr><td class="muted">${esc(b.source_file || "—")}</td><td><span class="badge badge-gray">${esc(b.brand || "")}</span></td><td>${esc(b.supplier_name)}</td><td>${esc(fmtDate(b.received_date))}</td><td>${esc(b.territory || "")}</td><td>${esc(b.kind_label || b.kind || "")}${b.ended_at ? ' <span class="badge badge-gray">ended</span>' : ""}</td><td class="muted">${esc(b.lead_time || "—")}</td><td class="muted">${esc(b.moq || "—")}</td><td class="num">${b.n_offers}</td><td class="num"><button class="danger sm" data-act="delbatch" data-bid="${b.batch_id}">🗑</button></td></tr>`; });
  if (!batches.length) h += '<tr><td colspan=10><div class="empty">No imports yet.</div></td></tr>';
  c.innerHTML = h + "</tbody></table></div>";
};

// ======================= REVIEW =======================
VIEWS.review = async function (c) {
  const items = await apiGet("/review");
  if (!items.length) { c.innerHTML = '<div class="empty"><div class="ei">✓</div><div>Nothing to review.</div><div class="muted" style="margin-top:8px">Failed EANs, rows with no price, or products without a brand/description appear here.</div></div>'; return; }
  let h = '<div class="alert alert-amber">Items needing a look. Products missing a brand or description are listed so you can fill them (click “fix”).</div>';
  h += '<div class="card"><table class="tbl"><thead><tr><th>Reason</th><th>Brand / detail</th><th>EAN</th><th></th></tr></thead><tbody>';
  items.forEach(r => h += `<tr><td><span class="badge badge-amber">${esc(r.reason)}</span></td><td class="muted">${esc(r.detail || "")}</td><td>${esc(r.ean || "")}</td><td class="num">${r.fix ? `<button class="sm" data-act="prodedit" data-ean="${esc(r.ean)}">fix</button>` : ""}</td></tr>`);
  c.innerHTML = h + "</tbody></table></div>";
};

// ======================= SETTINGS (Currency & FX) =======================
VIEWS.settings = async function (c) {
  const fx = await apiGet("/fx/rates"); store.fx = fx;
  let h = '<div class="alert alert-blue">Rates are fetched daily from Frankfurter (ECB-based, base EUR) by a server job. A manual override wins for that day. Add more currencies anytime.</div>';
  h += `<div class="card"><div class="card-title">Rates <span class="muted" style="font-weight:400">${fx.rate_date ? "· for " + esc(fx.rate_date) : ""}</span><button class="sm" style="margin-left:auto" data-act="fxrefresh">↻ Fetch today’s rates</button></div>
    <div style="display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap;margin-bottom:14px">
      <div><div class="fl">Code</div><input id="cc-code" placeholder="e.g. JPY" style="width:110px;text-transform:uppercase"/></div>
      <div><div class="fl">Rate (1 EUR =)</div><input id="cc-rate" type="number" step="0.0001" placeholder="e.g. 162.5" style="width:150px"/></div>
      <button class="primary" data-act="addcur">＋ Add / override</button></div>`;
  h += '<table class="tbl"><thead><tr><th>Currency</th><th class="num" style="width:180px">Rate (editable override)</th></tr></thead><tbody>';
  h += '<tr><td><b>EUR</b></td><td class="num">1.0000 <span class="badge badge-gray">base</span></td></tr>';
  Object.keys(fx.rates || {}).sort().forEach(cu => h += `<tr><td><b>${esc(cu)}</b></td><td class="num"><input type="number" step="0.0001" value="${esc(fx.rates[cu])}" data-act="setrate" data-cur="${esc(cu)}" style="width:140px;text-align:right"/></td></tr>`);
  if (!Object.keys(fx.rates || {}).length) h += '<tr><td colspan=2><div class="empty">No rates yet — “Fetch today’s rates”, or add one by hand.</div></td></tr>';
  h += '</tbody></table>';
  h += `<div class="card" style="margin-top:16px"><div class="card-title">Offline export</div>
    <div class="alert alert-amber">The export bundle contains the <b>full database</b>. Save it only to the encrypted drive.</div>
    <a href="/export" download><button class="success">⤓ Download database + CSV bundle</button></a></div>`;
  c.innerHTML = h;
};

// ---------- searchable combobox (type to filter, click to pick, or type a new value) ----------
function comboField(id, placeholder, value) {
  return `<div class="combo" data-combo="${id}">
    <input id="${id}" autocomplete="off" placeholder="${esc(placeholder)}" value="${esc(value || "")}" data-act="comboinput"/>
    <div class="combo-menu" id="${id}-menu"></div></div>`;
}
function openCombo(input) {
  const menu = document.getElementById(input.id + "-menu");
  if (!menu) return;
  const q = (input.value || "").trim().toLowerCase();
  const opts = (_comboOptions[input.id] || []).filter(n => !q || n.toLowerCase().includes(q));
  menu.innerHTML = opts.slice(0, 100).map(n =>
      `<div class="combo-item" data-act="comboitem" data-target="${input.id}" data-val="${esc(n)}">${esc(n)}</div>`).join("")
    || `<div class="combo-empty">No brand matches${input.value.trim() ? ` — “${esc(input.value.trim())}” will be used as typed` : ""}.</div>`;
  menu.style.display = "block";
}
function closeCombos() { document.querySelectorAll(".combo-menu").forEach(m => { m.style.display = "none"; }); }

// ---------- modals + toast ----------
function openModal(html) { document.getElementById("modalbox").innerHTML = html; document.getElementById("modal").classList.add("on"); }
function closeModal() { document.getElementById("modal").classList.remove("on"); }
function toast(msg) { const d = document.createElement("div"); d.textContent = msg; d.style.cssText = "position:fixed;bottom:22px;left:50%;transform:translateX(-50%);background:var(--navy);color:#fff;padding:12px 18px;border-radius:11px;box-shadow:var(--shadow-lg);z-index:2000;max-width:560px;white-space:pre-wrap;font-size:12.5px;line-height:1.5"; document.body.appendChild(d); setTimeout(() => { d.style.transition = "opacity .4s"; d.style.opacity = "0"; setTimeout(() => d.remove(), 400); }, 5200); }
function confirmModal(msg, onYes, yesLabel, danger) {
  _confirmCb = onYes;
  openModal(`<div class="detail-header"><div><h2>Please confirm</h2></div><div class="x" data-act="closemodal">×</div></div>
    <div class="detail-body"><div style="white-space:pre-wrap;margin-bottom:18px;line-height:1.5">${esc(msg)}</div>
      <div style="display:flex;gap:8px;justify-content:flex-end"><button data-act="closemodal">Cancel</button><button class="${danger ? "danger" : "primary"}" data-act="confirmyes">${esc(yesLabel || "Confirm")}</button></div></div>`);
}
function today() { return new Date().toISOString().slice(0, 10); }

// ---------- product edit / add line ----------
async function openProductEdit(ean) {
  const data = await apiGet("/products/" + encodeURIComponent(ean) + "/offers").catch(() => null);
  const desc = (data && data.description) || "";
  const curBrand = (data && data.brand) || "";
  const opts = '<option value="">— choose brand —</option>' + brandList().map(b => `<option ${b.name === curBrand ? "selected" : ""}>${esc(b.name)}</option>`).join("");
  openModal(`<div class="detail-header"><div><h2>Edit product</h2><div class="sub">EAN ${esc(ean)}${eanValid(ean) ? "" : " · ⚠ checksum fails"}</div></div><div class="x" data-act="closemodal">×</div></div>
   <div class="detail-body"><div class="grid2">
     <div style="grid-column:1/3"><div class="fl">Description</div><input id="pe-desc" value="${esc(desc)}" placeholder="product description"/></div>
     <div><div class="fl">Brand</div><select id="pe-brand">${opts}</select></div></div>
   <div class="muted" style="margin-top:8px">Size is no longer stored (dropped from the data model).</div>
   <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px;padding-top:14px;border-top:1px solid var(--border)">
     <button data-act="closemodal">Cancel</button><button class="primary" data-act="saveprod" data-ean="${esc(ean)}">Save</button></div></div>`);
}
async function saveProductEdit(ean) {
  const desc = document.getElementById("pe-desc").value.trim();
  const brand = document.getElementById("pe-brand").value;
  await api("PATCH", "/products/" + encodeURIComponent(ean), { description: desc, brand: brand });
  await loadStore(); closeModal(); if (brand) activeBrand = brand; render(); toast("Saved.");
}
function addLineModal() {
  const curBrand = activeBrand === "__unknown__" ? "" : activeBrand;
  openModal(`<div class="detail-header"><div><h2>Add line manually</h2><div class="sub">${esc(curBrand || "no brand (Unknown)")}</div></div><div class="x" data-act="closemodal">×</div></div>
  <div class="detail-body"><div class="grid2">
     <div><div class="fl">EAN *</div><input id="ml-ean" placeholder="13-digit barcode"/></div>
     <div><div class="fl">Brand</div><select id="ml-brand"><option value="">— choose brand —</option>${brandList().map(b => `<option ${b.name === curBrand ? "selected" : ""}>${esc(b.name)}</option>`).join("")}</select></div>
     <div style="grid-column:1/3"><div class="fl">Description</div><input id="ml-desc" placeholder="product description"/></div></div>
   <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px;padding-top:14px;border-top:1px solid var(--border)">
     <button data-act="closemodal">Cancel</button><button class="primary" data-act="saveline">Add product</button></div></div>`);
}
async function saveLine() {
  const ean = eanCanon(document.getElementById("ml-ean").value);
  const desc = document.getElementById("ml-desc").value.trim();
  const brand = document.getElementById("ml-brand").value;
  if (!ean) { toast("Enter a numeric EAN."); return; }
  try {
    const res = await api("POST", "/products", { ean, description: desc, brand });
    await loadStore(); closeModal(); if (brand) activeBrand = brand; render();
    if (res && res.ean_check === false) toast("Added — note: the EAN checksum does not validate.");
    else toast("Product added.");
  } catch (e) { toast(e.message); }
}

// ---------- offer details / export / delete / end ----------
async function openOfferDetails(bid) {
  const b = await apiGet("/batches/" + bid + "/offers");
  const rows = b.lines.map(o => `<tr><td class="muted">${esc(o.ean)}</td><td>${esc(o.description || "")}</td><td class="num">${fmt(o.price_eur)}</td></tr>`).join("");
  const st = (l, v) => `<div class="stat"><div class="stat-label">${esc(l)}</div><div class="stat-value" style="font-size:14px">${esc(v || "—")}</div></div>`;
  const endBtn = (b.kind === "stock_offer" && !b.ended_at) ? `<button class="sm" data-act="endoffer" data-bid="${b.batch_id}">⏹ End offer</button>` : "";
  openModal(`<div class="detail-header"><div><h2>${esc(b.supplier)}</h2><div class="sub">${esc(b.brand || "")} · ${esc(fmtDate(b.received_date))} · ${esc(b.kind_label || b.kind || "")}${b.ended_at ? " · ended " + esc(b.ended_at) : ""}</div></div>
     <div style="display:flex;gap:8px;align-items:center">${endBtn}<button class="success sm" data-act="exportoffer" data-bid="${b.batch_id}">⤓ Export</button><button class="danger sm" data-act="delbatch" data-bid="${b.batch_id}">🗑 Delete offer</button><div class="x" data-act="closemodal">×</div></div></div>
   <div class="detail-body">
     <div class="stat-grid" style="grid-template-columns:repeat(auto-fill,minmax(110px,1fr))">${st("Territory", b.territory)}${st("Currency", b.currency || "EUR")}${st("Lead time", b.lead_time)}${st("MOQ", b.moq)}${st("Lines", b.lines.length)}</div>
     <div class="card-title" style="margin-top:4px">Price list <span class="muted" style="font-weight:400">${b.source_file ? "· " + esc(b.source_file) : ""}</span></div>
     <div style="max-height:46vh;overflow:auto"><table class="tbl"><thead><tr><th>EAN</th><th>Description</th><th class="num">${esc(display)}</th></tr></thead><tbody>${rows || '<tr><td colspan=3 class=muted>no lines</td></tr>'}</tbody></table></div>
   </div>`);
  window.__offerCache = b;
}
async function exportOffer(bid) {
  const b = window.__offerCache && window.__offerCache.batch_id == bid ? window.__offerCache : await apiGet("/batches/" + bid + "/offers");
  const aoa = [["EAN", "Description", "Price EUR"]];
  b.lines.forEach(o => aoa.push([o.ean, o.description || "", o.price_eur]));
  const ws = XLSX.utils.aoa_to_sheet(aoa); const wb = XLSX.utils.book_new(); XLSX.utils.book_append_sheet(wb, ws, "offer");
  XLSX.writeFile(wb, ((b.supplier || "offer") + "_" + (b.brand || "") + "_" + (b.received_date || "") + ".xlsx").replace(/[^\w.-]+/g, "_"));
}
function deleteBatch(bid) {
  confirmModal("Delete this offer column? This permanently removes the batch and its price lines.", async () => {
    await api("DELETE", "/batches/" + bid); await loadStore(); render(); toast("Offer deleted.");
  }, "🗑 Delete offer", true);
}
async function endOffer(bid) {
  await api("POST", "/batches/" + bid + "/end-offer?ended_at=" + encodeURIComponent(today()));
  await loadStore(); closeModal(); render(); toast("Stock offer ended — it no longer counts as live.");
}
async function toggleBest(ean) {
  const row = currentGridRow(ean);
  await api("POST", "/products/" + encodeURIComponent(ean) + "/bestseller", { is_bestseller: !(row && row.is_bestseller) });
  render();
}
function currentGridRow(ean) { return _gridRows[ean]; }

// ---------- sourcing ----------
async function runSourcing() {
  const customer = (document.getElementById("req-cust").value || "").trim();
  const brand = (document.getElementById("req-brand").value || "").trim();
  const request_date = document.getElementById("req-date").value || today();
  const marginPctVal = parseFloat(document.getElementById("margin").value);
  const currency = document.getElementById("req-cur").value || "EUR";
  const margin = (isFinite(marginPctVal) ? marginPctVal : 7) / 100;
  const lines = [];
  document.getElementById("reqin").value.split("\n").map(l => l.trim()).filter(Boolean).forEach(l => {
    const { ean, target } = parseReqLine(l);
    if (!ean) return;
    const line = target != null ? { ean, target_price: String(target), currency } : { ean, currency };
    if (reqDescriptions[ean]) line.description = reqDescriptions[ean];
    lines.push(line);
  });
  if (!lines.length) { toast("No valid EAN lines found."); return; }
  try {
    const req = await api("POST", "/requests", { customer, brand, request_date, margin: String(margin), lines });
    lastResult = req; await loadStore(); render();
  } catch (e) { toast(e.message); }
}
function parseReqLine(l) {
  const toks = l.split(/\t|;|,/).map(t => t.trim()).filter(Boolean); let ean = null;
  for (const t of toks) { const code = eanCanon(t); if (code && (code.length === 13 || code.length === 8)) { ean = code; break; } }
  let target = null; const euroTok = toks.find(t => /[€$£]/.test(t) && toNum(t) != null);
  if (euroTok) target = toNum(euroTok);
  if (target == null) { const dec = toks.filter(t => /\d[.,]\d/.test(t) && toNum(t) != null && eanCanon(t) !== ean); if (dec.length) target = toNum(dec[dec.length - 1]); }
  if (target == null) { const nums = toks.filter(t => eanCanon(t) !== ean).map(toNum).filter(v => v != null); if (nums.length) target = nums[nums.length - 1]; }
  return { ean, target };
}
async function openRequestDetail(id) {
  const req = await apiGet("/requests/" + id);
  openModal(`<div class="detail-header"><div><h2>Request — ${esc(req.customer || "—")}</h2><div class="sub">${esc(fmtDate(req.request_date))}${req.brand ? " · " + esc(req.brand) : ""} · ${req.count} lines · min margin ${esc(marginPct(req.margin))}%</div></div>
      <div style="display:flex;gap:8px;align-items:center"><button class="danger sm" data-act="delreq" data-id="${req.request_id}">🗑 Delete</button><div class="x" data-act="closemodal">×</div></div></div>
    <div class="detail-body">${requestResultHTML(req)}</div>`);
}
function deleteRequest(id) {
  confirmModal("Delete this request from the history?", async () => {
    await api("DELETE", "/requests/" + id); if (lastResult && lastResult.request_id == id) lastResult = null; closeModal(); render(); toast("Request deleted.");
  }, "Delete", true);
}

// ---------- suppliers ----------
function newSupplierModal() {
  openModal(`<div class="detail-header"><div><h2>New supplier</h2></div><div class="x" data-act="closemodal">×</div></div>
  <div class="detail-body"><div class="grid2"><div><div class="fl">Name *</div><input id="ns-name" placeholder="supplier name"/></div>
    <div><div class="fl">Country</div><input id="ns-ctry" placeholder="e.g. Italy"/></div></div>
   <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px;padding-top:14px;border-top:1px solid var(--border)">
     <button data-act="closemodal">Cancel</button><button class="primary" data-act="savesup">Create supplier</button></div></div>`);
}
async function saveSupplier() {
  const name = document.getElementById("ns-name").value.trim();
  const country = document.getElementById("ns-ctry").value.trim();
  if (!name) { toast("Name is required."); return; }
  if (store.suppliers.some(s => s.name.toLowerCase() === name.toLowerCase())) { toast("That supplier already exists."); return; }
  const s = await api("POST", "/suppliers", { name });
  if (country) await api("PATCH", "/suppliers/" + s.supplier_id, { country });
  await loadStore(); closeModal(); render();
}

// ---------- settings ----------
async function setRate(cur, v) {
  const n = parseFloat(v); if (!(n > 0)) return;
  const rate_date = store.fx.rate_date || today();
  await api("POST", "/fx/rates", { rate_date, currency: cur, rate: String(v) });
  store.fx = await apiGet("/fx/rates");
}
async function addCurrency() {
  const code = (document.getElementById("cc-code").value || "").trim().toUpperCase();
  const rate = parseFloat(document.getElementById("cc-rate").value);
  if (!/^[A-Z]{3}$/.test(code)) { toast("Enter a 3-letter code."); return; }
  if (!(rate > 0)) { toast("Enter a positive rate."); return; }
  const rate_date = store.fx.rate_date || today();
  await api("POST", "/fx/rates", { rate_date, currency: code, rate: String(rate) });
  await loadStore(); render();
}
async function fxRefresh() {
  try { const r = await api("POST", "/fx/refresh"); await loadStore(); render(); toast("Fetched " + r.written + " rate(s) for " + r.rate_date + " (" + r.source + ")."); }
  catch (e) { toast("Fetch failed: " + e.message); }
}

// ---------- import wizard ----------
function openImportWizard(name) {
  const supOpts = store.suppliers.map(s => `<option>${esc(s.name)}</option>`).join("");
  const guess = (name.split(/[_\-. ]/)[0] || "").replace(/[^A-Za-z]/g, "");
  const brandMatch = store.brands.find(b => b.name.toLowerCase() === guess.toLowerCase());
  const brandOpts = brandList().map(b => `<option ${brandMatch && brandMatch.name === b.name ? "selected" : ""}>${esc(b.name)}</option>`).join("");
  openModal(`<div class="detail-header"><div><h2>Import offer</h2><div class="sub">${esc(name)}</div></div><div class="x" data-act="closemodal">×</div></div>
   <div class="detail-body"><div class="alert alert-blue"><b>Single-brand file:</b> pick its brand — it is applied to <b>every row</b>, so use it only when the whole file is one brand. <b>Multi-brand catalogue:</b> choose “🔀 Multi-brand” — if the file has a <b>brand column</b> it is used (new brand names ask for your OK first); otherwise each row’s brand is matched by <b>existing EAN</b>. Rows with no brand yet go to the <b>❓ Unknown brands</b> tab (and Review), resolved when a branded offer for that EAN arrives. Columns (EAN, description, price — lowest wins) are auto-mapped.</div>
     <div class="grid2">
       <div><div class="fl">Supplier *</div><select id="iw-sup-sel" data-act="togglenew" data-k="sup"><option value="">— choose supplier —</option>${supOpts}<option value="__new__">➕ New supplier…</option></select>
         <input id="iw-sup-new" style="display:none;margin-top:6px" placeholder="new supplier name"/></div>
       <div><div class="fl">Brand</div><select id="iw-brand-sel" data-act="togglenew" data-k="brand"><option value="">— pick a brand —</option>${brandOpts}<option value="__multi__">🔀 Multi-brand (match by EAN)</option><option value="__new__">➕ New brand…</option></select>
         <input id="iw-brand-new" style="display:none;margin-top:6px" placeholder="new brand name"/></div>
       <div><div class="fl">Offer type *</div><select id="iw-kind"><option value="price_list">Regular offer (longer validity)</option><option value="stock_offer">Stock offer (limited in time and/or quantity)</option></select></div>
       <div><div class="fl">Territory *</div><select id="iw-terr"><option value="T2">T2 · European</option><option value="T1">T1 · non-European</option></select></div>
       <div><div class="fl">Currency</div><select id="iw-cur">${store.currencies.map(c => `<option>${esc(c)}</option>`).join("")}</select></div>
       <div><div class="fl">Received date</div><input id="iw-date" type="date" value="${esc(today())}"/></div>
       <div><div class="fl">Notes (promotion…)</div><input id="iw-notes" placeholder="business info only"/></div>
       <div><div class="fl">Lead time</div><input id="iw-lead" placeholder="e.g. 2-3 weeks"/></div>
       <div><div class="fl">MOQ</div><input id="iw-moq" placeholder="e.g. €25,000 or 6240 pcs"/></div>
       <div style="grid-column:1/3"><label style="display:flex;gap:8px;align-items:center;font-size:12px"><input type="checkbox" id="iw-perbox" style="width:auto"/> Prices are per box — divide by pieces/box (uses the file’s pcs/box column)</label></div></div>
     <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px;padding-top:14px;border-top:1px solid var(--border)">
       <button data-act="closemodal">Cancel</button><button class="primary" data-act="doimport">Import &amp; compare</button></div></div>`);
}
function comboVal(k) { const v = document.getElementById("iw-" + k + "-sel").value; return v === "__new__" ? document.getElementById("iw-" + k + "-new").value.trim() : v; }
async function sha256Hex(buf) {
  try { const h = await crypto.subtle.digest("SHA-256", buf); return [...new Uint8Array(h)].map(b => b.toString(16).padStart(2, "0")).join(""); }
  catch (e) { return null; }
}
async function doImport() {
  const sup = comboVal("sup");
  const brandSel = document.getElementById("iw-brand-sel").value;
  const multibrand = brandSel === "__multi__";
  const singleBrand = multibrand ? "" : (brandSel === "__new__" ? document.getElementById("iw-brand-new").value.trim() : brandSel);
  if (!sup) { toast("Supplier is required."); return; }
  if (!multibrand && !singleBrand) { toast("Pick a brand, or choose Multi-brand."); return; }
  if (!_pendingBytes) { toast("No file."); return; }
  const perBox = document.getElementById("iw-perbox").checked;
  const kind = document.getElementById("iw-kind").value;
  const currency = document.getElementById("iw-cur").value;
  const meta = {
    territory: document.getElementById("iw-terr").value,
    received_date: document.getElementById("iw-date").value || today(),
    notes: document.getElementById("iw-notes").value.trim() || null,
    lead_time: document.getElementById("iw-lead").value.trim() || null,
    moq: document.getElementById("iw-moq").value.trim() || null,
  };
  let parsed;
  try { parsed = parseWorkbook(new Uint8Array(_pendingBytes)); }
  catch (e) { toast("Could not read the file: " + e.message); return; }
  if (!parsed.rows.length) { toast("Could not find an EAN + price table (" + parsed.sheets + " sheet(s) scanned)."); return; }

  // Guard: a single-brand import of a file that clearly lists several brands would force them
  // all under one brand (the mistake that mixed earlier catalogues). Offer to switch.
  if (!multibrand) {
    const fb = [...new Set(parsed.rows.map(r => (r.brand || "").trim()).filter(Boolean))];
    if (fb.length >= 2) {
      const goOn = await confirmSingleOnMulti(fb.length, singleBrand);
      if (!goOn) return;   // user will cancel and pick Multi-brand
    }
  }

  // Multi-brand with a brand column: known brands file automatically; NEW brand names need the
  // owner's OK (you chose "hold in Unknown until I confirm"). Unticked/declined -> Unknown tab.
  let allowed = null;   // lowercased brand names allowed to be assigned from the file
  const hasBrandCol = multibrand && parsed.rows.some(r => (r.brand || "").trim());
  if (hasBrandCol) {
    const knownLower = new Set(store.brands.map(b => b.name.toLowerCase()));
    allowed = new Set(knownLower);
    const fileBrands = [...new Set(parsed.rows.map(r => (r.brand || "").trim()).filter(Boolean))];
    const newBrands = fileBrands.filter(b => !knownLower.has(b.toLowerCase()));
    if (newBrands.length) {
      const counts = {};
      newBrands.forEach(b => counts[b] = parsed.rows.filter(r => (r.brand || "").trim().toLowerCase() === b.toLowerCase()).length);
      const approved = await confirmNewBrands(newBrands, counts);   // [] = none, null = cancel import
      if (approved === null) return;
      approved.forEach(b => allowed.add(b.toLowerCase()));
    }
  }

  try {
    let sid = (store.suppliers.find(s => s.name.toLowerCase() === sup.toLowerCase()) || {}).supplier_id;
    if (!sid) { sid = (await api("POST", "/suppliers", { name: sup })).supplier_id; }
    const rate = currency === "EUR" ? "1" : (store.fx.rates || {})[currency] || null;
    const noRate = currency !== "EUR" && !rate;   // non-EUR currency with no FX rate yet
    const sha = await sha256Hex(_pendingBytes);
    const batch = await api("POST", "/batches", {
      supplier_id: sid, currency, received_date: meta.received_date, territory: meta.territory,
      kind, brand: multibrand ? "(multi-brand)" : singleBrand, moq: meta.moq, lead_time: meta.lead_time,
      notes: meta.notes, source_file: _pendingFile ? _pendingFile.name : null, source_sha256: sha,
      fx_rate_eur: rate, fx_source: "import", fx_locked: 0,
    });
    if (batch.warning) toast("Note: " + batch.warning);
    const offers = parsed.rows.map(r => {
      const o = { ean: r.ean, quoted_price: String(r.price), description: r.desc || null };
      if (perBox && r.ppb) { o.price_basis = "per_box"; o.pieces_per_box = r.ppb; }
      const rb = (r.brand || "").trim();
      if (multibrand && allowed && rb && allowed.has(rb.toLowerCase())) o.brand = rb;   // file brand
      return o;
    });
    const res = await api("POST", "/offers", { batch_id: batch.batch_id, brand_mode: multibrand ? "multi" : "single", brand: multibrand ? null : singleBrand, offers });
    await loadStore();
    closeModal(); view = "rankings"; activeBrand = multibrand ? activeBrand : singleBrand; searchQ = ""; terrFilter = "all"; cmpA = ""; cmpB = "";
    setActiveNav("rankings"); render();
    const nrev = (res.review || []).length;
    const revNote = nrev ? "\n• " + nrev + " row(s) need attention" + (multibrand ? " — unmatched EANs are in the ❓ Unknown brands tab + Review" : " (Review)") : "";
    const fxNote = noRate ? `\n• ⚠ ${currency} prices stay hidden until you add a ${currency} rate in Currency & FX (then they appear automatically)` : "";
    toast(`Imported ${offers.length} rows from ${_pendingFile ? _pendingFile.name : "file"}\n• ${res.offer_ids.length} offers added${revNote}${fxNote}${parsed.bad ? "\n• " + parsed.bad + " rows skipped (no EAN or price)" : ""}`);
  } catch (e) { toast("Import failed: " + e.message); }
}

// Guard dialog: the chosen file looks multi-brand but single-brand was picked.
function confirmSingleOnMulti(n, brandName) {
  return new Promise(resolve => {
    _importGuardResolve = resolve;
    openModal(`<div class="detail-header"><div><h2>This looks like a multi-brand file</h2><div class="sub">${n} different brands found in a brand column</div></div><div class="x" data-act="guardcancel">×</div></div>
      <div class="detail-body"><div class="alert alert-amber">You picked a <b>single brand</b>${brandName ? ` (“${esc(brandName)}”)` : ""}, which files <b>every row</b> under it. This file lists <b>${n} brands</b> in a brand column — use <b>🔀 Multi-brand</b> instead to keep them separate (new names ask for your OK).</div>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px;padding-top:14px;border-top:1px solid var(--border)">
          <button class="primary" data-act="guardcancel">Cancel — pick Multi-brand</button><button class="danger" data-act="guardsingle">Import all as one brand anyway</button></div></div>`);
  });
}

// Approval step for brand names found in a multi-brand file that aren't in the system yet.
// Resolves to the array of approved (ticked) names, or null if the owner cancels the import.
function confirmNewBrands(newBrands, counts) {
  return new Promise(resolve => {
    _brandApproveResolve = resolve;
    const list = newBrands.map(b => `<label style="display:flex;gap:9px;align-items:center;padding:7px 0;border-bottom:1px solid var(--border)">
        <input type="checkbox" class="brand-approve" data-brand="${esc(b)}" style="width:auto"/>
        <b>${esc(b)}</b><span class="muted">· ${counts[b]} line(s)</span></label>`).join("");
    openModal(`<div class="detail-header"><div><h2>New brands in this file</h2><div class="sub">tick the real ones to create now; the rest wait in Unknown brands</div></div><div class="x" data-act="brandcancel">×</div></div>
      <div class="detail-body"><div class="alert alert-amber">These brand names in the file aren’t in your system yet. <b>Tick</b> the ones to create and file now. Leave a duplicate or misspelling <b>unticked</b> — those rows go to the <b>❓ Unknown brands</b> tab so you can assign them by hand.</div>
        ${list}
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px;padding-top:14px;border-top:1px solid var(--border)">
          <button data-act="brandcancel">Cancel import</button><button class="primary" data-act="brandcontinue">Continue import</button></div></div>`);
  });
}

// ---------- file inputs + drag/drop ----------
function pickFile(inputId, cb) { const el = document.getElementById(inputId); el.onchange = null; el.value = ""; el.onchange = () => { const f = el.files[0]; el.value = ""; if (f) cb(f); }; el.click(); }
function readFileThen(f, cb) { const fr = new FileReader(); fr.onload = () => cb(fr.result); fr.readAsArrayBuffer(f); }
function startOfferImport(f) { _pendingFile = f; readFileThen(f, buf => { _pendingBytes = buf; openImportWizard(f.name); }); }
function startRequestUpload(f) {
  readFileThen(f, buf => {
    try {
      const rows = parseRequestWorkbook(new Uint8Array(buf));
      if (!rows.length) { toast("No EANs found in that file."); return; }
      reqDescriptions = {}; rows.forEach(r => { if (r.desc) reqDescriptions[r.ean] = r.desc; });
      const out = rows.map(r => r.target != null ? (r.ean + "\t" + r.target) : r.ean);
      const noTarget = rows.filter(r => r.target == null).length;
      // keep anything already typed into the form (the re-render would otherwise clear it)
      const keep = id => { const el = document.getElementById(id); return el ? el.value : ""; };
      const prev = { cust: keep("req-cust"), brand: keep("req-brand"), date: keep("req-date"), margin: keep("margin"), cur: keep("req-cur") };
      view = "requests"; setActiveNav("requests"); render().then(() => {
        const set = (id, v) => { const el = document.getElementById(id); if (el && v) el.value = v; };
        set("req-cust", prev.cust); set("req-brand", prev.brand); set("req-date", prev.date); set("margin", prev.margin); set("req-cur", prev.cur);
        const ta = document.getElementById("reqin"); if (ta) ta.value = out.join("\n");
        toast(`${rows.length} EAN(s) loaded${noTarget === rows.length ? " (no prices — Find suppliers will list who can supply them)" : ""}. Review, then Find suppliers.`);
      });
    } catch (e) { toast("Upload failed: " + e.message); }
  });
}
function setActiveNav(v) { document.querySelectorAll(".nav-item").forEach(x => x.classList.toggle("active", x.dataset.view === v)); }

// ---------- event delegation (no inline handlers anywhere) ----------
document.querySelectorAll(".nav-item").forEach(n => n.addEventListener("click", () => { setActiveNav(n.dataset.view); view = n.dataset.view; cmpA = cmpB = ""; sortCol = null; render(); }));
document.getElementById("cur").addEventListener("change", e => { display = e.target.value; render(); });
document.getElementById("modal").addEventListener("click", e => { if (e.target.id === "modal") { if (_brandApproveResolve) { const r = _brandApproveResolve; _brandApproveResolve = null; r(null); } if (_importGuardResolve) { const r = _importGuardResolve; _importGuardResolve = null; r(false); } closeModal(); } });

document.addEventListener("click", e => {
  if (!e.target.closest(".combo")) closeCombos();   // click outside any combobox closes its menu
  const el = e.target.closest("[data-act]"); if (!el) return;
  const act = el.dataset.act, bid = el.dataset.bid, ean = el.dataset.ean, id = el.dataset.id;
  switch (act) {
    case "comboinput": openCombo(el); break;
    case "comboitem": { const inp = document.getElementById(el.dataset.target); if (inp) inp.value = el.dataset.val; const m = document.getElementById(el.dataset.target + "-menu"); if (m) m.style.display = "none"; break; }
    case "brand": activeBrand = el.dataset.brand; sortCol = null; cmpA = cmpB = ""; curFilter = "all"; if (activeBrand !== "__unknown__") brandLetter = letterOf(activeBrand); render(); break;
    case "brandletter": brandLetter = el.dataset.letter; render(); break;
    case "bsbrand": bsBrand = el.dataset.brand; render(); break;
    case "terr": terrFilter = el.dataset.terr; curFilter = "all"; render(); break;
    case "curf": curFilter = el.dataset.cur; render(); break;
    case "zoom": setZoom(parseInt(el.dataset.dir, 10)); break;
    case "sort": setSort(el.dataset.col); break;
    case "cmp": toggleCmp(bid); break;
    case "cmpclear": cmpA = cmpB = ""; render(); break;
    case "movecol": moveCol(parseInt(bid, 10), parseInt(el.dataset.dir, 10)); break;
    case "offer": openOfferDetails(bid); break;
    case "prodedit": openProductEdit(ean); break;
    case "best": toggleBest(ean); break;
    case "addfile": pickFile("addfile", startOfferImport); break;
    case "addline": addLineModal(); break;
    case "reqfile": pickFile("reqfile", startRequestUpload); break;
    case "runsrc": runSourcing(); break;
    case "openreq": openRequestDetail(id); break;
    case "delreq": deleteRequest(id); break;
    case "newsup": newSupplierModal(); break;
    case "savesup": saveSupplier(); break;
    case "saveprod": saveProductEdit(ean); break;
    case "saveline": saveLine(); break;
    case "exportoffer": exportOffer(bid); break;
    case "delbatch": deleteBatch(bid); break;
    case "endoffer": endOffer(bid); break;
    case "doimport": doImport(); break;
    case "addcur": addCurrency(); break;
    case "fxrefresh": fxRefresh(); break;
    case "newsupplier": newSupplierModal(); break;
    case "closemodal": closeModal(); break;
    case "confirmyes": { const cb = _confirmCb; _confirmCb = null; closeModal(); if (cb) cb(); break; }
    case "brandcancel": { const r = _brandApproveResolve; _brandApproveResolve = null; closeModal(); if (r) r(null); break; }
    case "brandcontinue": { const r = _brandApproveResolve; _brandApproveResolve = null; const picked = [...document.querySelectorAll(".brand-approve:checked")].map(x => x.dataset.brand); closeModal(); if (r) r(picked); break; }
    case "guardcancel": { const r = _importGuardResolve; _importGuardResolve = null; closeModal(); if (r) r(false); break; }
    case "guardsingle": { const r = _importGuardResolve; _importGuardResolve = null; closeModal(); if (r) r(true); break; }
  }
});
document.addEventListener("input", e => {
  const el = e.target.closest("[data-act]"); if (!el) return;
  if (el.dataset.act === "search") { searchQ = el.value; queueFocus("offSearch"); render(); }
  else if (el.dataset.act === "comboinput") { openCombo(el); }
});
document.addEventListener("focusin", e => {
  const t = e.target; if (t && t.dataset && t.dataset.act === "comboinput") openCombo(t);
});
document.addEventListener("keydown", e => {
  const t = e.target; if (!t || !t.dataset || t.dataset.act !== "comboinput") return;
  if (e.key === "Escape" || e.key === "Enter") { const m = document.getElementById(t.id + "-menu"); if (m) m.style.display = "none"; if (e.key === "Enter") e.preventDefault(); }
});
document.addEventListener("change", e => {
  const el = e.target.closest("[data-act]"); if (!el) return;
  const act = el.dataset.act, id = el.dataset.id;
  if (act === "togglenew") { const k = el.dataset.k; document.getElementById("iw-" + k + "-new").style.display = el.value === "__new__" ? "block" : "none"; }
  else if (act === "renamesup") { const v = el.value.trim(); if (v) api("PATCH", "/suppliers/" + id, { name: v }).then(loadStore); }
  else if (act === "setcountry") { api("PATCH", "/suppliers/" + id, { country: el.value.trim() }).then(loadStore); }
  else if (act === "setrate") { setRate(el.dataset.cur, el.value); }
  else if (act === "reqbrand") { reqBrand = el.value; render(); }
  else if (act === "reqcust") { reqCust = el.value; render(); }
});
function toggleCmp(bid) { bid = String(bid); let set = [cmpA, cmpB].filter(Boolean); if (set.includes(bid)) set = set.filter(x => x !== bid); else set.push(bid); if (set.length > 2) set = set.slice(-2); cmpA = set[0] || ""; cmpB = set[1] || ""; render(); }
function moveCol(bid, dir) {
  let ord = (colOrder[activeBrand] || _curCols).slice();
  const i = ord.indexOf(bid), j = i + dir;
  if (i < 0 || j < 0 || j >= ord.length) return;
  [ord[i], ord[j]] = [ord[j], ord[i]]; colOrder[activeBrand] = ord; render();
}

// drag/drop import
let _dragN = 0; const veil = document.getElementById("dropveil");
window.addEventListener("dragenter", e => { e.preventDefault(); _dragN++; veil.classList.add("on"); });
window.addEventListener("dragover", e => { e.preventDefault(); });
window.addEventListener("dragleave", e => { _dragN--; if (_dragN <= 0) veil.classList.remove("on"); });
window.addEventListener("drop", e => { e.preventDefault(); _dragN = 0; veil.classList.remove("on"); dropBtnOff(); const f = [...(e.dataTransfer.files || [])].find(f => /\.xlsx?$/i.test(f.name)); if (f) startOfferImport(f); });
// highlight the "Add file" button while a file is dragged over it (the window drop above imports it)
function dropBtnOff() { document.querySelectorAll('[data-drop="offer"].dragon').forEach(b => b.classList.remove("dragon")); }
window.addEventListener("dragover", e => { const b = e.target.closest && e.target.closest('[data-drop="offer"]'); if (b) b.classList.add("dragon"); else dropBtnOff(); });

// ---------- boot ----------
(async function init() {
  try { await loadStore(); } catch (e) { document.getElementById("content").innerHTML = '<div class="alert alert-amber">Could not reach the server: ' + esc(e.message) + "</div>"; return; }
  render();
})();
