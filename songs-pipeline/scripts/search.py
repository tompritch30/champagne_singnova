"""Search the local songs.db by title/artist/genre/status.

Usage:
  python scripts/search.py "Bruno Mars"
  python scripts/search.py "80s pop" --genre
  python scripts/search.py --status pending
  python scripts/search.py --status complete
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import get_db, print_table


def _status_label(row: sqlite3.Row) -> str:
    if row["download_status"] == "complete":
        return "complete"
    if row["has_txt"] and row["has_video"]:
        return "ready"
    if row["has_txt"] and not row["has_video"]:
        return "missing video"
    if row["has_txt"] and not row["has_cover"]:
        return "missing cover"
    return row["download_status"] or "pending"


def main() -> None:
    parser = argparse.ArgumentParser(description="Search local songs.db")
    parser.add_argument("query", nargs="?", default="", help="Search text")
    parser.add_argument("--genre", action="store_true", help="Filter by genre instead of artist/title")
    parser.add_argument("--status", choices=["complete", "pending", "missing_video", "all"],
                        help="Filter by download status")
    parser.add_argument("--limit", type=int, default=50, help="Max results (default 50)")
    args = parser.parse_args()

    conn = get_db()

    conditions: list[str] = []
    params: list = []

    if args.query:
        if args.genre:
            conditions.append("genre LIKE ?")
            params.append(f"%{args.query}%")
        else:
            conditions.append("(artist LIKE ? OR title LIKE ?)")
            params.extend([f"%{args.query}%", f"%{args.query}%"])

    if args.status == "complete":
        conditions.append("download_status = 'complete'")
    elif args.status == "pending":
        conditions.append("download_status = 'pending'")
    elif args.status == "missing_video":
        conditions.append("has_txt = 1 AND has_video = 0")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"SELECT * FROM songs {where} ORDER BY artist, title LIMIT ?"
    params.append(args.limit)

    rows = conn.execute(sql, params).fetchall()

    display = [
        {
            "artist": r["artist"],
            "title": r["title"],
            "year": r["year"] or "",
            "genre": r["genre"] or "",
            "language": r["language"] or "",
            "status": _status_label(r),
        }
        for r in rows
    ]
    print_table(display, ["artist", "title", "year", "genre", "language", "status"])


if __name__ == "__main__":
    main()
