"""
Servidor local del frontend.

  py server.py            # abre http://localhost:8765
  py server.py --puerto 9000

Endpoints:
  GET  /                        -> web/index.html
  GET  /api/products            -> productos ya scrapeados (data/*.json)
  GET  /api/product/<slug>      -> datos de un producto
  POST /api/scrape {"url": ...} -> scrapea el link y devuelve los datos
"""
import argparse
import json
import threading
import traceback
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import scraper

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
PORT = 8765
_lock = threading.Lock()  # un scrape a la vez


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB_DIR), **kw)

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            super().log_message(fmt, *args)

    def end_headers(self):
        # Permite usar el frontend abierto desde otro servidor (p. ej. Live Server)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/products":
            items = []
            for f in sorted(scraper.DATA_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
                try:
                    d = json.loads(f.read_text(encoding="utf8"))
                    items.append({"slug": f.stem, "name": d["product"]["name"], "url": d["url"],
                                  "scrapedAt": d["scrapedAt"]})
                except Exception:
                    pass
            return self._json(items)
        if self.path.startswith("/api/product/"):
            slug = self.path.rsplit("/", 1)[-1]
            f = scraper.DATA_DIR / f"{slug}.json"
            if not f.exists() or f.parent != scraper.DATA_DIR:
                return self._json({"error": "No encontrado"}, 404)
            return self._json(json.loads(f.read_text(encoding="utf8")))
        return super().do_GET()

    def do_POST(self):
        if self.path != "/api/scrape":
            return self._json({"error": "No encontrado"}, 404)
        try:
            length = int(self.headers.get("Content-Length", 0))
            url = (json.loads(self.rfile.read(length) or b"{}").get("url") or "").strip()
            if not url:
                return self._json({"error": "Falta el link del producto"}, 400)
            if not _lock.acquire(blocking=False):
                return self._json({"error": "Ya hay un scrape en curso, espera a que termine"}, 409)
            try:
                data = scraper.scrape(url)
            finally:
                _lock.release()
            return self._json(data)
        except Exception as e:
            traceback.print_exc()
            return self._json({"error": str(e)}, 500)


def run(port=PORT, open_slug=None, open_browser=True):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://localhost:{port}/" + (f"#{open_slug}" if open_slug else "")
    print(f"Frontend en {url}  (Ctrl+C para salir)", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--puerto", type=int, default=PORT)
    ap.add_argument("--no-abrir", action="store_true")
    a = ap.parse_args()
    run(a.puerto, open_browser=not a.no_abrir)
