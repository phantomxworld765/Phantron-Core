# -*- coding: utf-8 -*-
"""
PHANTRON web server - the cyberpunk UI backend.

Built entirely on Python's standard library http.server, so there is nothing
to pip-install for the interface to work. It serves the single-page UI and a
tiny JSON API:

    GET  /                 -> the UI (ui/index.html)
    GET  /api/status       -> brain/voice status
    POST /api/chat         -> {"text": "..."} returns {"reply": "..."}
    GET  /api/events?after -> long-ish poll for activity events (thoughts/tools)
    POST /api/voice        -> {"start": true|false} toggles the voice loop

The event stream lets the UI render PHANTRON's live "thinking" and actions.
"""

from __future__ import annotations

import json
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import List
from urllib.parse import urlparse, parse_qs

from .assistant import Assistant

UI_FILE = Path(__file__).resolve().parent / "ui" / "index.html"


class EventBuffer:
    """A small ring buffer of events the UI polls for."""

    def __init__(self, limit: int = 400):
        self._items: List[dict] = []
        self._limit = limit
        self._lock = threading.Lock()
        self._seq = 0

    def push(self, payload: dict):
        with self._lock:
            self._seq += 1
            item = dict(payload)
            item["id"] = self._seq
            item["t"] = time.time()
            self._items.append(item)
            if len(self._items) > self._limit:
                self._items = self._items[-self._limit:]

    def after(self, last_id: int) -> List[dict]:
        with self._lock:
            return [e for e in self._items if e["id"] > last_id]


def make_handler(assistant: Assistant, events: EventBuffer):

    class Handler(BaseHTTPRequestHandler):
        # silence default console spam
        def log_message(self, *args):
            return

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _read_body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0) or 0)
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8", "replace"))
            except Exception:
                return {}

        # ---- GET ----
        def do_GET(self):
            route = urlparse(self.path)
            path = route.path

            if path in ("/", "/index.html"):
                try:
                    html = UI_FILE.read_text(encoding="utf-8")
                except Exception:
                    html = "<h1>PHANTRON</h1><p>UI file missing.</p>"
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
                return

            if path == "/api/status":
                self._json(assistant.status())
                return

            if path == "/api/events":
                qs = parse_qs(route.query)
                after = int((qs.get("after", ["0"])[0]) or 0)
                # short wait-for-data so the UI feels live without hammering
                deadline = time.time() + 20
                while time.time() < deadline:
                    batch = events.after(after)
                    if batch:
                        self._json({"events": batch})
                        return
                    time.sleep(0.25)
                self._json({"events": []})
                return

            self._json({"error": "not found"}, 404)

        # ---- POST ----
        def do_POST(self):
            path = urlparse(self.path).path
            body = self._read_body()

            if path == "/api/chat":
                text = (body.get("text") or "").strip()
                speak = bool(body.get("speak", False))
                if not text:
                    self._json({"reply": ""})
                    return
                events.push({"action": "You", "detail": text, "kind": "user"})
                reply = assistant.chat(text, speak=speak)
                events.push({"action": "PHANTRON", "detail": reply, "kind": "reply"})
                self._json({"reply": reply})
                return

            if path == "/api/voice":
                if body.get("start"):
                    ok = assistant.start_voice_loop()
                    self._json({"voice": "on" if ok else "unavailable"})
                else:
                    assistant.stop_voice_loop()
                    self._json({"voice": "off"})
                return

            self._json({"error": "not found"}, 404)

    return Handler


def serve(assistant: Assistant) -> None:
    cfg = assistant.cfg
    host = cfg.get("server.host", "127.0.0.1")
    port = int(cfg.get("server.port", 8770))

    events = EventBuffer()
    assistant.add_listener(events.push)

    handler = make_handler(assistant, events)
    httpd = ThreadingHTTPServer((host, port), handler)

    url = "http://%s:%d" % (host, port)
    print("\n  PHANTRON UI  ->  " + url + "\n")
    if cfg.get("server.open_browser", True):
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    # surface the greeting into the event log so the UI shows it immediately
    events.push({"action": "PHANTRON", "detail": assistant.greeting(), "kind": "reply"})

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  PHANTRON shutting down. Bye P7!\n")
    finally:
        httpd.server_close()
