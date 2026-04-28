"""Index all English USDB songs into songs.db (metadata only, no download).

Usage:
  python scripts/bulk_index.py                    # index all English songs
  python scripts/bulk_index.py --language English
  python scripts/bulk_index.py --max-pages 10     # limit pages (50 songs/page)
  python scripts/bulk_index.py --resume           # skip songs already in DB
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import UsdbSession, get_db, load_config, setup_logging, upsert_song, _now

PAGE_SIZE = 50


def main() -> None:
    parser = argparse.ArgumentParser(description="Index USDB songs into songs.db")
    parser.add_argument("--language", default="English", help="Language filter (default: English)")
    parser.add_argument("--max-pages", type=int, default=0, help="Max pages to fetch (0 = all)")
    parser.add_argument("--resume", action="store_true", help="Skip USDB IDs already in DB")
    args = parser.parse_args()

    cfg = load_config()
    log = setup_logging("bulk_index")
    conn = get_db()

    session = UsdbSession(
        cfg["usdb_username"],
        cfg["usdb_password"],
        rate_limit=float(cfg.get("rate_limit_seconds", 2)),
    )

    log.info("Logging in to USDB...")
    if not session.login():
        log.error("USDB login failed — check credentials in config.json")
        sys.exit(1)
    log.info("Login OK")

    # Pre-load existing IDs for --resume
    existing_ids: set[str] = set()
    if args.resume:
        rows = conn.execute("SELECT usdb_id FROM songs WHERE usdb_id IS NOT NULL").fetchall()
        existing_ids = {r[0] for r in rows}
        log.info(f"Resuming — {len(existing_ids)} songs already indexed")

    page = 0
    total_new = 0
    total_skipped = 0

    while True:
        start = page * PAGE_SIZE
        log.info(f"Fetching page {page + 1} (start={start}, language={args.language})...")

        try:
            songs = session.search(language=args.language, limit=PAGE_SIZE, start=start)
        except Exception as e:
            log.error(f"Search failed on page {page + 1}: {e}")
            break

        if not songs:
            log.info(f"No more results after page {page}. Done.")
            break

        for song in songs:
            sid = song["usdb_id"]
            if args.resume and sid in existing_ids:
                total_skipped += 1
                continue
            upsert_song(conn, {
                "usdb_id": sid,
                "artist": song["artist"],
                "title": song["title"],
                "language": song["language"],
                "year": song["year"],
                "genre": song["genre"],
                "source": "usdb",
                "download_status": "pending",
                "last_updated": _now(),
            })
            total_new += 1

        log.info(f"  Page {page + 1}: {len(songs)} songs, {total_new} new indexed, {total_skipped} skipped")

        if len(songs) < PAGE_SIZE:
            log.info("Last page reached.")
            break

        page += 1
        if args.max_pages and page >= args.max_pages:
            log.info(f"Reached max-pages limit ({args.max_pages}).")
            break

    total_in_db = conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
    log.info(f"Done. New this run: {total_new} | Total in DB: {total_in_db}")


if __name__ == "__main__":
    main()
