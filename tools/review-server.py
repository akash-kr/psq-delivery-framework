#!/usr/bin/env python3
"""Review server — serves annotated HTML docs and receives submitted annotations.

Usage:
    python3 review-server.py --root /path/to/docs [--port 7788]

Endpoints:
    GET  /api/health    → {"ok": true}   (the doc uses this to detect the server
                                          and reveal its "Submit for revision" button)
    POST /api/reviews   → persists the submitted annotation payload as
                          reviews/inbox/<slug>-<timestamp>.json and .md,
                          and appends a line to reviews/queue.jsonl
    GET  /*             → static files from --root

Zero third-party dependencies. Pair with review-watcher.sh to auto-start
agent runs on each submission.
"""
import argparse
import json
import re
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "doc").lower()).strip("-")[:60] or "doc"


def notes_to_md(data):
    """Render a submission to the same markdown format the doc's export produces."""
    doc = data.get("doc", "Untitled")
    src = data.get("file", "")
    ts = time.strftime("%Y-%m-%d %H:%M")
    md = f"# Review notes — {doc}\n\n_Submitted {ts} · source file: {src}_\n\n"
    page_note = (data.get("pageNote") or "").strip()
    if page_note:
        md += f"## Overall page note\n\n{page_note}\n\n"
    items = data.get("items") or []
    if items:
        md += f"## Highlights & comments ({len(items)})\n\n"
        by_sec = {}
        for it in items:
            by_sec.setdefault(it.get("sec", "Intro"), []).append(it)
        for sec, its in by_sec.items():
            md += f"### § {sec}\n\n"
            for n, it in enumerate(its, 1):
                quote = (it.get("quote") or "").replace("\n", " ")
                comment = it.get("comment") or "_(highlight only, no comment)_"
                md += f"{n}. > {quote}\n\n   **Note:** {comment}\n\n"
    return md


class Handler(SimpleHTTPRequestHandler):
    root = None      # Path to serve
    reviews = None   # Path to reviews dir

    def do_GET(self):
        if self.path == "/api/health":
            return self._json({"ok": True})
        return super().do_GET()

    def do_POST(self):
        if self.path != "/api/reviews":
            return self.send_error(404)
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._json({"ok": False, "error": "bad json"}, 400)

        slug = slugify(data.get("doc"))
        ts = time.strftime("%Y%m%d-%H%M%S")
        inbox = self.reviews / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        base = inbox / f"{slug}-{ts}"
        base.with_suffix(".json").write_text(json.dumps(data, indent=2))
        base.with_suffix(".md").write_text(notes_to_md(data))
        with open(self.reviews / "queue.jsonl", "a") as f:
            f.write(json.dumps({
                "file": str(base.with_suffix(".json")),
                "doc": data.get("doc"),
                "source_file": data.get("file"),
                "note_count": len(data.get("items") or []),
                "ts": ts,
            }) + "\n")
        print(f"[review-server] saved {base.name} "
              f"({len(data.get('items') or [])} notes)")
        return self._json({"ok": True, "saved": base.name})

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # quieter static-file logging
        if "/api/" in (args[0] if args else ""):
            super().log_message(fmt, *args)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=".", help="folder to serve (contains the HTML docs)")
    ap.add_argument("--port", type=int, default=7788)
    args = ap.parse_args()

    root = Path(args.root).resolve()
    Handler.root = root
    Handler.reviews = root / "reviews"

    import functools
    handler = functools.partial(Handler, directory=str(root))
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), handler)
    print(f"[review-server] serving {root} at http://localhost:{args.port}")
    print(f"[review-server] submissions land in {Handler.reviews / 'inbox'}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
