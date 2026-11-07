#!/usr/bin/env python3
"""Tiny server for the Budget page: serves index.html and stores the encrypted vault.

The browser encrypts the vault before sending it, so this server only ever sees ciphertext.
Standard library only.

    python3 server.py                  # http://localhost:8000, this machine only
    python3 server.py --host 0.0.0.0   # reachable from the network (put HTTPS in front, see README)
"""
import argparse
import hashlib
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VAULT = ROOT / "vault.json"
BACKUPS = ROOT / "backups"
KEEP_BACKUPS = 30              # how many old versions to keep
BACKUP_EVERY_SECONDS = 10 * 60  # at most one backup per 10 minutes of saving
MAX_BYTES = 50 * 1024 * 1024
STATIC = {"/": "index.html", "/index.html": "index.html"}  # nothing else in the folder is served

write_lock = threading.Lock()


def etag(data: bytes) -> str:
    return '"' + hashlib.sha256(data).hexdigest()[:32] + '"'


def backup(current: bytes) -> None:
    BACKUPS.mkdir(exist_ok=True)
    old = sorted(BACKUPS.glob("vault-*.json"))
    if old and time.time() - old[-1].stat().st_mtime < BACKUP_EVERY_SECONDS:
        return
    (BACKUPS / time.strftime("vault-%Y%m%d-%H%M%S.json")).write_bytes(current)
    for f in sorted(BACKUPS.glob("vault-*.json"))[:-KEEP_BACKUPS]:
        f.unlink()


def write_atomically(data: bytes) -> None:
    tmp = VAULT.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, VAULT)  # a crash mid-save never leaves a half-written vault


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, body=b"", ctype="text/plain; charset=utf-8", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in STATIC:
            return self.reply(200, (ROOT / STATIC[path]).read_bytes(), "text/html; charset=utf-8")
        if path == "/vault.json":
            if not VAULT.exists():
                return self.reply(404, b"no vault yet")
            data = VAULT.read_bytes()
            return self.reply(200, data, "application/json", {"ETag": etag(data)})
        self.reply(404, b"not found")

    do_HEAD = do_GET

    def do_PUT(self):
        if self.path != "/vault.json":
            return self.reply(404, b"not found")
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_BYTES:
            return self.reply(413, b"vault too large or empty")
        body = self.rfile.read(length)
        # Only accept something shaped like an encrypted vault, never plaintext data.
        try:
            f = json.loads(body)
            ok = f.get("format") == "budget-vault" and all(isinstance(f.get(k), str) for k in ("salt", "iv", "data"))
        except ValueError:
            ok = False
        if not ok:
            return self.reply(400, b"not an encrypted vault")

        with write_lock:
            current = VAULT.read_bytes() if VAULT.exists() else None
            expected = self.headers.get("If-Match")
            # Reject stale writes: the page must be saving on top of the version it loaded.
            if (current is None and expected) or (current is not None and expected != etag(current)):
                return self.reply(409, b"vault changed since it was loaded")
            if current is not None:
                backup(current)
            write_atomically(body)
        self.reply(200, b"saved", headers={"ETag": etag(body)})

    def log_message(self, fmt, *args):  # one short line per request
        print(f"{self.log_date_time_string()}  {self.command} {self.path.split('?', 1)[0]} -> {args[1] if len(args) > 1 else ''}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1", help="interface to listen on (default: this machine only)")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    shown = "localhost" if args.host in ("127.0.0.1", "localhost") else args.host
    print(f"Budget running at http://{shown}:{args.port}  (Ctrl+C to stop)")
    if args.host not in ("127.0.0.1", "localhost"):
        print("Note: browsers only allow the page's encryption on https:// or localhost. See README.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
