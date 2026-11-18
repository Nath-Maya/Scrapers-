# Scraper de precios Sinalite

Le das el link de un producto de sinalite.com y te muestra una tabla con los precios de todas sus variaciones.

## Uso

**Frontend** (recomendado):

```
py server.py
```

Se abre http://localhost:8765. Pega el link del producto y pulsa **Obtener precios**.

- **Filas / Columnas**: qué opción va en cada eje (por defecto Tamaño × Cantidad).
- Las demás opciones (Stock, Coating, Turnaround…) se eligen en los filtros.
- **Precio total / por unidad**, **CSV tabla** y **CSV todas las combinaciones**.
- Los productos que ya scrapeaste aparecen arriba como accesos rápidos (se guardan en `data/`).

**Terminal**:

```
py scraper.py "https://sinalite.com/en_ca/print-products/postcards/14pt-uv-high-gloss.html"
py scraper.py "<link>" --abrir     # además abre el frontend con ese producto
```

## Cómo funciona

1. Inicia sesión con las credenciales de `.env` (la sesión se guarda en `storage_state.json`).
2. Abre el producto con Playwright y lee los datos de precio que carga la propia web.
3. Calcula los precios con la misma fórmula que usa la calculadora de Sinalite.
4. Para saber qué combinaciones existen, ejecuta en la página la propia lógica del sitio
   (`product.applyExclusions`), incluidas sus reglas especiales (p. ej. "UV" no existe con
   impresión 4/0, o "Next Business Day" en banners no permite Grommets/Hemming).

Los resultados se comprobaron contra la calculadora de la web: 450 combinaciones aleatorias
(postcards, business cards y banners) coinciden en precio y disponibilidad.

## Limitaciones

- No cubre los tamaños personalizados ("Need a custom size?"), que se cotizan en el servidor.
- Los productos con precio calculado en el servidor (`plusplus`) muestran un aviso.
- Si un producto tiene muchísimas opciones, el primer scrape puede tardar algunos minutos.
