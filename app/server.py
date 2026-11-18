"""Tiny HTTP server: static page + JSON API."""
from __future__ import annotations

import base64
import hmac
import json
import logging
import math
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import ntpq
from .chrony import ChronyError, run_chronyc
from .monitor import METRICS

log = logging.getLogger("http")
STATIC = os.path.realpath(os.path.join(os.path.dirname(__file__), "static"))

CSP = ("default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline' https://unpkg.com; "
       "script-src 'self' https://unpkg.com; connect-src 'self'; frame-ancestors 'none'")


def sanitize(o):
    if isinstance(o, float):
        return None if math.isnan(o) or math.isinf(o) else o
    if isinstance(o, dict):
        return {k: sanitize(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [sanitize(v) for v in o]
    return o


def make_handler(monitor, cfg):
    expected = None
    if cfg.basic_auth_user:
        expected = "Basic " + base64.b64encode(f"{cfg.basic_auth_user}:{cfg.basic_auth_pass}".encode()).decode()

    class Handler(BaseHTTPRequestHandler):
        server_version = "ntp-dashboard"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            if cfg.debug:
                log.debug("%s " + fmt, self.address_string(), *args)

        def _send(self, code, body: bytes, ctype, extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            # Tile servers such as OpenStreetMap refuse requests that carry no Referer.
            self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
            self.send_header("Content-Security-Policy", CSP)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj, code=200):
            body = json.dumps(sanitize(obj), separators=(",", ":")).encode()
            self._send(code, body, "application/json", {"Cache-Control": "no-store"})

        def _authorized(self) -> bool:
            if not expected:
                return True
            got = self.headers.get("Authorization", "")
            return hmac.compare_digest(got.encode(), expected.encode())

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            url = urlparse(self.path)
            path = url.path
            if path == "/healthz":
                return self._send(200, b"ok\n", "text/plain")
            if not self._authorized():
                return self._send(401, b"authentication required\n", "text/plain",
                                  {"WWW-Authenticate": 'Basic realm="NTP dashboard"'})
            try:
                if path == "/api/status":
                    return self._json(monitor.status())
                if path == "/api/time":
                    return self._json(monitor.time_info())
                if path == "/api/config":
                    return self._json({"server_name": cfg.server_name, "tile_url": cfg.map_tile_url,
                                       "attribution": cfg.map_attribution, "hide_position": cfg.hide_position,
                                       "history_days": cfg.history_days, "metrics": list(METRICS),
                                       "ntp_host": cfg.ntp_host, "server_host": cfg.server_host,
                                       "references": list(cfg.reference_servers)})
                if path == "/api/history":
                    q = parse_qs(url.query)
                    metrics = [m for m in (q.get("metrics", [""])[0]).split(",") if m]
                    try:
                        seconds = int(q.get("range", ["3600"])[0])
                    except ValueError:
                        seconds = 3600
                    return self._json(monitor.history(metrics, seconds))
                if path == "/api/raw":
                    kind = monitor._kind or ("ntpd" if cfg.server_type == "ntpd" else "chrony")
                    if kind == "ntpd" or cfg.server_type == "probe":
                        text = ntpq.raw_dump(cfg.server_host, cfg.ntp_port)
                    else:
                        out = {}
                        for cmd in ("tracking", "sources", "sourcestats", "serverstats"):
                            try:
                                out[cmd] = run_chronyc(cfg, cmd)
                            except ChronyError as e:
                                out[cmd] = f"ERROR: {e}"
                        text = "\n".join(f"$ chronyc -c {k}\n{v}" for k, v in out.items())
                    return self._send(200, text.encode(), "text/plain; charset=utf-8", {"Cache-Control": "no-store"})
                return self._static(path)
            except (BrokenPipeError, ConnectionResetError):
                raise
            except Exception:
                log.exception("request failed: %s", self.path)
                self._json({"error": "internal error"}, 500)

        def _static(self, path):
            if path in ("/", "/index.html"):
                rel = "index.html"
            elif path.startswith("/static/"):
                rel = path[len("/static/"):]
            else:
                return self._send(404, b"not found\n", "text/plain")
            full = os.path.realpath(os.path.join(STATIC, rel))
            if not full.startswith(STATIC + os.sep) or not os.path.isfile(full):
                return self._send(404, b"not found\n", "text/plain")
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            with open(full, "rb") as f:
                self._send(200, f.read(), ctype, {"Cache-Control": "no-cache"})

    return Handler


def make_server(monitor, cfg) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((cfg.listen_host, cfg.listen_port), make_handler(monitor, cfg))
    srv.daemon_threads = True
    return srv
