"""A small HTTP + JSON layer over the service, on the standard library only.

Why no framework: the entire dependency list of this project is "Python 3.10+".
A reviewer clones the repo and runs one command - no virtualenv, no pip, no
lockfile, no network. For a three-hour exercise that tradeoff is worth more than
the conveniences FastAPI would have given me, and the router below is 40 lines.
If this grew past a handful of endpoints I would switch to FastAPI for the
validation and the generated OpenAPI docs.
"""

from __future__ import annotations

import json
import re
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .models import ValidationError
from .service import Conflict, NotFound

WEB_ROOT = Path(__file__).with_name("web")
MAX_BODY = 64 * 1024
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}

ROUTES = []


def route(method, pattern):
    compiled = re.compile(f"^{pattern}$")

    def wrap(func):
        ROUTES.append((method, compiled, func))
        return func

    return wrap


class Handler(BaseHTTPRequestHandler):
    server_version = "LostFound/1.0"
    service = None  # injected by build_server

    # -- plumbing ----------------------------------------------------------
    def log_message(self, fmt, *args):
        print(f"  {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")

    def _send(self, status, payload=None, body=b"", content_type="application/json"):
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if content_type.startswith("application/json"):
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, status, code, message, fields=None):
        payload = {"error": {"code": code, "message": message}}
        if fields:
            payload["error"]["fields"] = fields
        self._send(status, payload)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValidationError({"_": "That request is too large."})
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValidationError({"_": "Body must be valid UTF-8 JSON."})
        if not isinstance(data, dict):
            raise ValidationError({"_": "Body must be a JSON object."})
        return data

    # -- dispatch ----------------------------------------------------------
    def _dispatch(self, method):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        self.query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        allowed = set()
        for verb, pattern, func in ROUTES:
            match = pattern.match(path)
            if not match:
                continue
            if verb != method:
                allowed.add(verb)
                continue
            try:
                func(self, *match.groups())
            except ValidationError as exc:
                self._error(422, "validation_error", exc.message, exc.fields)
            except NotFound as exc:
                self._error(404, "not_found", str(exc))
            except Conflict as exc:
                self._error(409, "conflict", str(exc))
            except BrokenPipeError:
                pass
            except Exception:
                traceback.print_exc()
                self._error(500, "internal_error", "Something broke on our side.")
            return
        if allowed:
            self._error(405, "method_not_allowed", f"Try {', '.join(sorted(allowed))}.")
        else:
            self._error(404, "not_found", f"No route for {path}.")

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_DELETE(self):
        self._dispatch("DELETE")


# -- static ----------------------------------------------------------------
@route("GET", "/")
def index(handler):
    _serve_file(handler, "index.html")


@route("GET", "/static/([A-Za-z0-9_.-]+)")
def static(handler, name):
    _serve_file(handler, name)


def _serve_file(handler, name):
    # Whitelisted characters in the route plus a resolved-path check: two locks
    # on the same door, because path traversal is not a bug worth having twice.
    target = (WEB_ROOT / name).resolve()
    if not target.is_file() or WEB_ROOT.resolve() not in target.parents:
        handler._error(404, "not_found", "No such file.")
        return
    handler._send(200, body=target.read_bytes(),
                  content_type=CONTENT_TYPES.get(target.suffix, "application/octet-stream"))


# -- meta ------------------------------------------------------------------
@route("GET", "/api/health")
def health(handler):
    handler._send(200, {"status": "ok"})


@route("GET", "/api/meta")
def meta(handler):
    handler._send(200, handler.service.meta())


@route("GET", "/api/stats")
def stats(handler):
    handler._send(200, handler.service.stats())


# -- reports ---------------------------------------------------------------
@route("GET", "/api/reports")
def list_reports(handler):
    query = handler.query
    handler._send(200, handler.service.list_reports(
        kind=query.get("kind"),
        status=query.get("status", "open"),
        query=query.get("q"),
        limit=_int(query.get("limit"), 200),
        offset=_int(query.get("offset"), 0),
    ))


@route("POST", "/api/reports")
def create_report(handler):
    handler._send(201, handler.service.create_report(handler._body()))


@route("GET", "/api/reports/([A-Za-z0-9-]+)")
def get_report(handler, report_id):
    handler._send(200, handler.service.get_report(report_id))


@route("DELETE", "/api/reports/([A-Za-z0-9-]+)")
def withdraw_report(handler, report_id):
    handler._send(200, handler.service.withdraw(report_id))


@route("GET", "/api/reports/([A-Za-z0-9-]+)/matches")
def report_matches(handler, report_id):
    debug = handler.query.get("debug") in ("1", "true", "yes")
    handler._send(200, handler.service.matches_for(report_id, include_rejections=debug))


# -- matches ---------------------------------------------------------------
@route("GET", "/api/matches")
def matches(handler):
    band = handler.query.get("band")
    if band and band not in ("strong", "possible", "weak"):
        raise ValidationError({"band": "Must be strong, possible or weak."})
    handler._send(200, handler.service.board(band=band, limit=_int(handler.query.get("limit"), 60)))


@route("POST", "/api/matches/([A-Za-z0-9-]+)/([A-Za-z0-9-]+)/decision")
def decide(handler, lost_id, found_id):
    body = handler._body()
    handler._send(200, handler.service.decide(
        lost_id, found_id, body.get("decision", ""), str(body.get("note") or "")[:280]
    ))


@route("GET", "/api/reunions")
def reunions(handler):
    handler._send(200, handler.service.resolved())


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def build_server(service, host="127.0.0.1", port=8000):
    class ReusableServer(ThreadingHTTPServer):
        allow_reuse_address = True
    handler = type("BoundHandler", (Handler,), {"service": service})
    return ReusableServer((host, port), handler)
