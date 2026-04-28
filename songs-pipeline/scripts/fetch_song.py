"""Fetch a song by search query: checks local db first, then downloads from USDB.

Usage:
  python scripts/fetch_song.py "Bohemian Rhapsody Queen"
  python scripts/fetch_song.py "Talking to the Moon Bruno Mars"
  python scripts/fetch_song.py --id 12345   (download by USDB ID directly)
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).parent))
from _common import (
    UsdbSession,  # noqa: F401 (used in _smart_search type hint)
    download_video,
    extract_audio_from_video,
    patch_txt_for_ultrastar,
    get_db,
    load_config,
    setup_logging,
    upsert_song,
    _now,
)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from _common import UsdbSession as _UsdbSession


def _sanitize(name: str) -> str:
    """Make a string safe for use as a folder name."""
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip(". ")


def _folder_for(artist: str, title: str, base: Path) -> Path:
    return base / f"{_sanitize(artist)} - {_sanitize(title)}"


def _smart_search(session: "UsdbSession", query: str, log) -> list[dict]:  # noqa: ANN001
    """Try several search strategies and return the first non-empty result list."""
    words = query.split()

    # Strategy 1: all words as title (works for "Talking to the Moon Bruno Mars")
    log.info(f"Search attempt 1: title='{query}'")
    results = session.search(title=query, limit=20)
    if results:
        return results

    # Strategy 2: last 1-2 words as artist, rest as title
    for n_artist in (2, 1, 3):
        if len(words) <= n_artist:
            continue
        title_part = " ".join(words[:-n_artist])
        artist_part = " ".join(words[-n_artist:])
        log.info(f"Search attempt: title='{title_part}' artist='{artist_part}'")
        results = session.search(title=title_part, artist=artist_part, limit=20)
        if results:
            return results

    # Strategy 3: all words as artist
    log.info(f"Search attempt: artist='{query}'")
    results = session.search(artist=query, limit=20)
    if results:
        return results

    # Strategy 4: first word as artist, rest as title
    if len(words) >= 2:
        log.info(f"Search attempt: artist='{words[0]}' title='{' '.join(words[1:])}'")
        results = session.search(artist=words[0], title=" ".join(words[1:]), limit=20)
        if results:
            return results

    return []


def fetch(query: str, usdb_id: str | None, cfg: dict, log) -> bool:  # noqa: ANN001
    conn = get_db()
    output_base = Path(cfg["songs_output_dir"])

    # --- 1. Check local db ---
    if usdb_id:
        existing_row = conn.execute("SELECT * FROM songs WHERE usdb_id = ?", (usdb_id,)).fetchone()
    else:
        existing_row = conn.execute(
            "SELECT * FROM songs WHERE download_status = 'complete' AND "
            "(artist LIKE ? OR title LIKE ?)",
            (f"%{query}%", f"%{query}%"),
        ).fetchone()

    if existing_row and existing_row["download_status"] == "complete":
        log.info(f"Already complete: {existing_row['artist']} - {existing_row['title']}")
        log.info(f"  Folder: {existing_row['folder_path']}")
        return True

    # --- 2. Build USDB search terms ---
    session = UsdbSession(
        cfg["usdb_username"],
        cfg["usdb_password"],
        rate_limit=float(cfg.get("rate_limit_seconds", 2)),
    )

    log.info("Logging in to USDB...")
    if not session.login():
        log.error("USDB login failed — check username/password in config.json")
        return False
    log.info("USDB login OK")
    time.sleep(1)

    if usdb_id:
        indexed_row = conn.execute(
            "SELECT artist, title, year, language, genre FROM songs WHERE usdb_id = ?",
            (usdb_id,),
        ).fetchone()
        if indexed_row and indexed_row["artist"] and indexed_row["title"]:
            results = [
                {
                    "usdb_id": usdb_id,
                    "artist": indexed_row["artist"],
                    "title": indexed_row["title"],
                    "year": indexed_row["year"] or "",
                    "language": indexed_row["language"] or "",
                    "genre": indexed_row["genre"] or "",
                    "cover_url": "",
                }
            ]
            log.info(
                "Using indexed metadata from songs.db for USDB ID "
                f"{usdb_id}: {indexed_row['artist']} - {indexed_row['title']}"
            )
        else:
            log.error(
                "USDB ID was provided but not found in songs.db metadata. "
                "Run bulk_index.py --resume first or download by query."
            )
            return False
    else:
        results = _smart_search(session, query, log)

    if not results:
        log.error(f"No USDB results for: {query}")
        return False

    log.info(f"Found {len(results)} USDB match(es). Using top result.")
    song = results[0]
    log.info(f"  => [{song['usdb_id']}] {song['artist']} - {song['title']} ({song['year']}) [{song['language']}]")

    sid = song["usdb_id"]
    artist = song["artist"]
    title = song["title"]
    folder = _folder_for(artist, title, output_base)
    folder.mkdir(parents=True, exist_ok=True)
    log.info(f"  Output folder: {folder}")

    # --- 3. Download TXT ---
    has_txt = False
    txt_path = folder / "song.txt"
    try:
        log.info("Downloading song.txt from USDB...")
        txt_content = session.get_txt(sid)
        if "#TITLE" in txt_content or "#ARTIST" in txt_content:
            txt_path.write_text(txt_content, encoding="utf-8")
            has_txt = True
            log.info(f"  song.txt saved ({len(txt_content)} bytes)")
        else:
            log.warning("  TXT response didn't look like a UltraStar file — may need login")
            txt_path.write_text(txt_content, encoding="utf-8")
    except Exception as e:
        log.error(f"  TXT download failed: {e}")

    # --- 4. Get YouTube ID and download video ---
    has_video = False
    youtube_id: str | None = None
    video_path: Path | None = None

    if cfg.get("download_video", True):
        # First try to extract YouTube ID from the TXT #VIDEO tag (fastest, no extra request)
        if has_txt and txt_path.exists():
            for line in txt_path.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.upper().startswith("#VIDEO:"):
                    m = re.search(r'v=([A-Za-z0-9_-]{11})', line)
                    if m:
                        youtube_id = m.group(1)
                        log.info(f"  YouTube ID from TXT #VIDEO tag: {youtube_id}")
                        break
        if not youtube_id:
            log.info(f"Fetching USDB detail page for song {sid} (no #VIDEO in TXT)...")
            youtube_id = session.get_youtube_id(sid)
        if youtube_id:
            log.info(f"  YouTube ID: {youtube_id}")
            video_path = download_video(
                youtube_id,
                folder,
                f"{artist} - {title}",
                video_format=cfg.get("video_format", "mp4"),
                log=log,
                cfg=cfg,
            )
            if video_path:
                has_video = True
                log.info(f"  Video saved: {video_path.name}")
            else:
                log.warning("  Video download failed (YouTube may be unavailable or geo-restricted)")
        else:
            log.warning("  No YouTube URL found on USDB detail page")

    # --- 4b. Post-process for UltraStar Play ---
    # UltraStar Play reads audio from #MP3 and video from #VIDEO in song.txt.
    # We need to:
    #   (a) Extract audio from video.mp4 → <Artist - Title>.mp3 (matches #MP3 tag)
    #   (b) Rewrite #VIDEO from USDB's "v=ID,..." format to local "video.mp4"
    has_audio = False
    if video_path and video_path.exists():
        # Read #MP3 filename from song.txt
        mp3_filename = f"{artist} - {title}.mp3"
        if txt_path.exists():
            for line in txt_path.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.upper().startswith("#MP3:"):
                    mp3_filename = line.split(":", 1)[1].strip()
                    break
        audio_dest = folder / mp3_filename
        log.info(f"  Extracting audio -> {mp3_filename}")
        has_audio = extract_audio_from_video(video_path, audio_dest, log)

        # Patch #VIDEO tag so UltraStar Play finds the local file
        patch_txt_for_ultrastar(txt_path, video_path.name, log)

    # --- 5. Download cover ---
    has_cover = False
    cover_url = song.get("cover_url", "")
    if cover_url:
        log.info("Downloading cover.jpg...")
        dest = folder / "cover.jpg"
        has_cover = session.download_cover(cover_url, dest)
        if has_cover:
            log.info(f"  cover.jpg saved ({dest.stat().st_size} bytes)")
        else:
            log.warning("  Cover download failed")

    # --- 6. Update DB ---
    status = "complete" if (has_txt and has_video and has_audio) else "partial" if has_txt else "failed"
    upsert_song(
        conn,
        {
            "usdb_id": sid,
            "artist": artist,
            "title": title,
            "language": song.get("language", ""),
            "year": song.get("year", ""),
            "genre": song.get("genre", ""),
            "youtube_id": youtube_id or "",
            "folder_path": str(folder),
            "has_txt": int(has_txt),
            "has_audio": int(has_audio),
            "has_video": int(has_video),
            "has_cover": int(has_cover),
            "source": "usdb",
            "download_status": status,
            "last_updated": _now(),
        },
    )

    # --- 7. Summary ---
    print()
    print("=" * 60)
    print(f"  Song     : {artist} - {title}")
    print(f"  USDB ID  : {sid}")
    print(f"  YouTube  : {youtube_id or 'not found'}")
    print(f"  Folder   : {folder}")
    print(f"  TXT      : {'OK' if has_txt else 'MISSING'}")
    print(f"  Video    : {'OK -> ' + (video_path.name if video_path else '') if has_video else 'MISSING'}")
    print(f"  Audio    : {'OK (extracted from video)' if has_audio else 'MISSING'}")
    print(f"  Cover    : {'OK' if has_cover else 'MISSING'}")
    print(f"  Status   : {status.upper()}")
    print("=" * 60)
    return status != "failed"


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch a song from USDB")
    parser.add_argument("query", nargs="?", default="", help="Search query, e.g. 'Bohemian Rhapsody Queen'")
    parser.add_argument("--id", dest="usdb_id", help="Download by USDB song ID directly")
    args = parser.parse_args()

    if not args.query and not args.usdb_id:
        parser.print_help()
        sys.exit(1)

    cfg = load_config()
    log = setup_logging("fetch_song")
    ok = fetch(args.query, args.usdb_id, cfg, log)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
