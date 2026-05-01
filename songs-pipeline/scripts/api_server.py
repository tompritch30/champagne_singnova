"""Lightweight HTTP API for the USDB songs catalog.

Start with:  python scripts/api_server.py
Listens on:  http://localhost:5123

Endpoints:
  GET  /api/search?q=bohemian+queen&limit=30
  POST /api/download   body: {"usdb_id": "12345"}
  GET  /api/status?usdb_id=12345
  GET  /api/ping
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import threading
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).parent))
from _common import DB_PATH, load_config, PIPELINE_DIR

PORT = 5123

# Setup logging for API subprocess calls
_API_LOG_PATH = PIPELINE_DIR / "logs" / "api_downloads.log"
_API_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
_api_logger = logging.getLogger("api_server")
_api_logger.setLevel(logging.DEBUG)
_handler = logging.FileHandler(_API_LOG_PATH, encoding='utf-8')
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
_api_logger.addHandler(_handler)

import os as _os
import re as _re

_downloads_lock = threading.Lock()
_downloads: dict[str, subprocess.Popen] = {}  # usdb_id -> running Popen
_progress: dict[str, dict] = {}  # usdb_id -> {stage, percent, speed, eta}

_YTDLP_PERCENT_RE = _re.compile(r'\[download\]\s+(\d+\.?\d*)%(?:.*?at\s+(\S+).*?ETA\s+(\S+))?')
_YTDLP_DEST_RE = _re.compile(r'\[download\] Destination: (.+)')


def _update_progress(usdb_id: str, line: str) -> None:
    p = _progress.setdefault(usdb_id, {"stage": "initializing", "percent": 0, "speed": "", "eta": ""})
    low = line.lower()
    if "logging in" in low:
        p["stage"] = "login"
    elif "login ok" in low or "indexed metadata" in low or "fetching" in low or "usdb match" in low:
        p["stage"] = "metadata"
    elif "downloading song.txt" in low:
        p["stage"] = "txt"
    elif "already complete" in low:
        p.update({"stage": "complete", "percent": 100, "speed": "", "eta": ""})
    elif "[merger]" in low or "merging" in low or "post-process" in low:
        p["stage"] = "processing"
        p["percent"] = 99
    dest_m = _YTDLP_DEST_RE.search(line)
    if dest_m:
        dest = dest_m.group(1).lower()
        is_video = any(ext in dest for ext in (".mp4", ".webm", ".mkv", "video"))
        p["stage"] = "video" if is_video else "audio"
        p["percent"] = 0
        p["speed"] = ""
        p["eta"] = ""
    pct_m = _YTDLP_PERCENT_RE.search(line)
    if pct_m:
        p["percent"] = float(pct_m.group(1))
        p["speed"] = pct_m.group(2) or ""
        p["eta"] = pct_m.group(3) or ""


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silence default request logging

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def _json(self, data: object, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if parsed.path == "/api/ping":
            self._json({"ok": True})

        elif parsed.path == "/api/search":
            q = qs.get("q", [""])[0].strip()
            limit = min(int(qs.get("limit", ["30"])[0]), 100)
            conn = _get_conn()
            try:
                if not q:
                    rows = conn.execute(
                        "SELECT usdb_id, artist, title, year, language, genre, download_status "
                        "FROM songs ORDER BY artist LIMIT ?",
                        (limit,),
                    ).fetchall()
                else:
                    words = q.lower().split()
                    conditions = " AND ".join(
                        "(LOWER(artist) LIKE ? OR LOWER(title) LIKE ?)" for _ in words
                    )
                    params: list = [f"%{w}%" for w in words for _ in range(2)]
                    rows = conn.execute(
                        f"SELECT usdb_id, artist, title, year, language, genre, download_status "
                        f"FROM songs WHERE {conditions} "
                        f"ORDER BY "
                        f"  CASE WHEN LOWER(artist) = ? OR LOWER(title) = ? THEN 0 "
                        f"       WHEN LOWER(artist) LIKE ? OR LOWER(title) LIKE ? THEN 1 "
                        f"       ELSE 2 END, artist "
                        f"LIMIT ?",
                        params + [q.lower(), q.lower(), f"{q.lower()}%", f"{q.lower()}%", limit],
                    ).fetchall()
                results = [
                    {
                        "usdb_id": r["usdb_id"],
                        "artist": r["artist"] or "",
                        "title": r["title"] or "",
                        "year": r["year"] or "",
                        "language": r["language"] or "",
                        "genre": r["genre"] or "",
                        "status": r["download_status"] or "indexed",
                    }
                    for r in rows
                ]
                self._json({"results": results, "count": len(results)})
            finally:
                conn.close()

        elif parsed.path == "/api/status":
            usdb_id = qs.get("usdb_id", [""])[0].strip()
            if not usdb_id:
                self._json({"error": "usdb_id required"}, 400)
                return
            conn = _get_conn()
            try:
                row = conn.execute(
                    "SELECT download_status, artist, title, folder_path FROM songs WHERE usdb_id = ?",
                    (usdb_id,),
                ).fetchone()
                if not row:
                    self._json({"status": "not_found"})
                    return
                with _downloads_lock:
                    proc = _downloads.get(usdb_id)
                    running = proc is not None and proc.poll() is None
                prog = _progress.get(usdb_id, {})
                self._json(
                    {
                        "status": row["download_status"] or "indexed",
                        "running": running,
                        "artist": row["artist"] or "",
                        "title": row["title"] or "",
                        "folder_path": row["folder_path"] or "",
                        "stage": prog.get("stage", ""),
                        "stage_percent": prog.get("percent", 0),
                        "speed": prog.get("speed", ""),
                        "eta": prog.get("eta", ""),
                    }
                )
            finally:
                conn.close()

        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        if parsed.path == "/api/download":
            try:
                data = json.loads(body) if body else {}
            except json.JSONDecodeError:
                self._json({"error": "invalid JSON"}, 400)
                return
            usdb_id = str(data.get("usdb_id", "")).strip()
            if not usdb_id:
                self._json({"error": "usdb_id required"}, 400)
                return

            with _downloads_lock:
                proc = _downloads.get(usdb_id)
                if proc and proc.poll() is None:
                    self._json({"started": False, "reason": "already running"})
                    return

            if getattr(sys, "frozen", False):
                fetch_exe = Path(sys.executable).parent / "fetch_song.exe"
                cmd = [str(fetch_exe), "--id", usdb_id]
                fetch_script = fetch_exe
                cwd = Path(sys.executable).parent
            else:
                fetch_script = Path(__file__).parent / "fetch_song.py"
                cmd = [sys.executable, str(fetch_script), "--id", usdb_id]
                cwd = Path(__file__).parent.parent  # songs-pipeline directory
            
            new_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(cwd),
                env={**_os.environ, "PYTHONUNBUFFERED": "1"},
            )
            with _downloads_lock:
                _downloads[usdb_id] = new_proc

            # Log subprocess result in background
            def _cleanup():
                _log_subprocess_result(usdb_id, new_proc, fetch_script)
            threading.Thread(target=_cleanup, daemon=True).start()

            self._json({"started": True, "usdb_id": usdb_id})

        else:
            self._json({"error": "not found"}, 404)


def _log_subprocess_result(usdb_id: str, proc: subprocess.Popen, fetch_script: Path) -> None:
    """Stream subprocess stdout/stderr live to terminal and log file."""
    def _pipe(stream, is_err: bool):
        for raw in iter(stream.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip()
            if not line:
                continue
            tag = "ERR" if is_err else "OUT"
            msg = f"[dl:{usdb_id}][{tag}] {line}"
            print(msg, flush=True)
            if is_err:
                _api_logger.error(msg)
            else:
                _api_logger.info(msg)
            if not is_err:
                _update_progress(usdb_id, line)

    t_out = threading.Thread(target=_pipe, args=(proc.stdout, False), daemon=True)
    t_err = threading.Thread(target=_pipe, args=(proc.stderr, True), daemon=True)
    t_out.start()
    t_err.start()
    proc.wait()
    t_out.join()
    t_err.join()

    if proc.returncode != 0:
        msg = f"[dl:{usdb_id}] FAILED (exit {proc.returncode})"
        print(msg, flush=True)
        _api_logger.error(msg)
    else:
        msg = f"[dl:{usdb_id}] SUCCESS"
        print(msg, flush=True)
        _api_logger.info(msg)


def main() -> None:
    try:
        load_config()
    except SystemExit as e:
        print(f"[WARNING] config.json issue: {e} — server starting anyway")
    except Exception as e:
        print(f"[WARNING] config.json: {e} — server starting anyway")
    conn = _get_conn()
    total = conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
    complete = conn.execute("SELECT COUNT(*) FROM songs WHERE download_status='complete'").fetchone()[0]
    conn.close()
    print(f"USDB API server  http://127.0.0.1:{PORT}")
    print(f"  Database : {DB_PATH}")
    print(f"  Songs    : {total:,} indexed  |  {complete:,} downloaded")
    print()
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")


if __name__ == "__main__":
    main()
