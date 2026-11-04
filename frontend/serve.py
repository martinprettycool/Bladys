"""Development-only static server with a same-origin customer API proxy."""
import argparse
import re
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

class Handler(SimpleHTTPRequestHandler):
    backend = "http://127.0.0.1:8000"

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/products" or re.fullmatch(r"/orders/[1-9][0-9]*", path):
            return self.proxy(path)
        return super().do_GET()

    def do_POST(self):
        if self.path != "/orders":
            return self.send_error(404)
        return self.proxy("/orders")

    def proxy(self, path):
        headers = {name: self.headers[name] for name in
                   ("Content-Type", "Accept", "Idempotency-Key", "X-Order-Access-Token")
                   if name in self.headers}
        data = None
        if self.command == "POST":
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self.send_error(400)
            if not 0 < length <= 1024 * 1024:
                return self.send_error(413)
            data = self.rfile.read(length)
        request = Request(self.backend + path, data=data, headers=headers, method=self.command)
        try:
            response = urlopen(request, timeout=20)
        except HTTPError as error:
            response = error
        except (URLError, TimeoutError):
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"detail":"Restaurant API unavailable"}')
            return
        with response:
            body = response.read()
            self.send_response(response.code)
            self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5173)
    parser.add_argument("--backend", default=Handler.backend)
    args = parser.parse_args()
    Handler.backend = args.backend.rstrip("/")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, directory=str(Path(__file__).parent)))
    print(f"Frontend: http://127.0.0.1:{args.port}; API: {Handler.backend}", flush=True)
    server.serve_forever()
