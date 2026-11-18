// Frontend de precios Sinalite: pide los datos al servidor local y arma la tabla.
const $ = (id) => document.getElementById(id);
// Si la página se abre desde otro servidor (p. ej. Live Server :5500 o file://), usar la API de server.py
const API = location.port === '8765' ? '' : 'http://localhost:8765';
async function api(path, opts) {
  let r;
  try {
    r = await fetch(API + path, opts);
  } catch {
    throw new Error('No se pudo conectar con el servidor. Ejecuta "py server.py" en la carpeta Scraper_Sinalite.');
  }
  const text = await r.text();
  let d;
  try { d = JSON.parse(text); } catch { throw new Error(`Respuesta inválida del servidor (${r.status}). ¿Está corriendo "py server.py"?`); }
  if (!r.ok || d.error) throw new Error(d.error || r.statusText);
  return d;
}
let DATA = null;
let filters = {}; // grupo -> opción elegida
let qtySel = null; // Set de cantidades a mostrar (null = todas)
let groupSel = {}; // nombre de grupo -> Set de opciones seleccionadas (null = todas)
let selCell = null; // celda seleccionada {r, c}

// ------------------------------------------------------------------ selección de cantidades
const parseQtys = (s) => (s || '').split(/[\s,;]+/).map((x) => x.replace(/[^\d]/g, '')).filter(Boolean);
const qtyOpts = () => (DATA.groups.find((g) => g.name === 'qty') || { options: [] }).options;

function applyQtyInput() {
  const wanted = parseQtys($('qtyInput').value);
  const opts = qtyOpts();
  if (!wanted.length || !opts.length) { qtySel = null; $('qtyNote').textContent = ''; return; }
  const found = wanted.filter((q) => opts.includes(q));
  const missing = wanted.filter((q) => !opts.includes(q));
  qtySel = new Set(found);
  $('qtyNote').textContent = missing.length
    ? `No existen en este producto: ${missing.join(', ')}`
    : '';
}

function syncQtyInput() {
  // refleja las casillas en el campo de texto (vacío = todas)
  const opts = qtyOpts();
  $('qtyInput').value = !qtySel || qtySel.size === opts.length ? '' : opts.filter((q) => qtySel.has(q)).join(', ');
}

function renderQtyChips() {
  const opts = qtyOpts();
  $('qtyBox').hidden = !opts.length;
  $('qtyChips').innerHTML = '';
  opts.forEach((q) => {
    const on = !qtySel || qtySel.has(q);
    const lab = document.createElement('label');
    lab.className = on ? 'on' : '';
    lab.innerHTML = `<input type="checkbox" ${on ? 'checked' : ''}>${Number(q).toLocaleString('en-US')}`;
    lab.onclick = (e) => {
      e.preventDefault();
      if (!qtySel) {
        qtySel = new Set([q]); // aislar desde "todas"
      } else if (qtySel.has(q)) {
        qtySel.delete(q); // deseleccionar (puede quedar vacío)
      } else {
        qtySel.add(q);
        if (qtySel.size === opts.length) qtySel = null; // si están todas → null
      }
      $('qtyNote').textContent = '';
      syncQtyInput();
      renderQtyChips();
      render();
    };
    $('qtyChips').appendChild(lab);
  });
}
const groupFilter = (opts, gname) => {
  if (gname === 'qty' && qtySel) return opts.filter((q) => qtySel.has(q));
  if (groupSel[gname]) return opts.filter((o) => groupSel[gname].has(o));
  return opts;
};

// ------------------------------------------------------------------ chips genéricos para cualquier grupo
function renderGroupChips(gname) {
  const opts = group(gname).options;
  const box = document.createElement('div');
  box.className = 'qtys';
  const title = document.createElement('small');
  title.className = 'qty-title';
  title.textContent = label(gname);
  box.appendChild(title);
  const chips = document.createElement('div');
  chips.className = 'qty-chips';
  opts.forEach((o) => {
    const sel = groupSel[gname];
    const on = !sel || sel.has(o);
    const lab = document.createElement('label');
    lab.className = on ? 'on' : '';
    lab.innerHTML = `<input type="checkbox" ${on ? 'checked' : ''}>${esc(nice(gname, o))}`;
    lab.onclick = (e) => {
      e.preventDefault();
      const sel = groupSel[gname];
      if (!sel) {
        groupSel[gname] = new Set([o]); // aislar desde "todas"
      } else if (sel.has(o)) {
        sel.delete(o); // deseleccionar (puede quedar vacío)
      } else {
        sel.add(o);
        if (sel.size === opts.length) groupSel[gname] = null; // si están todas → null
      }
      selCell = null;
      render();
    };
    chips.appendChild(lab);
  });
  box.appendChild(chips);
  const allBtn = document.createElement('button');
  allBtn.type = 'button'; allBtn.className = 'link'; allBtn.textContent = 'Todas';
  allBtn.onclick = () => { groupSel[gname] = null; selCell = null; render(); };
  const noneBtn = document.createElement('button');
  noneBtn.type = 'button'; noneBtn.className = 'link'; noneBtn.textContent = 'Ninguna';
  noneBtn.onclick = () => { groupSel[gname] = new Set(); selCell = null; render(); };
  box.append(allBtn, document.createTextNode(' '), noneBtn);
  return box;
}

function syncExtraChips(rowG, colG) {
  const box = $('extraChipsBoxes');
  box.innerHTML = '';
  for (const gname of [rowG, colG]) {
    if (gname === 'qty') continue;
    const g = group(gname);
    if (!g || g.options.length <= 1) continue;
    box.appendChild(renderGroupChips(gname));
  }
}

// ------------------------------------------------------------------ cálculo (replica product.getPrice() del sitio)
function calcPrice(sel) {
  const o = { ...sel };
  const nextDay = o.Turnaround === 'Next Business Day';
  const camp = DATA.campaign || { eligible: false, markup: 0 };
  if (nextDay && camp.eligible && 'Turnaround' in o) delete o.Turnaround;
  const qty = parseInt(o.qty, 10);
  let lot = qty, price = 0, mult = 1;
  const size = o.size ?? '';
  if (DATA.variableQty) {
    lot = Math.max(-1, ...DATA.variableQty.filter((s) => s <= qty));
    const bp = DATA.basePrice?.[size]?.[lot];
    if (bp != null) price += parseFloat(bp);
  }
  for (const g in o) {
    const c = DATA.components?.[g]?.[o[g]]?.[size]?.[lot];
    if (!c) continue;
    const [val, lotMode] = c;
    if (val === '-1') return null;
    if (val.charAt(0) === '%') mult += parseFloat(val.substring(1)) / 100;
    else if (lotMode) price += parseFloat(val);
    else price += parseFloat(val) * qty;
  }
  const ppu = (price * DATA.exchangeRate / qty).toFixed(4);
  let total = parseFloat((ppu * qty * mult).toFixed(2));
  if (nextDay && camp.markup > 0) total += (camp.markup * total) / 100;
  return total;
}

function isAvailable(sel) {
  const idx = {};
  DATA.groups.forEach((g) => (idx[g.name] = g.options.indexOf(sel[g.name])));
  const key = DATA.primary.map((n) => idx[n]).join('|');
  const vis = DATA.visibility[key];
  if (!vis) return false;
  if (!DATA.groups.every((g) => (vis[g.name] || []).includes(idx[g.name]))) return false;
  // reglas del sitio donde una opción oculta opciones de otro grupo (p. ej. Grommets -> Next Business Day)
  for (const [gname, byOpt] of Object.entries(DATA.conditional || {})) {
    const diff = byOpt[idx[gname]]?.[key];
    if (diff && Object.entries(diff).some(([n, v]) => !v.includes(idx[n]))) return false;
  }
  return true;
}

function cellValue(sel) {
  if (!isAvailable(sel)) return null;
  const p = calcPrice(sel);
  if (p == null) return null;
  return { p, u: p / parseInt(sel.qty, 10) };
}

// ------------------------------------------------------------------ helpers
const label = (n) => ({ qty: 'Cantidad', size: 'Tamaño' }[n] || n);
const money = (v) => '$' + v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const money4 = (v) => '$' + v.toLocaleString('en-US', { minimumFractionDigits: 4, maximumFractionDigits: 4 });
const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const nice = (g, o) => (g === 'qty' && /^\d+$/.test(o) ? Number(o).toLocaleString('en-US') : o);
const group = (n) => DATA.groups.find((g) => g.name === n);

function setStatus(msg, kind) {
  const s = $('status');
  if (!msg) { s.hidden = true; return; }
  s.hidden = false;
  s.className = 'status' + (kind === 'error' ? ' error' : '');
  s.innerHTML = (kind === 'loading' ? '<span class="spinner"></span>' : '') + msg;
}

// Para los grupos que aún no tienen filtro elige la opción que deja más celdas con precio
function ensureFilters(rowG, colG) {
  const rows = group(rowG).options, cols = group(colG).options;
  const count = (f) => {
    let n = 0;
    for (const r of rows) for (const c of cols) if (isAvailable({ ...f, [rowG]: r, [colG]: c })) n++;
    return n;
  };
  const pending = DATA.groups.filter((g) => g.name !== rowG && g.name !== colG && !(g.name in filters));
  pending.forEach((g) => (filters[g.name] = g.options[0]));
  for (const g of pending) {
    let best = g.options[0], bestN = -1;
    for (const o of g.options) {
      const n = count({ ...filters, [g.name]: o });
      if (n > bestN) { best = o; bestN = n; }
    }
    filters[g.name] = best;
  }
}

// ------------------------------------------------------------------ render
function render() {
  const rowG = $('rowSel').value, colG = $('colSel').value;
  ensureFilters(rowG, colG);
  const unit = $('modeSel').value === 'unit';
  const hide = $('hideEmpty').checked;

  // filtros: todos los grupos que no son filas/columnas
  const fbox = $('filters');
  fbox.innerHTML = '';
  for (const g of DATA.groups) {
    if (g.name === rowG || g.name === colG) continue;
    if (g.name === 'qty' && qtySel && qtySel.size && !qtySel.has(filters.qty)) filters.qty = qtyOpts().find((q) => qtySel.has(q));
    if (!(g.name in filters)) filters[g.name] = g.options[0];
    const lab = document.createElement('label');
    lab.className = 'pill';
    lab.innerHTML = `<small>${esc(label(g.name))}</small>`;
    const s = document.createElement('select');
    groupFilter(g.options, g.name).forEach((o) => s.add(new Option(nice(g.name, o), o, false, o === filters[g.name])));
    s.onchange = () => { filters[g.name] = s.value; selCell = null; render(); };
    lab.appendChild(s);
    fbox.appendChild(lab);
  }

  syncExtraChips(rowG, colG);
  let rows = groupFilter(group(rowG).options, rowG), cols = groupFilter(group(colG).options, colG);
  const grid = rows.map((r) => cols.map((c) => {
    const sel = { ...filters, [rowG]: r, [colG]: c };
    return cellValue(sel);
  }));

  let ri = rows.map((_, i) => i), ci = cols.map((_, j) => j);
  if (hide) {
    ri = ri.filter((i) => grid[i].some((v) => v != null));
    ci = ci.filter((j) => ri.some((i) => grid[i][j] != null));
  }

  // mejor precio por unidad de cada columna
  const best = {};
  ci.forEach((j) => {
    let bi = null;
    ri.forEach((i) => { const v = grid[i][j]; if (v && (bi == null || v.u < grid[bi][j].u - 1e-9)) bi = i; });
    if (bi != null && ri.filter((i) => grid[i][j]).length > 1) best[j] = bi;
  });

  const t = $('priceTable');
  let html = `<thead><tr><th>${esc(label(rowG))} <small>/ ${esc(label(colG))}</small></th>`;
  ci.forEach((j) => (html += `<th>${esc(nice(colG, cols[j]))}</th>`));
  html += '</tr></thead><tbody>';
  if (!ri.length || !ci.length) html += `<tr><td class="empty-msg" colspan="${Math.max(ci.length, 1) + 1}">Selecciona al menos una opción en los filtros de arriba para ver precios</td></tr>`;
  ri.forEach((i) => {
    const rowSel = selCell && selCell.r === rows[i];
    html += `<tr${rowSel ? ' class="row-sel"' : ''}><th>${esc(nice(rowG, rows[i]))}</th>`;
    ci.forEach((j) => {
      const v = grid[i][j];
      if (v == null) { html += '<td class="na">—</td>'; return; }
      const cls = [];
      if (best[j] === i) cls.push('best');
      if (rowSel && selCell.c === cols[j]) cls.push('cell-sel');
      const main = unit ? money4(v.u) : money(v.p);
      const sub = unit ? `${money(v.p)} total` : `${money4(v.u)} c/u`;
      html += `<td class="${cls.join(' ')}" data-r="${esc(rows[i])}" data-c="${esc(cols[j])}"><b>${main}</b><small>${sub}</small></td>`;
    });
    html += '</tr>';
  });
  t.innerHTML = html + '</tbody>';
  t._export = {
    rowG, colG, rows: ri.map((i) => rows[i]), cols: ci.map((j) => cols[j]),
    grid: ri.map((i) => ci.map((j) => (grid[i][j] ? (unit ? grid[i][j].u : grid[i][j].p) : null))),
  };
}

function showProduct(data) {
  DATA = data;
  filters = {};
  // Iniciar todos los grupos sin nada seleccionado; el usuario elige lo que quiere ver
  groupSel = {};
  data.groups.forEach((g) => { groupSel[g.name] = new Set(); });
  qtySel = new Set();
  $('empty').hidden = true;
  $('product').hidden = false;
  selCell = null;
  $('pName').textContent = data.product.name;
  const tags = [data.product.category, `ID ${data.product.id}`, data.currency].filter(Boolean)
    .map((t) => `<span class="tag">${esc(t)}</span>`).join('');
  $('pMeta').innerHTML = tags + `<span class="tag live">● Actualizado ${esc(fmtDate(data.scrapedAt))}</span>`;
  $('providerLink').href = data.url;
  $('refreshBtn').hidden = false;
  $('crumbs').innerHTML = `Buscar producto &nbsp;/&nbsp; ${data.product.category ? esc(data.product.category) + ' &nbsp;/&nbsp; ' : ''}<b>${esc(data.product.name)}</b>`;
  $('cur').textContent = data.currency;
  $('urlInput').value = data.url;
  applyQtyInput();
  // Si el campo de cantidades estaba vacío, mantener la selección vacía (nada seleccionado)
  if (!$('qtyInput').value.trim()) qtySel = new Set();
  renderQtyChips();
  $('warnings').innerHTML = (data.warnings || []).map((w) => `<div>${w}</div>`).join('');

  const names = data.groups.map((g) => g.name);
  const fill = (sel, def) => {
    sel.innerHTML = '';
    names.forEach((n) => sel.add(new Option(label(n), n, false, n === def)));
  };
  const defRow = names.includes('size') ? 'size' : names[0];
  const defCol = names.includes('qty') ? 'qty' : names.find((n) => n !== defRow);
  fill($('rowSel'), defRow);
  fill($('colSel'), defCol);
  location.hash = data.slug;
  document.title = data.product.name + ' · Tarifario Sinalite';
  markHistory();
  render();
}

// ------------------------------------------------------------------ CSV
function downloadCSV(name, rows) {
  const esc = (v) => (v == null ? '' : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : v);
  const csv = '﻿' + rows.map((r) => r.map(esc).join(',')).join('\r\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  a.download = name;
  a.click();
}

$('csvBtn').onclick = () => {
  const e = $('priceTable')._export;
  const info = Object.entries(filters).filter(([k]) => k !== e.rowG && k !== e.colG).map(([k, v]) => `${k}=${v}`).join('; ');
  const rows = [[`${label(e.rowG)} \\ ${label(e.colG)}`, ...e.cols]];
  e.rows.forEach((r, i) => rows.push([r, ...e.grid[i].map((v) => (v == null ? '' : v.toFixed($('modeSel').value === 'unit' ? 4 : 2)))]));
  rows.push([], ['Filtros: ' + info]);
  downloadCSV(`${DATA.slug}-tabla.csv`, rows);
};

$('csvAllBtn').onclick = () => {
  const gs = DATA.groups;
  const total = gs.reduce((n, g) => n * g.options.length, 1);
  if (total > 400000 && !confirm(`Son ${total.toLocaleString()} combinaciones posibles. ¿Generar el CSV igual?`)) return;
  const out = [[...gs.map((g) => g.name), 'Precio', 'Precio unitario']];
  const rec = (k, sel) => {
    if (k === gs.length) {
      if (!isAvailable(sel)) return;
      const p = calcPrice(sel);
      if (p == null) return;
      out.push([...gs.map((g) => sel[g.name]), p.toFixed(2), (p / parseInt(sel.qty, 10)).toFixed(4)]);
      return;
    }
    for (const o of groupFilter(gs[k].options, gs[k].name)) rec(k + 1, { ...sel, [gs[k].name]: o });
  };
  rec(0, {});
  downloadCSV(`${DATA.slug}-todas.csv`, out);
};

// ------------------------------------------------------------------ carga / scrape
async function scrape(url) {
  $('scrapeBtn').disabled = $('refreshBtn').disabled = true;
  setStatus('Obteniendo precios desde Sinalite… (≈1 minuto)', 'loading');
  try {
    const d = await api('/api/scrape', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url }) });
    setStatus('');
    showProduct(d);
    loadHistory();
  } catch (e) {
    setStatus('Error: ' + e.message, 'error');
  } finally {
    $('scrapeBtn').disabled = $('refreshBtn').disabled = false;
  }
}

async function loadSaved(slug) {
  setStatus('Cargando…', 'loading');
  try {
    showProduct(await api('/api/product/' + encodeURIComponent(slug)));
    setStatus('');
  } catch (e) {
    setStatus('Error: ' + e.message, 'error');
  }
}

let HISTORY = [];
const THUMBS = [
  'linear-gradient(135deg,#00a6d6,#e2337a)', 'linear-gradient(135deg,#ffc22e,#e2337a)',
  'linear-gradient(135deg,#0f1e33,#00a6d6)', 'linear-gradient(135deg,#16895a,#00a6d6)',
];
function fmtDate(s) {
  const d = new Date((s || '').replace(' ', 'T'));
  if (isNaN(d)) return s || '';
  const hm = d.toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit' });
  const days = Math.round((new Date(new Date().toDateString()) - new Date(d.toDateString())) / 864e5);
  if (days === 0) return 'hoy ' + hm;
  if (days === 1) return 'ayer ' + hm;
  return d.toLocaleDateString('es', { day: 'numeric', month: 'short' }) + ' ' + hm;
}
async function loadHistory() {
  try {
    HISTORY = await api('/api/products');
  } catch { HISTORY = []; }
  $('history').innerHTML = HISTORY.map((h, i) => {
    const words = h.name.replace(/[^A-Za-z0-9 ]/g, ' ').trim().split(/\s+/);
    const ini = (words.filter((w) => /^[A-Za-z]/.test(w)).slice(0, 2).map((w) => w[0]).join('') || words[0][0]).toUpperCase();
    return `<a href="#${esc(h.slug)}" data-slug="${esc(h.slug)}" title="${esc(h.url)}">` +
      `<span class="thumb" style="background:${THUMBS[i % THUMBS.length]}">${esc(ini)}</span>` +
      `<span><b>${esc(h.name)}</b><small>${esc(fmtDate(h.scrapedAt))}</small></span></a>`;
  }).join('');
  $('historyEmpty').hidden = HISTORY.length > 0;
  markHistory();
}
function markHistory() {
  document.querySelectorAll('#history a').forEach((a) => a.classList.toggle('active', DATA && a.dataset.slug === DATA.slug));
}

$('scrapeForm').onsubmit = (e) => {
  e.preventDefault();
  const url = $('urlInput').value.trim();
  // mismo producto ya cargado: solo aplicar las cantidades, sin volver a scrapear
  if (DATA && url === DATA.url) { applyQtyInput(); renderQtyChips(); render(); return; }
  scrape(url);
};
$('qtyInput').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && DATA && $('urlInput').value.trim() === DATA.url) { e.preventDefault(); applyQtyInput(); renderQtyChips(); render(); }
});
$('qtyAll').onclick = () => { qtySel = null; $('qtyNote').textContent = ''; syncQtyInput(); renderQtyChips(); render(); };
$('qtyNone').onclick = () => { qtySel = new Set(); $('qtyNote').textContent = ''; syncQtyInput(); renderQtyChips(); render(); };
$('refreshBtn').onclick = () => DATA && scrape(DATA.url);
['rowSel', 'colSel'].forEach((id) => ($(id).onchange = () => {
  // evitar que filas y columnas sean el mismo grupo
  const other = id === 'rowSel' ? 'colSel' : 'rowSel';
  if ($('rowSel').value === $('colSel').value) {
    $(other).value = DATA.groups.map((g) => g.name).find((n) => n !== $(id).value);
  }
  selCell = null;
  render();
}));
document.querySelectorAll('#modeSeg button').forEach((b) => (b.onclick = () => {
  $('modeSel').value = b.dataset.mode;
  document.querySelectorAll('#modeSeg button').forEach((x) => x.classList.toggle('on', x === b));
  render();
}));
$('priceTable').addEventListener('click', (e) => {
  const td = e.target.closest('td[data-r]');
  if (!td) return;
  const same = selCell && selCell.r === td.dataset.r && selCell.c === td.dataset.c;
  selCell = same ? null : { r: td.dataset.r, c: td.dataset.c };
  render();
});
$('hideEmpty').onchange = render;
window.addEventListener('hashchange', () => {
  const slug = location.hash.slice(1);
  if (slug && (!DATA || DATA.slug !== slug)) loadSaved(slug);
});

loadHistory().then(() => {
  const slug = location.hash.slice(1);
  if (slug) loadSaved(slug);
});
