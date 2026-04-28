"""Download songs from a shortlist CSV by USDB ID.

Usage:
  python scripts/download_shortlist.py shortlists/singalong_400.csv
  python scripts/download_shortlist.py shortlists/singalong_400.csv --limit 50
  python scripts/download_shortlist.py shortlists/singalong_400.csv --start-index 121
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = PIPELINE_DIR / "songs.db"
FETCH_SCRIPT = Path(__file__).resolve().parent / "fetch_song.py"


def load_shortlist(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Shortlist file not found: {path}")

    with path.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        return []

    if "usdb_id" not in rows[0]:
        raise ValueError("Shortlist CSV must include a 'usdb_id' column")

    deduped: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        sid = (row.get("usdb_id") or "").strip()
        if not sid or sid in seen:
            continue
        seen.add(sid)
        deduped.append(row)
    return deduped


def already_complete(conn: sqlite3.Connection, usdb_id: str) -> bool:
    row = conn.execute(
        "SELECT download_status FROM songs WHERE usdb_id = ?",
        (usdb_id,),
    ).fetchone()
    return bool(row and row[0] == "complete")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download songs from a shortlist CSV")
    parser.add_argument("shortlist", help="Path to shortlist CSV with a usdb_id column")
    parser.add_argument("--start-index", type=int, default=1, help="1-based row index to start from")
    parser.add_argument("--limit", type=int, default=0, help="Max songs to process (0 = all)")
    parser.add_argument("--sleep", type=float, default=1.0, help="Seconds between songs")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without downloading")
    parser.add_argument("--stop-on-error", action="store_true", help="Stop after first failed download")
    args = parser.parse_args()

    shortlist_path = Path(args.shortlist)
    if not shortlist_path.is_absolute():
        shortlist_path = PIPELINE_DIR / shortlist_path

    rows = load_shortlist(shortlist_path)
    if not rows:
        print("Shortlist is empty. Nothing to do.")
        return

    start_idx = max(1, args.start_index)
    start_offset = start_idx - 1
    selected = rows[start_offset:]
    if args.limit > 0:
        selected = selected[: args.limit]

    conn = sqlite3.connect(DB_PATH)

    total = len(selected)
    completed = 0
    skipped = 0
    failed = 0
    failures: list[str] = []

    print(f"Shortlist rows loaded   : {len(rows)}")
    print(f"Processing from row     : {start_idx}")
    print(f"Rows this run           : {total}")
    print(f"Dry run                 : {'yes' if args.dry_run else 'no'}")
    print()

    for index, row in enumerate(selected, start=start_idx):
        usdb_id = (row.get("usdb_id") or "").strip()
        artist = (row.get("artist") or "").strip()
        title = (row.get("title") or "").strip()
        prefix = f"[{index}/{start_idx + total - 1}]"

        if not usdb_id:
            print(f"{prefix} Skipping row with missing usdb_id")
            skipped += 1
            continue

        if already_complete(conn, usdb_id):
            print(f"{prefix} Already complete: {usdb_id}  {artist} - {title}")
            skipped += 1
            continue

        cmd = [sys.executable, str(FETCH_SCRIPT), "--id", usdb_id]
        print(f"{prefix} Downloading: {usdb_id}  {artist} - {title}")

        if args.dry_run:
            continue

        result = subprocess.run(cmd)
        if result.returncode == 0:
            completed += 1
        else:
            failed += 1
            failures.append(usdb_id)
            print(f"{prefix} FAILED: {usdb_id}")
            if args.stop_on_error:
                break

        if args.sleep > 0:
            time.sleep(args.sleep)

    print()
    print("=" * 60)
    print("  DOWNLOAD SHORTLIST SUMMARY")
    print("=" * 60)
    print(f"  Completed this run : {completed}")
    print(f"  Skipped            : {skipped}")
    print(f"  Failed             : {failed}")
    if failures:
        print(f"  Failed IDs         : {', '.join(failures)}")
    print("=" * 60)

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
