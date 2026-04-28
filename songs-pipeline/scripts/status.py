"""Scan downloads/ folder and report status of all songs.

Usage:
  python scripts/status.py
  python scripts/status.py --fix       # re-download missing video files
  python scripts/status.py --sync      # sync downloads/ into songs.db
"""

from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import get_db, load_config, setup_logging, upsert_song, _now


_VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".avi", ".mov"}
_AUDIO_EXTS = {".mp3", ".ogg", ".aac", ".flac", ".wav", ".m4a"}


def _scan_folder(folder: Path) -> dict:
    files = {f.suffix.lower(): f for f in folder.iterdir() if f.is_file()}
    txt = any(f.suffix == ".txt" for f in folder.iterdir() if f.is_file())
    video = any(s in _VIDEO_EXTS for s in files)
    audio = any(s in _AUDIO_EXTS for s in files)
    cover = any(f.stem.lower() == "cover" and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
                for f in folder.iterdir() if f.is_file())
    return {"has_txt": txt, "has_video": video, "has_audio": audio, "has_cover": cover}


def sync_to_db(conn: sqlite3.Connection, downloads_dir: Path, log) -> None:  # noqa: ANN001
    """Walk downloads/ and upsert every subfolder into songs.db."""
    count = 0
    for folder in sorted(downloads_dir.iterdir()):
        if not folder.is_dir():
            continue
        state = _scan_folder(folder)
        # Try to read artist/title from song.txt #ARTIST / #TITLE headers
        artist, title = "", ""
        txt_files = list(folder.glob("*.txt"))
        if txt_files:
            try:
                for line in txt_files[0].read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.upper().startswith("#ARTIST:"):
                        artist = line.split(":", 1)[1].strip()
                    elif line.upper().startswith("#TITLE:"):
                        title = line.split(":", 1)[1].strip()
            except Exception:
                pass
        if not artist or not title:
            parts = folder.name.split(" - ", 1)
            artist = parts[0].strip() if parts else folder.name
            title = parts[1].strip() if len(parts) > 1 else ""

        # Look up existing DB row by folder path
        row = conn.execute("SELECT usdb_id FROM songs WHERE folder_path = ?", (str(folder),)).fetchone()
        status = "complete" if (state["has_txt"] and state["has_video"]) else \
                 "partial" if state["has_txt"] else "unknown"
        upsert_song(conn, {
            "usdb_id": row["usdb_id"] if row else f"local_{folder.name}",
            "artist": artist,
            "title": title,
            "folder_path": str(folder),
            "has_txt": int(state["has_txt"]),
            "has_audio": int(state["has_audio"]),
            "has_video": int(state["has_video"]),
            "has_cover": int(state["has_cover"]),
            "download_status": status,
            "last_updated": _now(),
        })
        count += 1
    log.info(f"Synced {count} folder(s) into songs.db")


def report(conn: sqlite3.Connection, downloads_dir: Path) -> dict:
    total_db = conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
    complete = conn.execute(
        "SELECT COUNT(*) FROM songs WHERE download_status = 'complete'"
    ).fetchone()[0]
    missing_video = conn.execute(
        "SELECT COUNT(*) FROM songs WHERE has_txt = 1 AND has_video = 0 AND download_status != 'pending'"
    ).fetchone()[0]
    missing_cover = conn.execute(
        "SELECT COUNT(*) FROM songs WHERE has_txt = 1 AND has_cover = 0 AND has_video = 1"
    ).fetchone()[0]
    broken = conn.execute(
        "SELECT COUNT(*) FROM songs WHERE has_txt = 0 AND download_status NOT IN ('pending', 'unknown')"
    ).fetchone()[0]
    pending = conn.execute(
        "SELECT COUNT(*) FROM songs WHERE download_status = 'pending'"
    ).fetchone()[0]

    # Also scan actual folders on disk
    disk_folders = [f for f in downloads_dir.iterdir() if f.is_dir()] if downloads_dir.exists() else []
    disk_complete = sum(1 for f in disk_folders if _scan_folder(f)["has_txt"] and _scan_folder(f)["has_video"])

    print()
    print("=" * 50)
    print("  SONG PIPELINE STATUS")
    print("=" * 50)
    print(f"  Songs in DB           : {total_db}")
    print(f"  Complete (txt+video)  : {complete}")
    print(f"  Missing video only    : {missing_video}")
    print(f"  Missing cover only    : {missing_cover}")
    print(f"  Broken (no txt)       : {broken}")
    print(f"  Pending (not started) : {pending}")
    print(f"  Disk folders total    : {len(disk_folders)}")
    print(f"  Disk complete         : {disk_complete}")
    print("=" * 50)
    print()
    return {"missing_video_count": missing_video}


def fix_missing_videos(conn: sqlite3.Connection, cfg: dict, log) -> None:  # noqa: ANN001
    """Attempt to re-download video for songs that have txt but no video."""
    rows = conn.execute(
        "SELECT usdb_id, artist, title, youtube_id, folder_path FROM songs "
        "WHERE has_txt = 1 AND has_video = 0 AND usdb_id IS NOT NULL"
    ).fetchall()

    if not rows:
        log.info("Nothing to fix — all songs have videos.")
        return

    log.info(f"Attempting to fix {len(rows)} missing video(s)...")
    from _common import download_video, UsdbSession

    for row in rows:
        artist = row["artist"]
        title = row["title"]
        youtube_id = row["youtube_id"]
        folder = Path(row["folder_path"]) if row["folder_path"] else None

        if not folder or not folder.exists():
            log.warning(f"  Skipping {artist} - {title}: folder not found")
            continue

        if not youtube_id:
            log.warning(f"  Skipping {artist} - {title}: no YouTube ID in DB (run fetch_song.py to re-fetch)")
            continue

        log.info(f"  Downloading video for: {artist} - {title} (yt/{youtube_id})")
        video_path = download_video(
            youtube_id, folder, f"{artist} - {title}",
            video_format=cfg.get("video_format", "mp4"), log=log, cfg=cfg,
        )
        if video_path:
            conn.execute(
                "UPDATE songs SET has_video=1, download_status='complete', last_updated=? "
                "WHERE usdb_id=?",
                (_now(), row["usdb_id"]),
            )
            conn.commit()
            log.info(f"    Fixed: {video_path.name}")
        else:
            log.error(f"    Still failed for {artist} - {title}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Songs pipeline status reporter")
    parser.add_argument("--fix", action="store_true", help="Re-download missing video files")
    parser.add_argument("--sync", action="store_true", help="Sync downloads/ folder into songs.db")
    args = parser.parse_args()

    cfg = load_config()
    log = setup_logging("status")
    conn = get_db()
    downloads_dir = Path(cfg["songs_output_dir"])

    if args.sync:
        sync_to_db(conn, downloads_dir, log)

    stats = report(conn, downloads_dir)

    if args.fix:
        fix_missing_videos(conn, cfg, log)
        report(conn, downloads_dir)


if __name__ == "__main__":
    main()
