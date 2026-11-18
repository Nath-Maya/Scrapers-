"""
Scraper DSR:
  1. Login en https://dsr.prestodemos.com/#/dsr
  2. Click en la flecha (KeyboardArrowDownIcon)
  3. Settings -> Prepress
  4. Por cada regla de reglas.json: en el Website indicado, asigna el operador
     a todos los productos que contengan el filtro (sin importar mayúsculas),
     saltando los que ya tienen un operador asignado.

Uso:
  py scraper.py --dry-run          # solo muestra lo que haría
  py scraper.py                    # aplica las asignaciones
  py scraper.py --reglas otro.json --headless
  py scraper.py --solo 8           # solo la regla 8 del json (o varias: --solo 7,8)
"""
import argparse
import json
import os
import re
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

from Scraper_pressOperator.clasificacion import clasificar

# Carga el .env que está junto a este archivo (sin dependencias externas)
def _load_dotenv():
    env_path = Path(__file__).parent / ".env"
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass

_load_dotenv()

URL = "https://dsr.prestodemos.com/#/dsr"
USER = os.getenv("DSR_USER") or ""
PASSWORD = os.getenv("DSR_PASSWORD") or ""

if not USER or not PASSWORD:
    raise SystemExit(
        "Error: falta DSR_USER o DSR_PASSWORD.\n"
        "Crea el archivo Scraper_pressOperator/.env con:\n"
        "  DSR_USER=tu_usuario\n"
        "  DSR_PASSWORD=tu_contraseña"
    )

TIMEOUT = 20_000  # ms
SLOW_MO = 0  # ms entre acciones; subir (p.ej. 300) para ver el paso a paso


def visible_inputs(page):
    return [i for i in page.locator("input").all() if i.is_visible()]


def login(page):
    page.goto(URL, wait_until="domcontentloaded")
    page.locator("input").first.wait_for(timeout=TIMEOUT)
    page.wait_for_timeout(1000)

    inputs = visible_inputs(page)
    print(f"Inputs visibles en login: {len(inputs)}")

    # Usuario: primer input visible
    inputs[0].click()
    inputs[0].fill(USER)

    # Contraseña: input type=password, o el segundo input visible
    pwd = page.locator("input[type='password']")
    if pwd.count() == 0 and len(inputs) > 1:
        pwd = inputs[1]
    elif pwd.count() == 0:
        # Login en dos pasos: enviar usuario y esperar el campo de contraseña
        page.keyboard.press("Enter")
        pwd = page.locator("input[type='password']")
        pwd.first.wait_for(timeout=TIMEOUT)
    else:
        pwd = pwd.first

    pwd.click()
    pwd.fill(PASSWORD)

    # Login exitoso = aparece la flecha del menú. A veces el primer click no responde: reintentar
    boton = page.get_by_role("button", name=re.compile(r"sign in", re.IGNORECASE))
    flecha = page.locator("svg[data-testid='KeyboardArrowDownIcon']").first
    for intento in range(3):
        # Si sigue "cargando" (deshabilitado) no hay que volver a hacer click, solo esperar
        if boton.is_visible() and boton.is_enabled():
            boton.click()
        try:
            flecha.wait_for(timeout=8_000)
            break
        except Exception:
            print(f"Login sin respuesta (intento {intento + 1}), reintentando...")
    else:
        flecha.wait_for(timeout=TIMEOUT)
    print("Login OK ->", page.url)


def menu_item(page, texto):
    """<p class="MuiTypography-root"> visible con el texto exacto, sin importar mayúsculas."""
    patron = re.compile(rf"^\s*{texto}\s*$", re.IGNORECASE)
    item = page.locator("p.MuiTypography-root", has_text=patron).locator("visible=true").first
    item.wait_for(timeout=TIMEOUT)
    return item


def ir_a_prepress(page):
    # Flecha del menú desplegable (click en el botón que la contiene)
    arrow = page.locator("svg[data-testid='KeyboardArrowDownIcon']").first
    arrow.locator("xpath=ancestor-or-self::*[self::button or @role='button'][1]").or_(arrow).first.click()

    menu_item(page, "settings").click()
    menu_item(page, "prepress").click()

    page.wait_for_load_state("networkidle")
    print("En Prepress ->", page.url)


def norm(texto):
    return " ".join(texto.split()).lower()


def dialogo_prepress(page):
    dlg = page.get_by_role("dialog").filter(has_text="Prepress product assignments")
    dlg.wait_for(timeout=TIMEOUT)
    # El modal abre con un spinner mientras carga datos; hasta entonces los combos dicen "No options"
    dlg.locator("[role='row'][data-id]").first.wait_for(timeout=TIMEOUT * 3)
    return dlg


def combo(dlg, etiqueta):
    """Autocomplete por su etiqueta (los ids :rXX: cambian en cada carga)."""
    return dlg.get_by_role("combobox", name=etiqueta, exact=True)


def seleccionar(page, campo, texto, busqueda=None):
    """Escribe en el autocomplete y elige la opción con el texto exacto.
    busqueda: lo que se escribe (por defecto el propio texto); puede ser una lista de alternativas."""
    opcion = page.get_by_role("listbox").get_by_role("option", name=texto, exact=True).first
    busquedas = busqueda if isinstance(busqueda, list) else [busqueda or texto]
    for intento in range(3):
        for b in busquedas:
            campo.click()
            campo.fill(b)
            try:
                opcion.click(timeout=8_000)
                return
            except Exception:
                # La lista se re-renderiza a veces mientras carga, o esa búsqueda no la muestra
                campo.press("Escape")
    raise RuntimeError(f"No se pudo seleccionar la opción: {texto}")


def listar_opciones(page, campo, filtro):
    """Todas las opciones del autocomplete que contienen el filtro (sin importar mayúsculas)."""
    campo.click()
    campo.fill(filtro)
    listbox = page.get_by_role("listbox")
    popper = page.locator(".MuiAutocomplete-popper")
    # Esperar a la lista o a un "No options" real. En sitios con miles de productos aparece
    # "No options" un momento mientras filtra: solo vale si se mantiene 5 s seguidos
    sin_opciones = 0
    for _ in range(int(TIMEOUT * 2 / 500)):
        if listbox.get_by_role("option").count():
            break
        if popper.count() and "no options" in popper.first.inner_text().lower():
            sin_opciones += 1
            if sin_opciones >= 10:
                campo.press("Escape")
                return []
        else:
            sin_opciones = 0
        page.wait_for_timeout(500)
    else:
        campo.press("Escape")
        raise RuntimeError(f"La lista de opciones no terminó de cargar (filtro '{filtro}')")

    # La lista puede cargar por partes: hacer scroll hasta que no aparezcan más
    textos = []
    while True:
        nuevos = [t for t in listbox.get_by_role("option").all_inner_texts() if t not in textos]
        if not nuevos:
            break
        textos += nuevos
        listbox.evaluate("el => el.scrollTop = el.scrollHeight")
        page.wait_for_timeout(300)

    campo.press("Escape")
    f = norm(filtro)
    return [t.strip() for t in textos if f in norm(t)]


def buscar_en_tabla(page, dlg, texto):
    """Filtra la tabla y espera a que se estabilice (el buscador tiene debounce)."""
    dlg.get_by_placeholder("Search assignments...").fill(texto)
    pie = dlg.locator(".MuiTablePagination-displayedRows")
    page.wait_for_timeout(600)
    previo = None
    for _ in range(20):
        actual = (pie.inner_text(), dlg.locator("[role='row'][data-id]").all_inner_texts())
        if actual == previo:
            break
        previo = actual
        page.wait_for_timeout(300)

    # La tabla conserva la página en la que estaba: volver a la primera
    anterior = dlg.get_by_role("button", name="Go to previous page")
    while anterior.count() and anterior.is_enabled():
        anterior.click()
        page.wait_for_timeout(300)


def filas_tabla(page, dlg):
    """Recorre todas las filas visibles de todas las páginas: (row, website, "producto - sku", operador).
    La tabla (MUI DataGrid) solo pinta las filas visibles, así que se hace scroll."""
    scroller = dlg.locator(".MuiDataGrid-virtualScroller")
    vistos = set()
    while True:
        antes = len(vistos)
        for row in dlg.locator("[role='row'][data-id]").all():
            rid = row.get_attribute("data-id")
            if rid in vistos:
                continue
            vistos.add(rid)
            web = row.locator("[data-field='website_name']").inner_text().strip()
            prod = row.locator("[data-field='product_name']").inner_text().strip()
            sku = row.locator("[data-field='sku']").inner_text().strip()
            op = row.locator("[data-field='operator_name']").inner_text().strip()
            # Las opciones del autocomplete vienen como "Producto - SKU"
            yield row, web, f"{prod} - {sku}", op
        if len(vistos) == antes:
            # Fin de la página actual: pasar a la siguiente si existe
            siguiente = dlg.get_by_role("button", name="Go to next page")
            if siguiente.count() and siguiente.is_enabled():
                siguiente.click()
                page.wait_for_timeout(500)
                continue
            break
        scroller.evaluate("el => el.scrollTop += el.clientHeight")
        page.wait_for_timeout(200)


def leer_asignaciones(page, dlg, busqueda):
    """{(website, "producto - sku"): (website, operador)} ya asignados, usando el buscador de la tabla."""
    buscar_en_tabla(page, dlg, busqueda)
    asignados = {(norm(w), norm(p)): (w, op) for _, w, p, op in filas_tabla(page, dlg)}
    buscar_en_tabla(page, dlg, "")
    return asignados


def enviar(page, dlg, boton):
    """Click en Save/Update esperando la respuesta del servidor; reintenta si la red falla
    (el sitio a veces responde "Failed to fetch")."""
    for intento in range(3):
        try:
            with page.expect_response(
                lambda r: "savePrepressProductAssignment" in (r.request.post_data or ""), timeout=15_000
            ) as resp:
                dlg.get_by_role("button", name=boton).click()
            if resp.value.ok:
                return
        except Exception:
            pass
        print(f"    fallo de red al guardar (intento {intento + 1}), reintentando...")
        page.wait_for_timeout(2_000)
    raise RuntimeError(f"No se pudo guardar tras 3 intentos ({boton})")


def reasignar(page, dlg, website, prod, operador):
    """Edita la fila existente (lápiz) y cambia el operador."""
    buscar_en_tabla(page, dlg, prod.rsplit(" - ", 1)[-1])  # buscar por SKU
    for row, web, nombre, _ in filas_tabla(page, dlg):
        if norm(web) == norm(website) and norm(nombre) == norm(prod):
            row.get_by_role("button", name="Edit").click()
            break
    else:
        raise RuntimeError(f"No se encontró la fila para reasignar: {website} | {prod}")

    # Verificar que el formulario cargó la fila correcta antes de tocar nada
    c_web, c_prod = combo(dlg, "Website"), combo(dlg, "Product")
    expect(c_prod).not_to_have_value("", timeout=TIMEOUT)
    if norm(c_web.input_value()) != norm(website) or norm(c_prod.input_value()) != norm(prod):
        dlg.get_by_role("button", name="Cancel edit").click()
        raise RuntimeError(f"El formulario cargó otra fila: {c_web.input_value()} | {c_prod.input_value()}")

    seleccionar(page, combo(dlg, "Prepress operator"), operador)
    enviar(page, dlg, "Update")
    expect(dlg.get_by_role("button", name="Cancel edit")).to_be_hidden(timeout=TIMEOUT)


def elegir_website(page, dlg, website):
    """Selecciona el Website y espera a que cargue su lista de productos (Product se habilita).
    Si la carga falla por la red, vuelve a seleccionarlo."""
    c_web, c_prod = combo(dlg, "Website"), combo(dlg, "Product")
    limpiar = c_web.locator("xpath=ancestor::div[contains(@class,'MuiAutocomplete-root')]").get_by_label("Clear")
    for intento in range(3):
        # Si ya estaba seleccionado, re-elegirlo no dispara la carga: limpiarlo primero
        if c_web.input_value():
            limpiar.click(force=True)
            expect(c_web).to_have_value("", timeout=5_000)
        try:
            # Hasta que llega listSiteProducts el campo Product muestra "No options" (falso vacío)
            with page.expect_response(
                lambda r: "listSiteProducts" in (r.request.post_data or ""), timeout=TIMEOUT * 2
            ) as resp:
                seleccionar(page, c_web, website)
            # El servidor a veces responde vacío o con error: tratarlo como fallo de carga
            # (en el último intento se acepta una lista vacía, p.ej. Prestoprint no tiene productos)
            body = resp.value.json() if resp.value.ok else {}
            n = len((body.get("responseData") or {}).get("listProduct") or [])
            if body.get("type") == "ok" and (n or intento == 2):
                expect(c_prod).to_be_enabled(timeout=15_000)
                page.wait_for_timeout(500)  # que el autocomplete pinte las opciones recibidas
                return n
        except Exception:
            pass
        print(f"    Product no cargó para {website} (intento {intento + 1}), reintentando...")
        page.wait_for_timeout(2_000)
    raise RuntimeError(f"No cargó la lista de productos de {website}")


def aplicar_regla(page, dlg, regla, dry_run):
    website, filtro, operador = regla["website"], regla["filtro"], regla["operador"]
    print(f"\n=== {website} | '{filtro}' -> {operador}{' (reasignar)' if regla['reasignar'] else ''}")

    c_web, c_prod, c_op = combo(dlg, "Website"), combo(dlg, "Product"), combo(dlg, "Prepress operator")
    elegir_website(page, dlg, website)

    productos = listar_opciones(page, c_prod, filtro)
    if regla["tipos"]:
        # Por categoría de clasificacion.py (las mismas del sin_asignar.csv)
        def tipo(p):
            nombre, _, sku = p.rpartition(" - ")
            return clasificar(nombre, sku) if nombre else clasificar(p)
        productos = [p for p in productos if tipo(p) in regla["tipos"]]
    if regla["productos"]:
        # Lista exacta "Producto - SKU"; avisar si alguno no existe en el website
        pedidos = {norm(x) for x in regla["productos"]}
        faltan = pedidos - {norm(p) for p in productos}
        productos = [p for p in productos if norm(p) in pedidos]
        for x in regla["productos"]:
            if norm(x) in faltan:
                print(f"  [no existe] {x}")
    if regla["excluir"]:
        # Se compara solo el nombre (sin el " - SKU" final)
        patron = re.compile("|".join(regla["excluir"]), re.IGNORECASE)
        productos = [p for p in productos if not patron.search(p.rsplit(" - ", 1)[0])]
    asignados = leer_asignaciones(page, dlg, filtro or website)
    pendientes = [p for p in productos if (norm(website), norm(p)) not in asignados]
    # Con "reasignar": los que ya tienen OTRO operador se cambian; si no, se saltan
    a_reasignar = [
        p for p in productos
        if regla["reasignar"] and (norm(website), norm(p)) in asignados
        and norm(asignados[(norm(website), norm(p))][1]) != norm(operador)
    ]

    print(f"Productos con '{filtro}': {len(productos)} | pendientes: {len(pendientes)} | "
          f"a reasignar: {len(a_reasignar)} | se saltan: {len(productos) - len(pendientes) - len(a_reasignar)}")
    for p in productos:
        tag = "[asignar]  " if p in pendientes else "[reasignar]" if p in a_reasignar else "[saltar]   "
        print(f"  {tag} {p}")

    if dry_run:
        return

    for i, prod in enumerate(a_reasignar, 1):
        reasignar(page, dlg, website, prod, operador)
        print(f"  ({i}/{len(a_reasignar)}) reasignado: {prod}")

    if not pendientes and not a_reasignar:
        return

    for i, prod in enumerate(pendientes, 1):
        # Tras guardar el formulario se limpia entero: re-seleccionar los 3 campos
        if c_web.input_value() != website or c_prod.is_disabled():
            elegir_website(page, dlg, website)
        # Con el nombre completo la búsqueda del sitio a veces da "No options": probar
        # primero el filtro (si hay) y luego el SKU, que acota la lista a 1 o 2 opciones
        sku = prod.rsplit(" - ", 1)[-1]
        seleccionar(page, c_prod, prod, busqueda=[b for b in (filtro, sku, prod) if b])
        seleccionar(page, c_op, operador)

        enviar(page, dlg, "Save assignment")
        # El servidor ya confirmó; esperar a que el formulario se limpie (a veces tarda mucho)
        try:
            expect(c_prod).to_have_value("", timeout=TIMEOUT)
        except Exception:
            print("    el formulario no se limpió a tiempo; se continúa (el guardado ya fue confirmado)")
        print(f"  ({i}/{len(pendientes)}) guardado: {prod}")

    # Verificación final contra la tabla
    asignados = leer_asignaciones(page, dlg, filtro or website)
    fallidos = [p for p in pendientes + a_reasignar
                if norm(asignados.get((norm(website), norm(p)), ("", ""))[1]) != norm(operador)]
    if fallidos:
        print(f"ATENCIÓN: {len(fallidos)} no aparecen en la tabla tras guardar:")
        for p in fallidos:
            print("  -", p)
    else:
        print("Verificado: todos aparecen en la tabla con el operador correcto.")


def resumen(page, dlg):
    """Cantidad de productos por operador (y por website) en toda la tabla."""
    conteo = {}
    for web, op in leer_asignaciones(page, dlg, "").values():
        conteo.setdefault(op, {}).setdefault(web, 0)
        conteo[op][web] += 1

    print("\n=== Resumen por Prepress operator")
    for op, webs in sorted(conteo.items(), key=lambda x: -sum(x[1].values())):
        detalle = ", ".join(f"{w}: {n}" for w, n in sorted(webs.items()))
        print(f"  {op}: {sum(webs.values())}  ({detalle})")


def guardar_diagnostico(page):
    try:
        page.screenshot(path="error.png", full_page=True)
        with open("error.html", "w", encoding="utf-8") as f:
            f.write(page.content())
        print("Diagnóstico guardado en error.png y error.html")
    except Exception:
        print("No se pudo guardar diagnóstico (el navegador ya estaba cerrado)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reglas", default="reglas.json")
    ap.add_argument("--dry-run", action="store_true", help="solo listar, no guardar")
    ap.add_argument("--headless", action="store_true", help="no mostrar el navegador")
    ap.add_argument("--solo", help="números de regla a correr (1 = la primera del json), p.ej. 8 o 7,8")
    args = ap.parse_args()

    with open(args.reglas, encoding="utf-8") as f:
        reglas = json.load(f)["reglas"]
    if args.solo:
        reglas = [reglas[int(n) - 1] for n in args.solo.split(",")]

    # "website" y "filtro" pueden ser texto o lista: expandir a una regla por combinación
    como_lista = lambda v: v if isinstance(v, list) else [v]
    reglas = [
        {"website": w.strip(), "filtro": f.strip(), "operador": r["operador"].strip(),
         "reasignar": r.get("reasignar", False), "excluir": r.get("excluir", []),
         "tipos": como_lista(r.get("tipo", [])), "productos": r.get("productos", [])}
        for r in reglas
        for w in como_lista(r["website"])
        for f in como_lista(r.get("filtro", [""]))
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless, slow_mo=SLOW_MO)
        page = browser.new_page()
        try:
            login(page)
            ir_a_prepress(page)
            dlg = dialogo_prepress(page)
            sitios = None
            for regla in reglas:
                if norm(regla["website"]) != "todos":
                    aplicar_regla(page, dlg, regla, args.dry_run)
                    continue
                # "todos" = cada opción del campo Website
                if sitios is None:
                    sitios = listar_opciones(page, combo(dlg, "Website"), "")
                    if not sitios:
                        raise RuntimeError("No se pudo leer la lista de websites")
                    print(f"\nWebsites disponibles: {', '.join(sitios)}")
                for w in sitios:
                    aplicar_regla(page, dlg, {**regla, "website": w}, args.dry_run)
            resumen(page, dlg)
            print("\nListo.")
        except Exception as e:
            print("ERROR:", e)
            guardar_diagnostico(page)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
