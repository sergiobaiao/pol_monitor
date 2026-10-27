import os
import sys
import json
import ssl
import urllib.request
import urllib.error
from http.server import HTTPServer, SimpleHTTPRequestHandler
import webbrowser

PORT = 8000
TARGET_API = "https://office-api.pol.adv.br"

# Token opcional para o proxy (ex.: copiado do navegador). Nunca hardcoded no código.
DEFAULT_TOKEN = os.environ.get("POL_TOKEN", "")
DEFAULT_OFFICE_ID = "25324"
DEFAULT_USER_ID = "46e0695a-490e-49f4-b660-d19af55d956d"

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

class SwaggerProxyHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, PATCH, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Expose-Headers", "*")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_PROXY(self, method):
        target_url = TARGET_API + self.path
        
        # Read request body if present
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else None

        # Build headers
        forward_headers = {}
        for k, v in self.headers.items():
            if k.lower() not in ["host", "origin", "referer", "content-length"]:
                forward_headers[k] = v

        if DEFAULT_TOKEN and "Authorization" not in forward_headers and "authorization" not in forward_headers:
            forward_headers["Authorization"] = f"Bearer {DEFAULT_TOKEN}"
        if "x-officeid" not in forward_headers and "X-Officeid" not in forward_headers:
            forward_headers["x-officeid"] = DEFAULT_OFFICE_ID
        if "x-selecteduserid" not in forward_headers and "X-Selecteduserid" not in forward_headers:
            forward_headers["x-selecteduserid"] = DEFAULT_USER_ID

        req = urllib.request.Request(target_url, data=body, headers=forward_headers, method=method)

        try:
            with urllib.request.urlopen(req, context=ctx) as response:
                resp_body = response.read()
                self.send_response(response.status)
                for header_name, header_value in response.getheaders():
                    if header_name.lower() not in ["transfer-encoding", "content-encoding", "access-control-allow-origin"]:
                        self.send_header(header_name, header_value)
                self.end_headers()
                self.wfile.write(resp_body)
        except urllib.error.HTTPError as e:
            err_body = e.read()
            self.send_response(e.code)
            for header_name, header_value in e.headers.items():
                if header_name.lower() not in ["transfer-encoding", "content-encoding", "access-control-allow-origin"]:
                    self.send_header(header_name, header_value)
            self.end_headers()
            self.wfile.write(err_body)
        except Exception as e:
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Bad Gateway / Proxy Error", "detail": str(e)}).encode("utf-8"))

    def do_GET(self):
        # If it's an API route or management route, proxy it to the POL backend
        if self.path.startswith("/services/") or self.path.startswith("/api/") or self.path.startswith("/management/"):
            self.do_PROXY("GET")
        else:
            super().do_GET()

    def do_POST(self):
        if self.path.startswith("/services/") or self.path.startswith("/api/") or self.path.startswith("/management/"):
            self.do_PROXY("POST")
        else:
            self.send_response(405)
            self.end_headers()

    def do_PUT(self):
        if self.path.startswith("/services/") or self.path.startswith("/api/") or self.path.startswith("/management/"):
            self.do_PROXY("PUT")
        else:
            self.send_response(405)
            self.end_headers()

    def do_DELETE(self):
        if self.path.startswith("/services/") or self.path.startswith("/api/") or self.path.startswith("/management/"):
            self.do_PROXY("DELETE")
        else:
            self.send_response(405)
            self.end_headers()

    def do_PATCH(self):
        if self.path.startswith("/services/") or self.path.startswith("/api/") or self.path.startswith("/management/"):
            self.do_PROXY("PATCH")
        else:
            self.send_response(405)
            self.end_headers()


def run():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    server_address = ("127.0.0.1", PORT)
    httpd = HTTPServer(server_address, SwaggerProxyHandler)
    url = f"http://127.0.0.1:{PORT}/"
    print("=" * 65)
    print(f"  POL ADV - Swagger UI & Reverse Proxy iniciado com sucesso!")
    print(f"  Endereço Local: {url}")
    print(f"  Recursos:")
    print(f"   - Lawoffice Core API (476 rotas de processos, intimações, etc.)")
    print(f"   - PolGateway API (Autenticação e Sessão)")
    print(f"   - PolGateway Management (Actuator/Métricas)")
    print(f"   - Proxy CORS Automático integrado para https://office-api.pol.adv.br")
    print("=" * 65)
    
    # Open default browser
    try:
        webbrowser.open(url)
    except Exception:
        pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor encerrado pelo usuário.")
        httpd.server_close()


if __name__ == "__main__":
    run()
