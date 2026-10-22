import os
import re
import secrets
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

APP_PRIVATE = os.environ.get("APP_PRIVATE") == "1"
APP_VARY = os.environ.get("APP_VARY") == "1"
APP_PAGES = int(os.environ["APP_PAGES"])
LOG = "/results/app.log"

sessions = {}
lock = threading.Lock()
log_file = open(LOG, "a", buffering=1)
ITEM = re.compile(r"^/items/([0-9]+)$")


def write_log(path, user):
    with lock:
        log_file.write(f"{time.time():.3f} {path} {user or '-'}\n")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def session_user(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        if "session" not in cookie:
            return None
        return sessions.get(cookie["session"].value)

    def send(self, body, extra):
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/login":
            user = parse_qs(url.query).get("user", [""])[0]
            sid = secrets.token_hex(16)
            with lock:
                sessions[sid] = user
            write_log(url.path, user)
            self.send("<!doctype html><p>Logged in</p>\n", [("Set-Cookie", f"session={sid}; Path=/; HttpOnly")])
            return
        m = ITEM.match(url.path)
        if not m or not 1 <= int(m.group(1)) <= APP_PAGES:
            self.send_error(404)
            return
        page = int(m.group(1))
        user = self.session_user()
        write_log(url.path, user)
        top = f"Hello, {user}" if user else '<a href="/login">Log in</a>'
        body = (
            "<!doctype html>\n"
            f"<html><head><title>Item {page}</title></head><body>\n"
            f"<!-- for:{user or 'anon'} page:{page} -->\n"
            f"<div class=\"top\">{top}</div>\n"
            f"<main><h1>Item {page}</h1><p>Description of item {page}.</p></main>\n"
            "</body></html>\n"
        )
        extra = []
        if APP_PRIVATE and user:
            extra.append(("Cache-Control", "private"))
        if APP_VARY:
            extra.append(("Vary", "Cookie"))
        self.send(body, extra)


ThreadingHTTPServer.daemon_threads = True
ThreadingHTTPServer.request_queue_size = 1024
ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
