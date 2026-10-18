"""Loopback dashboard with narrowly scoped review actions (stdlib only)."""
from __future__ import annotations

import json
import secrets
import sys
import webbrowser
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import unquote, urlsplit

from . import rundir
from .dashboard_data import RunStore

ASSETS = {"/VT323-Regular.ttf": ("VT323-Regular.ttf", "font/ttf"),"/": ("index.html", "text/html; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8"),
          "/wopr.js": ("wopr.js", "text/javascript; charset=utf-8"),
          "/presentation.js": ("presentation.js", "text/javascript; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8")}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; "
       "connect-src 'self'; img-src 'self'; font-src 'self'; "
       "base-uri 'none'; frame-ancestors 'none'; form-action 'none'")


class DashboardHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, store, csrf_token, **kwargs):
        self.store = store
        self.csrf_token = csrf_token
        super().__init__(*args, **kwargs)

    def log_message(self, *args):
        # Polling must not fill the terminal with access logs or run names.
        pass

    def _send(self, status, body, content_type="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, status, value):
        self._send(status, json.dumps(value, allow_nan=False).encode("utf-8"))

    def _local_request(self):
        host = self.headers.get("Host", "")
        port = self.server.server_port
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        return (host in allowed and
                self.headers.get("Sec-Fetch-Site", "none") in ("none", "same-origin") and
                self.headers.get("Origin", f"http://{host}") == f"http://{host}")

    def do_GET(self):
        if not self._local_request():
            self._json(403, {"error": "Only same-origin loopback requests are allowed."})
            return
        path = urlsplit(self.path).path
        if path in ASSETS:
            name, mime = ASSETS[path]
            body = files("dirtywork.dashboard_assets").joinpath(name).read_bytes()
            self._send(200, body, mime)
            return
        if path == "/api/runs":
            try:
                self._json(200, {**self.store.snapshot(), "csrf_token": self.csrf_token})
            except OSError:
                self._json(503, {"error": "Run records are temporarily unavailable; retry shortly."})
            return
        if path.startswith("/api/runs/"):
            is_changes = path.endswith("/changes")
            slug = unquote(path[len("/api/runs/"):-len("/changes")] if is_changes else path[len("/api/runs/"):])
            try:
                self._json(200, self.store.changes(slug) if is_changes else self.store.detail(slug))
            except ValueError:
                self._json(400, {"error": "Invalid run slug."})
            except FileNotFoundError:
                self._json(404, {"error": "Run no longer exists."})
            except OSError:
                self._json(503, {"error": "Run records are temporarily unavailable; retry shortly."})
            return
        self._json(404, {"error": "Not found."})

    def do_POST(self):
        path = urlsplit(self.path).path
        is_task_type = path.endswith("/task-type")
        if not path.startswith("/api/runs/") or not (path.endswith("/review") or is_task_type):
            self._json(405, {"error": "Only review actions are supported."})
            return
        if (not self._local_request() or not self.headers.get("Origin") or
                not secrets.compare_digest(self.headers.get("X-Dirtywork-CSRF", "").encode("utf-8"), self.csrf_token.encode("utf-8"))):
            self._json(403, {"error": "Reload the dashboard before saving task types." if is_task_type else
                                    "Reload the dashboard before saving review actions."})
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self._json(415, {"error": "Task types require JSON." if is_task_type else "Review actions require JSON."})
            return
        suffix = "/task-type" if is_task_type else "/review"
        slug = unquote(path[len("/api/runs/"):-len(suffix)])
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if self.headers.get("Transfer-Encoding") or not 0 < length <= 16384:
                raise ValueError("Invalid task type request size." if is_task_type else "Invalid review request size.")
            self.connection.settimeout(5)
            payload = json.loads(self.rfile.read(length))
            if is_task_type:
                if not isinstance(payload, dict) or "task_type" not in payload:
                    raise ValueError("Choose a task type or reset the suggestion.")
                result = self.store.set_task_type(slug, payload["task_type"])
            else:
                if not isinstance(payload, dict) or payload.get("action") not in ("start", "accept", "reject"):
                    raise ValueError("Choose Start Review, Accept, or Reject.")
                note = payload.get("note", "")
                if not isinstance(note, str) or len(note) > 2000:
                    raise ValueError("Review notes must be at most 2,000 characters.")
                result = self.store.review_action(slug, payload["action"], note)
            self._json(200, {"run": result})
        except FileNotFoundError:
            self._json(404, {"error": "Run no longer exists."})
        except (ValueError, RecursionError, UnicodeError) as e:
            self._json(400, {"error": str(e)})
        except RuntimeError as e:
            self._json(409, {"error": str(e)})
        except OSError:
            self._json(503, {"error": "Could not save the task type; refresh and try again." if is_task_type else
                                    "Could not save the review; refresh and try again."})

    def _unsupported_write(self):
        self._json(405, {"error": "Only review POST actions are supported."})

    do_PUT = _unsupported_write
    do_PATCH = _unsupported_write
    do_DELETE = _unsupported_write


def make_server(runs_dir, *, port=8765):
    handler = partial(DashboardHandler, store=RunStore(runs_dir), csrf_token=secrets.token_urlsafe(32))
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def dispatch(args):
    if not 0 <= args.port <= 65535:
        print("error: dashboard port must be between 0 and 65535", file=sys.stderr)
        return 2
    try:
        server = make_server(args.runs_dir or rundir.RUNS_DIR, port=args.port)
    except (OSError, ValueError) as e:
        print(f"error: cannot start dashboard: {e}", file=sys.stderr)
        return 2
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"dirtywork dashboard: {url}\nReading {server.RequestHandlerClass.keywords['store'].root}\n"
          "Local review controls · Ctrl-C to stop", flush=True)
    try:
        if args.open:
            try:
                webbrowser.open(url)
            except webbrowser.Error as e:
                print(f"warning: could not open browser: {e}", file=sys.stderr)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
