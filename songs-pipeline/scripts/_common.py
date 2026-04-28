"""Shared helpers for all pipeline scripts — stdlib only."""

from __future__ import annotations

import http.cookiejar
import json
import logging
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths  (frozen = compiled via PyInstaller)
# ---------------------------------------------------------------------------
_FROZEN = getattr(sys, "frozen", False)
PIPELINE_DIR = Path(sys.executable).parent if _FROZEN else Path(__file__).resolve().parent.parent
CONFIG_PATH = PIPELINE_DIR / "config.json"
DB_PATH     = PIPELINE_DIR / "songs.db"
LOG_PATH    = PIPELINE_DIR / "logs" / "download.log"

# ---------------------------------------------------------------------------
# USDB constants
# ---------------------------------------------------------------------------
USDB_BASE = "https://usdb.animux.de/"
USDB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Regex to extract song rows from USDB list page.
# Actual HTML structure (observed 2026):
#   <tr class="list_trN" data-songid="ID" data-lastchange="..." onmouseover=... onmouseout=...>
#     <td ...><audio ...><source src="..."/></audio><button ...>...</button></td>   ← sample player (complex)
#     <td onclick="..." style="..."><img src="data/cover/ID.jpg" ...></td>           ← cover
#     <td onclick="...">Artist</td>
#     <td onclick="..."><a href="?link=detail&id=ID">Title</td>                      ← note: unclosed </a>
#     <td onclick="...">Genre</td>
#     <td onclick="...">Year</td>
#     <td onclick="...">Edition</td>
#     <td onclick="...">GoldenNotes (Yes/No)</td>
#     <td onclick="...">Language</td>
#     <td onclick="...">Creator</td>
#     <td onclick="...">Rating HTML</td>
#     <td onclick="...">Views</td>
#     <td>...</td>
#   </tr>
_ROW_RE = re.compile(
    r'<tr class="list_tr\d"[^>]*data-songid="(?P<song_id>\d+)"[^>]*>.*?'   # row open
    r'<img src="(?P<cover_url>[^"]*)"[^>]*>.*?'                              # cover img
    r'<td[^>]*>(?P<artist>[^<]+)</td>\s*'                                    # artist cell
    r'<td[^>]*><a href[^>]*>(?P<title>[^<]+)',                               # title (unclosed a ok)
    re.DOTALL,
)
# Separate regex to grab year and language from the cells after title in each row block
_YEAR_RE = re.compile(
    r'data-songid="(?P<song_id>\d+)".*?'
    r'<td[^>]*>[^<]*</td>\s*'   # genre
    r'<td[^>]*>(?P<year>\d{4})?',
    re.DOTALL,
)
_LANG_RE = re.compile(
    r'data-songid="(?P<song_id>\d+)".*?'
    r'(?:<td[^>]*>[^<]*</td>\s*){6}'   # skip 6 cells after title: genre,year,edition,golden,→language
    r'<td[^>]*>(?P<language>[^<]*)',
    re.DOTALL,
)
_YOUTUBE_RE = re.compile(
    r'(?:https?://)?(?:www\.)?(?:'
    r'youtube\.com/(?:watch\?(?:[^"&\s]*&)*v=|embed/)|'
    r'youtu\.be/'
    r')(?P<vid>[A-Za-z0-9_-]{11})',
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        sys.exit(f"[ERROR] config.json not found at {CONFIG_PATH}")
    with CONFIG_PATH.open(encoding="utf-8") as f:
        cfg = json.load(f)
    for placeholder_key in ("usdb_username", "usdb_password"):
        val = cfg.get(placeholder_key, "")
        if val.startswith("<") and val.endswith(">"):
            sys.exit(
                f"[ERROR] config.json has placeholder value for '{placeholder_key}'.\n"
                f"  Edit {CONFIG_PATH} and fill in your real USDB credentials."
            )
    return cfg


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS songs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  usdb_id TEXT,
  artist TEXT,
  title TEXT,
  language TEXT,
  year TEXT,
  genre TEXT,
  youtube_id TEXT,
  folder_path TEXT,
  has_txt INTEGER DEFAULT 0,
  has_audio INTEGER DEFAULT 0,
  has_video INTEGER DEFAULT 0,
  has_cover INTEGER DEFAULT 0,
  source TEXT DEFAULT 'usdb',
  download_status TEXT DEFAULT 'pending',
  last_updated TEXT
);
CREATE INDEX IF NOT EXISTS idx_songs_usdb_id ON songs(usdb_id);
CREATE INDEX IF NOT EXISTS idx_songs_artist  ON songs(artist COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_songs_title   ON songs(title  COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_songs_status  ON songs(download_status);
"""


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(DB_SCHEMA)
    conn.commit()
    return conn


def upsert_song(conn: sqlite3.Connection, row: dict) -> None:
    """Insert or update a song row keyed on usdb_id."""
    row.setdefault("last_updated", _now())
    existing = conn.execute(
        "SELECT id FROM songs WHERE usdb_id = ?", (row["usdb_id"],)
    ).fetchone()
    if existing:
        sets = ", ".join(f"{k} = :{k}" for k in row if k != "usdb_id")
        conn.execute(f"UPDATE songs SET {sets} WHERE usdb_id = :usdb_id", row)
    else:
        cols = ", ".join(row.keys())
        placeholders = ", ".join(f":{k}" for k in row.keys())
        conn.execute(f"INSERT INTO songs ({cols}) VALUES ({placeholders})", row)
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(name: str, verbose: bool = True) -> logging.Logger:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger(name)
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG if verbose else logging.INFO)
    ch.setFormatter(fmt)
    log.addHandler(fh)
    log.addHandler(ch)
    return log


# ---------------------------------------------------------------------------
# USDB HTTP session
# ---------------------------------------------------------------------------

class UsdbSession:
    """Lightweight USDB client built on urllib + cookiejar."""

    def __init__(self, username: str, password: str, rate_limit: float = 2.0):
        self._user = username
        self._pass = password
        self._rate = rate_limit
        self._last_req: float = 0.0
        jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar)
        )
        self._logged_in = False

    # ------------------------------------------------------------------
    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_req
        if elapsed < self._rate:
            time.sleep(self._rate - elapsed)
        self._last_req = time.monotonic()

    def _get(self, url: str, params: dict | None = None) -> str:
        self._throttle()
        if params:
            url = url + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=USDB_HEADERS)
        with self._opener.open(req, timeout=20) as r:
            return r.read().decode("utf-8", errors="replace")

    def _post(self, url: str, data: dict, params: dict | None = None) -> str:
        self._throttle()
        if params:
            url = url + "?" + urllib.parse.urlencode(params)
        body = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(url, data=body, headers=USDB_HEADERS, method="POST")
        with self._opener.open(req, timeout=20) as r:
            return r.read().decode("utf-8", errors="replace")

    # ------------------------------------------------------------------
    def login(self) -> bool:
        html = self._post(
            USDB_BASE,
            {"user": self._user, "pass": self._pass, "login": "Login"},
        )
        # USDB shows "Welcome" or username on success; "invalid" on failure
        ok = (
            "invalid" not in html.lower()[:2000]
            and (
                "welcome" in html.lower()[:2000]
                or self._user.lower() in html.lower()[:5000]
            )
        )
        self._logged_in = ok
        return ok

    # ------------------------------------------------------------------
    def search(
        self,
        artist: str = "",
        title: str = "",
        language: str = "",
        limit: int = 50,
        start: int = 0,
    ) -> list[dict]:
        """Return list of song dicts from USDB song-list page."""
        payload = {
            "order": "id",
            "ud": "asc",
            "limit": str(limit),
            "details": "1",
            "start": str(start),
        }
        if artist:
            payload["interpret"] = artist
        if title:
            payload["title"] = title
        if language:
            payload["language"] = language
        html = self._post(USDB_BASE + "index.php", payload, params={"link": "list"})
        return _parse_song_list(html)

    # ------------------------------------------------------------------
    def get_txt(self, song_id: str) -> str:
        """Download the UltraStar TXT for a song.

        USDB shows a wait-timer page on first GET; the real TXT is returned by
        a POST to index.php?link=gettxt&id=<id> with body wd=1.
        The response wraps the TXT in a <textarea> element.
        """
        html = self._post(
            USDB_BASE + "index.php",
            {"wd": "1"},
            params={"link": "gettxt", "id": song_id},
        )
        # Extract content from <textarea>
        m = re.search(r'<textarea[^>]*>(.*?)</textarea>', html, re.DOTALL | re.IGNORECASE)
        if m:
            import html as _html_mod
            return _html_mod.unescape(m.group(1))
        # Fallback: if the response already IS the txt (no HTML wrapper)
        if "#TITLE" in html or "#ARTIST" in html:
            return html
        # First attempt may return the wait page; wait and retry once
        import time as _time
        _time.sleep(26)
        html2 = self._post(
            USDB_BASE + "index.php",
            {"wd": "1"},
            params={"link": "gettxt", "id": song_id},
        )
        m2 = re.search(r'<textarea[^>]*>(.*?)</textarea>', html2, re.DOTALL | re.IGNORECASE)
        if m2:
            import html as _html_mod2
            return _html_mod2.unescape(m2.group(1))
        return html2

    # ------------------------------------------------------------------
    def get_youtube_id(self, song_id: str) -> str | None:
        """Scrape the USDB song-detail page for the first YouTube video ID."""
        html = self._get(USDB_BASE + "index.php", {"id": song_id, "link": "detail"})
        m = _YOUTUBE_RE.search(html)
        return m.group("vid") if m else None

    # ------------------------------------------------------------------
    def download_cover(self, cover_url: str, dest: Path) -> bool:
        """Download a cover image; return True on success."""
        try:
            if cover_url and not cover_url.startswith("http"):
                cover_url = USDB_BASE.rstrip("/") + "/" + cover_url.lstrip("/")
            self._throttle()
            req = urllib.request.Request(cover_url, headers=USDB_HEADERS)
            with self._opener.open(req, timeout=20) as r:
                dest.write_bytes(r.read())
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# HTML parsing helpers
# ---------------------------------------------------------------------------

def _clean(s: str) -> str:
    import html as _html
    return _html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def _parse_song_list(html: str) -> list[dict]:
    """Parse song rows from USDB song-list HTML page."""
    songs = []

    # Split the HTML into per-song blocks by row boundary
    # Each block starts just before data-songid="..."
    row_starts = [m.start() for m in re.finditer(r'data-songid="\d+"', html)]

    for i, start in enumerate(row_starts):
        end = row_starts[i + 1] if i + 1 < len(row_starts) else len(html)
        block = html[start:end]

        sid_m = re.search(r'data-songid="(\d+)"', block)
        if not sid_m:
            continue
        song_id = sid_m.group(1)

        cover_m = re.search(r'<img src="([^"]*cover[^"]*|data/cover/[^"]*)"', block, re.IGNORECASE)
        if not cover_m:
            # fallback: any img after the audio block
            cover_m = re.search(r'</(?:audio|button)>.*?<img src="([^"]*)"', block, re.DOTALL)
        cover_url = cover_m.group(1) if cover_m else f"data/cover/{song_id}.jpg"

        # The artist, title, genre, year, edition, golden, language are in plain <td> cells
        # after the cover image. Extract all bare <td> text values (no nested tags content)
        td_texts = re.findall(r'<td[^>]*onclick=[^>]*>([^<]*)</td>', block)
        # Also grab <td><a href=...>Title text (no </a>)
        title_m = re.search(r'<a href[^>]*>([^<]+)', block)
        title = _clean(title_m.group(1)) if title_m else ""

        # td_texts layout after onclick tds: artist(0), then linked title is separate
        # Remaining onclick tds (after artist): genre, year, edition, golden_notes, language, creator, ...
        artist = _clean(td_texts[0]) if td_texts else ""
        genre = _clean(td_texts[1]) if len(td_texts) > 1 else ""
        year = _clean(td_texts[2]) if len(td_texts) > 2 else ""
        # edition at index 3, golden_notes at 4
        language = _clean(td_texts[5]) if len(td_texts) > 5 else ""
        if not language:
            language = _clean(td_texts[4]) if len(td_texts) > 4 else ""

        if not artist and not title:
            continue
        songs.append(
            {
                "usdb_id": song_id,
                "artist": artist,
                "title": title,
                "genre": genre,
                "year": year,
                "language": language,
                "cover_url": cover_url.strip(),
            }
        )
    return songs


# ---------------------------------------------------------------------------
# yt-dlp video download
# ---------------------------------------------------------------------------

def _find_ytdlp() -> str:
    """Return the yt-dlp executable path."""
    found = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    if found:
        return found
    candidates = [
        Path(sys.executable).parent / "yt-dlp.exe",  # bundled next to exe in dist build
        Path.home() / ".local" / "bin" / "yt-dlp.exe",
        Path.home() / ".local" / "bin" / "yt-dlp",
        Path("C:/Users") / Path.home().name / ".local" / "bin" / "yt-dlp.exe",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return "yt-dlp"


def _find_ffmpeg() -> str | None:
    """Return the directory containing ffmpeg, or None."""
    found = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if found:
        return str(Path(found).parent)
    candidates = [
        Path(sys.executable).parent,  # bundled next to exe in dist build
        Path("C:/ProgramData/chocolatey/bin"),
        Path("C:/ProgramData/scoop/apps/ffmpeg/current/bin"),
        Path(Path.home(), "scoop/apps/ffmpeg/current/bin"),
        Path("C:/ffmpeg/bin"),
        Path("C:/Program Files/ffmpeg/bin"),
    ]
    for d in candidates:
        if (d / "ffmpeg.exe").exists():
            return str(d)
    return None


def _yt_dlp_cookie_attempts(cfg: dict | None) -> list[tuple[str, list[str]]]:
    """Return yt-dlp cookie argument strategies in retry order."""
    attempts: list[tuple[str, list[str]]] = []
    seen: set[tuple[str, ...]] = set()

    def add_attempt(label: str, args: list[str]) -> None:
        key = tuple(args)
        if key in seen:
            return
        seen.add(key)
        attempts.append((label, args))

    add_attempt("default", [])

    if cfg:
        cookies_file = str(cfg.get("yt_dlp_cookies_file", "")).strip()
        if cookies_file:
            add_attempt("cookies-file", ["--cookies", cookies_file])

        browser_cfg = str(cfg.get("yt_dlp_cookies_from_browser", "")).strip()
        if browser_cfg:
            for browser_spec in [part.strip() for part in browser_cfg.split(",") if part.strip()]:
                add_attempt(f"cookies-from-browser:{browser_spec}", ["--cookies-from-browser", browser_spec])

    # Practical defaults on Windows in case config is not set.
    for browser in ("edge", "chrome", "firefox"):
        add_attempt(f"cookies-from-browser:{browser}", ["--cookies-from-browser", browser])

    return attempts


def download_video(
    youtube_id: str,
    output_dir: Path,
    song_name: str,
    video_format: str = "mp4",
    log: logging.Logger | None = None,
    cfg: dict | None = None,
) -> Path | None:
    """Download a YouTube video with yt-dlp; return Path to file or None."""
    output_dir.mkdir(parents=True, exist_ok=True)

    def cleanup_partial_streams() -> None:
        for leftover in output_dir.glob("video.f*"):
            try:
                leftover.unlink()
            except OSError:
                pass

    # Clean up any leftover partial streams from prior runs.
    cleanup_partial_streams()

    out_tmpl = str(output_dir / f"video.%(ext)s")
    ytdlp = _find_ytdlp()
    ffmpeg_dir = _find_ffmpeg()
    base_cmd = [
        ytdlp,
        f"https://www.youtube.com/watch?v={youtube_id}",
        "-o", out_tmpl,
        # Prefer a pre-muxed mp4 first (no ffmpeg needed); fall back to best+merge
        "--format",
        f"bestvideo[ext={video_format}][vcodec^=avc]+bestaudio[ext=m4a]/bestvideo[ext={video_format}]+bestaudio/best[ext={video_format}]/best",
        "--merge-output-format", video_format,
        "--no-playlist",
        "--no-warnings",
        "--progress",
    ]
    if ffmpeg_dir:
        base_cmd += ["--ffmpeg-location", ffmpeg_dir]

    cookie_attempts = _yt_dlp_cookie_attempts(cfg)
    success = False
    cookie_error_snippets = (
        "could not copy chrome cookie database",
        "could not copy firefox cookie database",
        "could not copy edge cookie database",
        "could not open cookie database",
        "could not read cookies",
        "failed to decrypt cookies",
    )

    if log:
        log.info(f"yt-dlp: downloading youtube/{youtube_id} -> {output_dir}")

    for index, (label, cookie_args) in enumerate(cookie_attempts):
        if index > 0:
            cleanup_partial_streams()

        cmd = base_cmd + cookie_args
        if log and cookie_args:
            log.info(f"yt-dlp: retrying with {label}")

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        blocked_by_bot_check = False
        cookie_extract_failed = False
        for line in proc.stdout:  # type: ignore[union-attr]
            line = line.rstrip()
            if line:
                print(f"  [yt-dlp] {line}", flush=True)
                lower = line.lower()
                if "sign in to confirm you" in lower and "not a bot" in lower:
                    blocked_by_bot_check = True
                if any(snippet in lower for snippet in cookie_error_snippets):
                    cookie_extract_failed = True
                if log:
                    log.debug(f"yt-dlp: {line}")

        proc.wait()
        if proc.returncode == 0:
            success = True
            break

        if log:
            log.error(f"yt-dlp exited with code {proc.returncode}")

        has_next_attempt = index + 1 < len(cookie_attempts)
        if not has_next_attempt:
            break

        # Retry with cookies if YouTube explicitly blocks anonymous/bot-like requests.
        if blocked_by_bot_check:
            if log:
                log.warning("yt-dlp blocked by YouTube bot check; trying next cookie strategy")
            continue

        if cookie_extract_failed:
            if log:
                log.warning("yt-dlp could not read browser cookies; trying next cookie strategy")
            continue

        # If the plain request fails for another reason, try one cookie strategy before giving up.
        if not cookie_args:
            if log:
                log.warning("yt-dlp failed without cookies; trying cookie strategy")
            continue

        break

    if not success:
        return None

    # Check if yt-dlp produced a clean merged file
    merged = output_dir / f"video.{video_format}"
    if merged.exists():
        return merged

    # yt-dlp left separate stream files — merge manually with ffmpeg
    stream_files = sorted(output_dir.glob("video.f*"))
    video_streams = [f for f in stream_files if f.suffix.lower() in (".mp4", ".mkv", ".webm")]
    audio_streams = [f for f in stream_files if f.suffix.lower() in (".m4a", ".aac", ".webm", ".ogg", ".mp3")]
    # If webm appears in both, it might be audio-only
    # Prefer the largest file as video
    if video_streams and audio_streams and video_streams[0] != audio_streams[0]:
        v_file = max(video_streams, key=lambda f: f.stat().st_size)
        a_file = max(audio_streams, key=lambda f: f.stat().st_size)
        ffmpeg_bin = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
        if ffmpeg_bin and v_file != a_file:
            if log:
                log.info(f"  Merging streams: {v_file.name} + {a_file.name} -> video.{video_format}")
            merge_cmd = [
                ffmpeg_bin, "-y",
                "-i", str(v_file),
                "-i", str(a_file),
                "-c:v", "copy",
                "-c:a", "aac" if video_format == "mp4" else "copy",
                str(merged),
            ]
            merge_proc = subprocess.run(merge_cmd, capture_output=True, text=True)
            if merge_proc.returncode == 0 and merged.exists():
                if log:
                    log.info(f"  Merge OK: {merged.name} ({merged.stat().st_size:,} bytes)")
                for stream in stream_files:
                    try:
                        stream.unlink()
                    except OSError:
                        pass
                return merged
            elif log:
                log.warning(f"  ffmpeg merge failed: {merge_proc.stderr[-200:]}")

    # Fallback: return the largest video-like file present
    candidates = sorted(
        [p for p in output_dir.iterdir() if p.suffix.lower() in (".mp4", ".webm", ".mkv")],
        key=lambda f: f.stat().st_size,
        reverse=True,
    )
    return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# UltraStar Play post-processing
# ---------------------------------------------------------------------------

def extract_audio_from_video(video_path: Path, audio_path: Path, log: logging.Logger | None = None) -> bool:
    """Extract mp3 audio from a video file using ffmpeg. Returns True on success."""
    ffmpeg_dir = _find_ffmpeg()
    if ffmpeg_dir:
        # _find_ffmpeg returns the directory; build the full executable path
        ffmpeg_bin = str(Path(ffmpeg_dir) / "ffmpeg.exe")
        if not Path(ffmpeg_bin).exists():
            ffmpeg_bin = str(Path(ffmpeg_dir) / "ffmpeg")
    else:
        ffmpeg_bin = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe") or ""
    if not ffmpeg_bin or not Path(ffmpeg_bin).exists():
        if log:
            log.warning("ffmpeg not found — cannot extract audio")
        return False
    cmd = [
        ffmpeg_bin, "-y",
        "-i", str(video_path),
        "-vn",                          # drop video stream
        "-c:a", "libmp3lame",
        "-q:a", "2",                    # VBR ~190kbps
        str(audio_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0 and audio_path.exists():
        if log:
            log.info(f"  Audio extracted: {audio_path.name} ({audio_path.stat().st_size:,} bytes)")
        return True
    if log:
        log.warning(f"  Audio extraction failed: {result.stderr[-300:]}")
    return False


def patch_txt_for_ultrastar(txt_path: Path, video_filename: str, log: logging.Logger | None = None) -> None:
    """Rewrite the #VIDEO tag from USDB format to the local filename UltraStar Play expects."""
    if not txt_path.exists():
        return
    content = txt_path.read_text(encoding="utf-8", errors="replace")
    new_lines = []
    for line in content.splitlines(keepends=True):
        upper = line.upper()
        if upper.startswith("#VIDEO:"):
            # Replace USDB's "v=ID,co=...,bg=..." with the local filename
            new_lines.append(f"#VIDEO:{video_filename}\n")
            if log:
                log.info(f"  Patched #VIDEO tag -> {video_filename}")
        else:
            new_lines.append(line)
    txt_path.write_text("".join(new_lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Terminal table formatter
# ---------------------------------------------------------------------------

def print_table(rows: list[dict], columns: list[str]) -> None:
    if not rows:
        print("  (no results)")
        return
    widths = {c: len(c) for c in columns}
    for row in rows:
        for c in columns:
            widths[c] = max(widths[c], len(str(row.get(c, ""))))
    sep = "+" + "+".join("-" * (w + 2) for w in widths.values()) + "+"
    header = "|" + "|".join(f" {c:<{widths[c]}} " for c in columns) + "|"
    print(sep)
    print(header)
    print(sep)
    for row in rows:
        line = "|" + "|".join(f" {str(row.get(c,'')):<{widths[c]}} " for c in columns) + "|"
        print(line)
    print(sep)
    print(f"  {len(rows)} row(s)")
