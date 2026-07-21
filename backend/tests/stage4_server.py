"""A tiny loopback HTTP server for Stage 4 tests (stdlib only).

Serves deterministic fixtures: PDFs with known bytes, a flaky endpoint that
fails twice before succeeding (proves retry logic), a redirect, a
Content-Disposition filename, an RBI-style listing page, and a login page.
Runs on 127.0.0.1 with an OS-assigned port; no outside network involved.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PDF_A = b"%PDF-1.4 fixture-A " + b"a" * 200000  # ~200 KB → several chunks
PDF_B = b"%PDF-1.4 fixture-B " + b"b" * 1000
DOCX_C = b"PK docx-fixture " + b"c" * 500


class _Handler(BaseHTTPRequestHandler):
    server_version = "Stage4Fixture/1.0"

    def do_GET(self) -> None:  # noqa: N802 — http.server API
        route = getattr(self.server, "routes", {}).get(self.path.split("?")[0])
        if route is None:
            self._send(404, b"not here", "text/plain")
            return
        route(self)

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        extra: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # keep test output clean
        pass


class FixtureServer:
    def __init__(self) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.routes = {}  # type: ignore[attr-defined]
        self.flaky_hits = 0
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._install_routes()

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> "FixtureServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def base(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    def url(self, path: str) -> str:
        return self.base + path

    # -- routes ---------------------------------------------------------------

    def _install_routes(self) -> None:
        routes = self.httpd.routes  # type: ignore[attr-defined]

        routes["/a.pdf"] = lambda h: h._send(200, PDF_A, "application/pdf")
        routes["/b.pdf"] = lambda h: h._send(200, PDF_B, "application/pdf")
        # Same bytes as /a.pdf under another URL → duplicate by SHA-256.
        routes["/dup.pdf"] = lambda h: h._send(200, PDF_A, "application/pdf")
        routes["/annexure.docx"] = lambda h: h._send(
            200, DOCX_C,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        routes["/gone.pdf"] = lambda h: h._send(404, b"gone", "text/plain")

        def named(h: _Handler) -> None:
            h._send(200, PDF_B, "application/pdf", {
                "Content-Disposition": 'attachment; filename="Circular No. 12 of 2026.pdf"'
            })
        routes["/named"] = named

        def redirect(h: _Handler) -> None:
            h.send_response(302)
            h.send_header("Location", "/a.pdf")
            h.send_header("Content-Length", "0")
            h.end_headers()
        routes["/redirect"] = redirect

        def flaky(h: _Handler) -> None:
            self.flaky_hits += 1
            if self.flaky_hits <= 2:
                h._send(500, b"try again", "text/plain")
            else:
                h._send(200, PDF_B, "application/pdf")
        routes["/flaky.pdf"] = flaky

        listing = f"""
        <html><head><title>Reserve Bank of India - Test Listing</title></head>
        <body>
          <a href="/a.pdf">Master Direction – KYC (Updated as on July 15, 2026)</a>
          <a href="/b.pdf">Notification dated 05/03/2026 on FEMA reporting</a>
          <a href="/dup.pdf">The same Master Direction, linked again elsewhere</a>
          <a href="/about.html">About this site</a>
          <p>Server-rendered listing used by the Stage 4 service tests. It has
          enough visible text that the JS-shell heuristic stays quiet, exactly
          like a real RBI notifications page would.</p>
        </body></html>
        """.encode()
        routes["/listing.html"] = lambda h: h._send(200, listing, "text/html")

        empty = (b"<html><head><title>Nothing here</title></head><body>"
                 b"<p>An article page with plenty of words but no files at all. "
                 b"It goes on about regulatory context in general terms so the "
                 b"text-length heuristics see a normal page.</p>"
                 b"<a href='/about.html'>About</a></body></html>")
        routes["/empty.html"] = lambda h: h._send(200, empty, "text/html")

        login = (b"<html><head><title>Portal Sign In</title></head><body>"
                 b"<h1>Sign In</h1><form><input type='text' name='u'/>"
                 b"<input type='password' name='p'/><button>Login</button></form>"
                 b"<p>Registered users can sign in to continue.</p></body></html>")
        routes["/login"] = lambda h: h._send(200, login, "text/html")

        routes["/direct-file"] = lambda h: h._send(200, PDF_B, "application/pdf")
