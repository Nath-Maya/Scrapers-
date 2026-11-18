"""
Clasificación de productos por tipo, según palabras del nombre.
El orden importa: gana la primera categoría que coincide (p.ej. un Tri-Panel 9x12 va a Tri-Panel).
"""
import re

CATEGORIAS = [
    # Ropa y otros productos no imprenta tradicional
    ("Ropa / Apparel", r"gildan|bella\s*\+\s*canvas|t-shirt|hoodie|sweatshirt|crewneck|champion|softstyle|\btee\b|polo"),
    ("Bottle Neckers", r"bottle\s*necker"),
    ("Libros / Encuadernación", r"\bbooks?\b|perfect bound|coil|wire\s*o|binding|saddle"),
    # Folders, de lo más específico a lo más general
    ("Pocket Folder - Tri-Panel", r"tri\s*-?\s*panel"),
    ("Pocket Folder - Gusset / High Capacity", r"gusset|high capacity|spine|expand"),
    ("Pocket Folder - Legal", r"legal|9\.?5?\s*x\s*14\.5"),
    ("Certificate Folders", r"certificate"),
    ("Tax Folders", r"tax folder"),
    ("Pocket Folder - Pequeños (4x9, 6x9, Small)", r"\b4(\.\d+)?\s*x\s*9|\b6\s*x\s*9|\b9\s*x\s*6|\b5\.\d+\s*x\s*8|small"),
    ("Pocket Folder 9x12", r"9\s*x\s*12|12\s*x\s*9"),
    ("Presentation Folders", r"presentation"),
    ("Pocket Folder - Curved / Semicurved", r"curved"),
    ("Pocket Folder - Reinforced", r"reinforced"),
    ("Pocket Folder - Otros tamaños / estilos", r"pocket folder"),
    ("Folders - Otros", r"folder"),
    # Impresos
    ("Business Cards", r"business card"),
    ("Postcards", r"post\s*card"),
    ("Flyers", r"flyer"),
    ("Brochures", r"brochure"),
    ("Door Hangers", r"door\s*hanger"),
    ("Booklets", r"booklet"),
    ("Stickers / Labels", r"sticker|label|decal"),
    ("Envelopes", r"envelope"),
    ("Letterhead", r"letterhead"),
    ("Rack Cards", r"rack card"),
    ("Greeting Cards", r"greeting"),
    ("Scorecards", r"scorecard"),
    ("Notepads", r"notepad"),
    ("Banners / Signs", r"banner|\bsigns?\b|yard"),
    ("Boxes / Packaging", r"\bbox|packag|sleeve"),
    ("Calendars", r"calendar"),
    ("Bookmarks", r"bookmark"),
    ("Tickets", r"ticket"),
    ("Invitations", r"invitation"),
    ("Posters", r"poster"),
    ("Cards - Otros", r"card"),
    ("Covers / Report Covers / Inserts", r"covers?\b|dust jacket|duotang|prong"),
    ("Forms (NCR / Laser)", r"\bforms?\b|carbonless|ncr"),
    ("Promocionales / POP", r"dangler|keg collar|clapper|hanger|placemat|flag|tote|tags?\b|folded cross|photo|document holder|auto-lock"),
    ("Cargos / Servicios", r"charge|fee\b|service|cut only|die cut|laminating|sheets|dtf"),
    ("Pruebas / Cotizaciones / Custom", r"proof|test|prueba|mock|quote|custom|sample"),
]
# Si el nombre no dice qué es, el prefijo del SKU lo aclara: se agrega esa palabra al nombre
POR_SKU = [(r"^(N-)?PF-", "pocket folder"), (r"^P[ST]-", "poster")]
_SKU = [(re.compile(rx, re.IGNORECASE), palabra) for rx, palabra in POR_SKU]
_COMPILADAS = [(nombre, re.compile(rx, re.IGNORECASE)) for nombre, rx in CATEGORIAS]
ORDEN = {nombre: i for i, (nombre, _) in enumerate(CATEGORIAS)}


def clasificar(producto, sku=""):
    # "Standard 100lb. Lynx Smooth Cover - PF-S045" es un pocket folder aunque el nombre no lo diga
    for rx, palabra in _SKU:
        if rx.search(sku) and palabra not in producto.lower():
            producto = f"{producto} {palabra}"
    for nombre, rx in _COMPILADAS:
        if rx.search(producto):
            return nombre
    return "Otros"
