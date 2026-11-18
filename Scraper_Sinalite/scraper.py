"""
Scraper de precios Sinalite.

Dado el link de un producto de sinalite.com:
  1. Inicia sesión (credenciales en .env) y reutiliza la sesión en storage_state.json.
  2. Abre el producto y lee los datos de precio que la propia web carga
     (api.sinalite.com/product/<id>/<store>), la tasa de cambio y los grupos de opciones.
  3. Usa la función del sitio `product.applyExclusions()` para saber qué opciones están
     disponibles en cada combinación de Stock/Shape + Size + Qty (respeta las excepciones
     que el sitio tiene codificadas por producto).
  4. Guarda todo en data/<producto>.json, que el frontend usa para armar la tabla de precios.

Uso:
  py scraper.py <url>                 # scrapea, guarda JSON e imprime un resumen
  py scraper.py <url> --abrir         # además levanta el frontend y lo abre en el navegador
  py scraper.py <url> --ver           # navegador visible (para depurar)
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
STATE_FILE = BASE_DIR / "storage_state.json"
LOGIN_URL = "https://sinalite.com/en_ca/customer/account/login/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
TIMEOUT = 60_000  # ms

# Grupos que determinan qué otras opciones están disponibles (el sitio
# calcula la disponibilidad con size + qty y tiene excepciones por Stock/Shape).
PRIMARY_GROUPS = ("shape", "stock", "size", "qty")


def load_env():
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def md5c(s):
    return hashlib.md5(s.lower().encode("utf8")).hexdigest()


def slugify(url):
    path = re.sub(r"\.html?$", "", url.split("?")[0].rstrip("/"))
    parts = [p for p in path.split("/") if p][-2:]
    return re.sub(r"[^a-z0-9]+", "-", "-".join(parts).lower()).strip("-")


for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def log(msg):
    print(msg, flush=True)


# --------------------------------------------------------------------------- login

def is_logged_in(page):
    return page.locator("a:has-text('Sign Out'), a:has-text('Déconnexion')").count() > 0


def login(page):
    user, pwd = os.getenv("SINALITE_USER"), os.getenv("SINALITE_PASSWORD")
    if not user or not pwd:
        raise RuntimeError("Faltan SINALITE_USER / SINALITE_PASSWORD en .env")
    log("Iniciando sesión en Sinalite...")
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=TIMEOUT)
    page.locator("#email").wait_for(timeout=TIMEOUT)
    page.fill("#email", user)
    pass_input = page.locator("input[name='login[password]']")
    pass_input.fill(pwd)
    pass_input.press("Enter")
    page.wait_for_url(lambda u: "/customer/account/login" not in u, timeout=TIMEOUT)
    page.wait_for_load_state("domcontentloaded")
    log("Sesión iniciada.")


# --------------------------------------------------------------------------- JS en página

JS_READ_PAGE = """
() => {
  const groups = [];
  const seen = {};
  document.querySelectorAll('#product .productOption').forEach(e => {
    if (e.tagName === 'SELECT') {
      groups.push({name: e.name, kind: 'select',
        options: [...e.options].filter(o => o.value !== '').map(o => o.innerHTML)});
    } else if (e.tagName === 'INPUT' && (e.type === 'radio' || e.type === 'checkbox')) {
      if (!seen[e.name]) { seen[e.name] = {name: e.name, kind: 'radio', options: []}; groups.push(seen[e.name]); }
      const t = e.getAttribute('text');
      if (t !== null && !seen[e.name].options.includes(t)) seen[e.name].options.push(t);
    }
  });
  let eligible = false;
  try { eligible = !!product.isEligibleProductForTurnaroundCampaign(); } catch (err) {}
  const h1 = document.querySelector('.page-title, h1');
  return {
    id: product.currentProduct,
    store: product.options.storeCode,
    name: (product.productData && product.productData.name) || (h1 ? h1.innerText.trim() : ''),
    category: (product.productData && product.productData.category) || '',
    sku: (product.productData && product.productData.sku) || '',
    title: document.title,
    exchangeRate: window.pricingData.exchangeRate,
    meta: product.cache.productMetaData || {},
    campaign: {eligible: eligible, markup: parseFloat(window.turnaroundCampMarkup || 0) || 0},
    groups: groups,
    priceData: product.cache.priceData,
    loggedIn: !!document.querySelector('a[href*="logout"]') || /Sign Out|Déconnexion/.test(document.body.innerText),
  };
}
"""

# Evaluador de disponibilidad que corre dentro de la página. Cada tarea es
# [idx_primarios, grupo_override|null, idx_override]: fija esa combinación (resto de
# grupos en su primera opción), ejecuta product.applyExclusions() del sitio y devuelve
# los índices de opciones visibles de cada grupo. Corre asíncrono (en lotes) para poder
# repartir el trabajo entre varias pestañas en paralelo.
JS_VIS_START = """
([tasks, groups, primary]) => {
  const els = {};
  groups.forEach(g => {
    els[g.name] = g.kind === 'select'
      ? document.querySelector('#product select.productOption[name="' + g.name + '"]')
      : [...document.querySelectorAll('#product input.productOption[name="' + g.name + '"]')];
  });
  const setVal = (g, text) => {
    const el = els[g.name];
    if (g.kind === 'select') {
      const o = [...el.options].find(o => o.innerHTML === text);
      if (o) el.value = o.value;
    } else {
      el.forEach(r => { r.checked = (r.getAttribute('text') === text); });
    }
  };
  const visible = (g) => {
    const el = els[g.name];
    const out = [];
    if (g.kind === 'select') {
      [...el.options].filter(o => o.value !== '').forEach((o, i) => {
        if (o.style.display !== 'none' && !o.disabled) out.push(i);
      });
    } else {
      g.options.forEach((t, i) => {
        const r = el.find(r => r.getAttribute('text') === t);
        const box = r && (r.closest('label') || r.parentElement);
        if (r && !r.disabled && r.style.display !== 'none' && (!box || box.style.display !== 'none')) out.push(i);
      });
    }
    return out;
  };
  const pgroups = primary.map(n => groups.find(g => g.name === n));
  const byName = {};
  groups.forEach(g => byName[g.name] = g);
  window.__vis = {done: false, results: [], error: null};
  let i = 0;
  const step = () => {
    try {
      const until = Math.min(tasks.length, i + 40);
      for (; i < until; i++) {
        const [idx, oname, oidx] = tasks[i];
        // applyExclusions resetea los selects cuya opción quedó oculta: se re-fija todo cada vez
        groups.forEach(g => { if (g.options.length) setVal(g, g.options[0]); });
        pgroups.forEach((pg, j) => setVal(pg, pg.options[idx[j]]));
        if (oname) setVal(byName[oname], byName[oname].options[oidx]);
        try { product.applyExclusions(); } catch (err) {}
        const vis = {};
        groups.forEach(g => { vis[g.name] = visible(g); });
        window.__vis.results.push(vis);
      }
      if (i < tasks.length) setTimeout(step, 0); else window.__vis.done = true;
    } catch (err) { window.__vis.error = String(err); window.__vis.done = true; }
  };
  setTimeout(step, 0);
  return tasks.length;
}
"""

JS_READY = (
    "window.product && product.cache && product.cache.priceData"
    " && document.querySelectorAll('#product select.productOption option').length > 2"
)


# --------------------------------------------------------------------------- componentes de precio

def build_components(info):
    """Para cada grupo/opción/size/qty extrae del priceData los valores que usa
    la fórmula del sitio: valor (#qty), modo lote (#qty_each_lot) y días extra (#qty_t_inc)."""
    pd = info["priceData"]
    groups = info["groups"]
    size_group = next((g for g in groups if g["name"] == "size"), None)
    sizes = size_group["options"] if size_group else [""]
    meta = info["meta"] or {}

    if meta.get("variable_qty"):
        qtys = sorted({int(q) for q in meta["variable_qty"]})
    else:
        qg = next((g for g in groups if g["name"] == "qty"), None)
        qtys = [int(q) for q in qg["options"]] if qg else []

    comp = {}  # comp[group][option][size][qty] = [valor, lote(0/1)]
    for g in groups:
        gname = g["name"]
        for opt in g["options"]:
            for s in sizes:
                for q in qtys:
                    base = f"#{gname}#{gname}_{opt}#size#size_{s}#"
                    val = pd.get(md5c(base + f"qty#qty_{q}"))
                    if val is None:
                        continue
                    lot = pd.get(md5c(base + f"qty_each_lot#qty_{q}"))
                    # En el JS del sitio cualquier string no vacío es "truthy" (incluido "0")
                    lot_mode = 1 if (lot is not None and str(lot) != "") else 0
                    comp.setdefault(gname, {}).setdefault(opt, {}).setdefault(s, {})[str(q)] = [str(val), lot_mode]

    base_price = {}
    if meta.get("variable_qty"):
        for s in sizes:
            for q in qtys:
                v = pd.get(md5c(f"#Base price#size#size_{s}#qty#qty_{q}"))
                if v is not None:
                    base_price.setdefault(s, {})[str(q)] = str(v)
    return comp, base_price, qtys


# --------------------------------------------------------------------------- disponibilidad

N_WORKERS = 4        # pestañas en paralelo para evaluar disponibilidad
DEP_SAMPLE = 60      # combinaciones de muestra para detectar dependencias entre grupos


def open_product_page(ctx, url):
    page = ctx.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT)
    page.wait_for_function(JS_READY, timeout=TIMEOUT)
    page.wait_for_timeout(1000)
    return page


def run_tasks(pages, tasks, groups, primary):
    """Reparte las tareas entre las pestañas, las corre en paralelo y devuelve los resultados en orden."""
    if not tasks:
        return []
    n = len(pages)
    chunks = [tasks[i::n] for i in range(n)]
    for page, chunk in zip(pages, chunks):
        page.evaluate(JS_VIS_START, [chunk, groups, primary])
    pending = set(range(n))
    while pending:
        time.sleep(0.4)
        for i in list(pending):
            st = pages[i].evaluate("() => [window.__vis.done, window.__vis.error]")
            if st[0]:
                if st[1]:
                    raise RuntimeError("Error evaluando disponibilidad: " + st[1])
                pending.discard(i)
    parts = [pages[i].evaluate("() => window.__vis.results") for i in range(n)]
    out = [None] * len(tasks)
    for i, part in enumerate(parts):
        for j, r in enumerate(part):
            out[i + j * n] = r
    return out


def compute_availability(pages, groups, primary):
    """Devuelve (visibility, conditional).
    visibility[key]                 -> opciones visibles con el resto de grupos en su 1ª opción
    conditional[grupo][idx][key]    -> {otro_grupo: visibles} cuando 'grupo' toma la opción idx
                                       (solo grupos que cambian la disponibilidad de otros)."""
    pg = [next(g for g in groups if g["name"] == n) for n in primary]
    keys = [[]]
    for g in pg:
        keys = [k + [i] for k in keys for i in range(len(g["options"]))]
    kstr = lambda k: "|".join(map(str, k))

    log(f"  · {len(keys)} combinaciones base ({', '.join(primary)})")
    base_res = run_tasks(pages, [[k, None, 0] for k in keys], groups, primary)
    visibility = {kstr(k): r for k, r in zip(keys, base_res)}

    # Detectar grupos no primarios cuyas opciones ocultan/muestran opciones de otros grupos
    others = [g for g in groups if g["name"] not in primary and len(g["options"]) > 1]
    if not others:
        return visibility, {}
    step = max(1, len(keys) // DEP_SAMPLE)
    sample = keys[::step][:DEP_SAMPLE]
    det_tasks = [[k, g["name"], i] for g in others for i in range(1, len(g["options"])) for k in sample]
    det_res = run_tasks(pages, det_tasks, groups, primary)
    dependent = []
    for (k, gname, _), r in zip(det_tasks, det_res):
        base = visibility[kstr(k)]
        if gname not in dependent and any(r[n] != base[n] for n in r if n != gname):
            dependent.append(gname)
    if not dependent:
        log("  · sin dependencias entre opciones")
        return visibility, {}

    log(f"  · opciones que afectan a otras: {', '.join(dependent)} — calculando...")
    conditional = {}
    tasks = [[k, gname, i] for gname in dependent
             for i in range(1, len(next(g for g in groups if g["name"] == gname)["options"]))
             for k in keys]
    res = run_tasks(pages, tasks, groups, primary)
    for (k, gname, i), r in zip(tasks, res):
        base = visibility[kstr(k)]
        diff = {n: v for n, v in r.items() if n != gname and v != base[n]}
        if diff:
            conditional.setdefault(gname, {}).setdefault(str(i), {})[kstr(k)] = diff
    return visibility, conditional


# --------------------------------------------------------------------------- scrape principal

def scrape(url, headless=True):
    load_env()
    if "sinalite.com" not in url:
        raise ValueError("El link debe ser de sinalite.com")
    t0 = time.time()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx_args = {"user_agent": USER_AGENT, "viewport": {"width": 1366, "height": 900}}
        if STATE_FILE.exists():
            ctx_args["storage_state"] = str(STATE_FILE)
        ctx = browser.new_context(**ctx_args)
        page = ctx.new_page()

        log(f"Abriendo producto: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT)
        if not is_logged_in(page):
            login(page)
            ctx.storage_state(path=str(STATE_FILE))
            page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT)

        try:
            page.wait_for_function(JS_READY, timeout=TIMEOUT)
        except Exception:
            raise RuntimeError("La página no cargó la calculadora de precios (¿es un link de producto válido?)")
        page.wait_for_timeout(1500)

        info = page.evaluate(JS_READ_PAGE)
        log(f"Producto: {info['name']} (id {info['id']}, tienda {info['store']}) — "
            f"{len(info['priceData'])} registros de precio")

        meta = info["meta"] or {}
        warnings = []
        if meta.get("plusplus"):
            warnings.append("Este producto calcula el precio en el servidor (plusplus); "
                            "los precios mostrados pueden no ser exactos.")

        names = [g["name"] for g in info["groups"]]
        primary = [n for n in names if n.lower() in PRIMARY_GROUPS]
        log(f"Grupos de opciones: {', '.join(names)}")
        log("Calculando qué combinaciones están disponibles...")
        ctx.storage_state(path=str(STATE_FILE))
        pages = [page] + [open_product_page(ctx, url) for _ in range(N_WORKERS - 1)]
        visibility, conditional = compute_availability(pages, info["groups"], primary)
        browser.close()

    comp, base_price, qtys = build_components(info)
    data = {
        "url": url,
        "scrapedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "product": {k: info[k] for k in ("id", "store", "name", "category", "sku", "title")},
        "exchangeRate": info["exchangeRate"],
        "currency": "CAD" if str(info["store"]).endswith("_ca") else "USD",
        "meta": meta,
        "campaign": info["campaign"],
        "groups": [{"name": g["name"], "options": g["options"]} for g in info["groups"]],
        "variableQty": qtys if meta.get("variable_qty") else None,
        "primary": primary,
        "visibility": visibility,
        "conditional": conditional,
        "components": comp,
        "basePrice": base_price,
        "warnings": warnings,
    }
    DATA_DIR.mkdir(exist_ok=True)
    slug = slugify(url)
    data["slug"] = slug
    out = DATA_DIR / f"{slug}.json"
    out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf8")
    log(f"Listo en {time.time() - t0:.1f}s → {out}")
    return data


# --------------------------------------------------------------------------- cálculo (igual que el sitio)

def calc_price(data, sel):
    """Replica product.getPrice() del sitio. sel = {grupo: opción}. Devuelve float o None si no es válida."""
    o = dict(sel)
    next_day = o.get("Turnaround") == "Next Business Day"
    camp = data["campaign"]
    if next_day and camp["eligible"] and "Turnaround" in o:
        del o["Turnaround"]
    qty = int(o["qty"])
    lot = qty
    price, mult = 0.0, 1.0
    size = o.get("size", "")
    if data.get("variableQty"):
        steps = data["variableQty"]
        lot = max([s for s in steps if s <= qty] or [-1])
        bp = data["basePrice"].get(size, {}).get(str(lot))
        if bp is not None:
            price += float(bp)
    for g, v in o.items():
        c = data["components"].get(g, {}).get(v, {}).get(size, {}).get(str(lot))
        if c is None:
            continue
        val, lot_mode = c
        if val == "-1":
            return None
        if val.startswith("%"):
            mult += float(val[1:]) / 100
        elif lot_mode:
            price += float(val)
        else:
            price += float(val) * qty
    ppu = round(price * data["exchangeRate"] / qty, 4)
    total = round(ppu * qty * mult, 2)
    if next_day and camp["markup"] > 0:
        total = total + camp["markup"] * total / 100
    return total


def is_available(data, sel):
    idx = {g["name"]: g["options"].index(sel[g["name"]]) for g in data["groups"]}
    key = "|".join(str(idx[n]) for n in data["primary"])
    vis = data["visibility"].get(key)
    if not vis:
        return False
    if any(idx[n] not in vis.get(n, []) for n in idx):
        return False
    for gname, by_opt in (data.get("conditional") or {}).items():
        diff = by_opt.get(str(idx[gname]), {}).get(key)
        if diff and any(idx[n] not in v for n, v in diff.items()):
            return False
    return True


def default_filters(data, row_g, col_g):
    """Para cada grupo fijo elige (en orden) la opción que deja más celdas con precio."""
    groups = {g["name"]: g["options"] for g in data["groups"]}
    fixed = {n: opts[0] for n, opts in groups.items() if n not in (row_g, col_g)}

    def count(f):
        return sum(1 for r in groups[row_g] for c in groups[col_g]
                   if is_available(data, dict(f, **{row_g: r, col_g: c})))

    for n in list(fixed):
        best, best_n = fixed[n], -1
        for opt in groups[n]:
            k = count(dict(fixed, **{n: opt}))
            if k > best_n:
                best, best_n = opt, k
        fixed[n] = best
    return fixed


def print_summary(data, qtys=None):
    groups = {g["name"]: g["options"] for g in data["groups"]}
    if "size" not in groups or "qty" not in groups:
        return
    fixed = default_filters(data, "size", "qty")
    if qtys:
        missing = [q for q in qtys if q not in groups["qty"]]
        if missing:
            log(f"Cantidades que no existen en este producto: {', '.join(missing)}")
        qtys = [q for q in qtys if q in groups["qty"]] or groups["qty"][:8]
    else:
        qtys = groups["qty"][:8]
    log("\nResumen (" + ", ".join(f"{k}: {v}" for k, v in fixed.items()) + ")")
    head = f"{'Size':<14}" + "".join(f"{q:>11}" for q in qtys)
    log(head)
    log("-" * len(head))
    for s in groups["size"]:
        row = f"{s:<14}"
        for q in qtys:
            sel = dict(fixed, size=s, qty=q)
            p = calc_price(data, sel) if is_available(data, sel) else None
            row += f"{('$%.2f' % p) if p is not None else '—':>11}"
        log(row)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Scraper de precios Sinalite")
    ap.add_argument("url", nargs="?", help="Link del producto en sinalite.com")
    ap.add_argument("--abrir", action="store_true", help="Levantar el frontend y abrirlo")
    ap.add_argument("--ver", action="store_true", help="Navegador visible")
    ap.add_argument("--qty", help="Cantidades a mostrar, ej. 250,500,1000")
    args = ap.parse_args()
    url = args.url or input("Link del producto Sinalite: ").strip()
    try:
        d = scrape(url, headless=not args.ver)
    except Exception as e:
        log(f"ERROR: {e}")
        sys.exit(1)
    print_summary(d, [q for q in re.split(r"[\s,;]+", args.qty or "") if q])
    if args.abrir:
        import server
        server.run(open_slug=d["slug"])
