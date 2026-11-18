"""
Barrido (solo lectura): productos SIN prepress operator asignado, por website.

Uso:
  py barrido.py              # genera sin_asignar.csv y muestra el resumen
  py barrido.py --headless
"""
import argparse
import collections
import csv
from playwright.sync_api import sync_playwright

import Scraper_pressOperator.scraper as s
from Scraper_pressOperator.clasificacion import ORDEN, clasificar

# Tipos que no se listan en el CSV (no requieren prepress operator)
EXCLUIR_TIPOS = {"Ropa / Apparel"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", action="store_true", help="no mostrar el navegador")
    ap.add_argument("--salida", default="sin_asignar.csv")
    args = ap.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless, slow_mo=s.SLOW_MO)
        page = browser.new_page()
        try:
            s.login(page)
            s.ir_a_prepress(page)
            dlg = s.dialogo_prepress(page)

            asignados = s.leer_asignaciones(page, dlg, "")
            sitios = s.listar_opciones(page, s.combo(dlg, "Website"), "")

            filas, resumen = [], []
            for web in sitios:
                esperados = s.elegir_website(page, dlg, web)
                productos = s.listar_opciones(page, s.combo(dlg, "Product"), "")
                if len(productos) != esperados:
                    raise RuntimeError(f"{web}: el servidor envió {esperados} productos pero la lista mostró {len(productos)}")
                libres = [pr for pr in productos if (s.norm(web), s.norm(pr)) not in asignados]
                resumen.append((web, len(productos), len(libres)))
                for pr in libres:
                    nombre, _, sku = pr.rpartition(" - ")
                    nombre, sku = (nombre, sku) if nombre else (pr, "")
                    filas.append({"tipo": clasificar(nombre, sku), "website": web, "producto": nombre, "sku": sku})
                print(f"{web}: {len(productos)} productos | {len(libres)} sin asignar")

            if not any(r[1] for r in resumen):
                raise RuntimeError("Se leyeron 0 productos en total; no se sobrescribe el CSV")
            excluidos = sum(1 for r in filas if r["tipo"] in EXCLUIR_TIPOS)
            filas = [r for r in filas if r["tipo"] not in EXCLUIR_TIPOS]
            # Agrupado por tipo (en el orden de clasificacion.py), luego website y producto
            filas.sort(key=lambda r: (ORDEN.get(r["tipo"], len(ORDEN)), r["website"], r["producto"].lower()))
            with open(args.salida, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=["tipo", "website", "producto", "sku"])
                w.writeheader()
                w.writerows(filas)

            total = sum(r[1] for r in resumen)
            libres = sum(r[2] for r in resumen)
            print(f"\nTOTAL: {total} productos | {total - libres} asignados | {libres} sin asignar")
            por_tipo = collections.Counter(r["tipo"] for r in filas)
            print("\nSin asignar por tipo:")
            for t, n in sorted(por_tipo.items(), key=lambda x: ORDEN.get(x[0], len(ORDEN))):
                print(f"  {n:5}  {t}")
            print(f"(no incluidos en el CSV: {excluidos} de {', '.join(sorted(EXCLUIR_TIPOS))})")
            print(f"Detalle en {args.salida}")
        except Exception as e:
            print("ERROR:", e)
            s.guardar_diagnostico(page)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
