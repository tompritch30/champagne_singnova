"""Generate minimal UltraStar .txt stub files for every song in songs.db.

Songs that are already fully downloaded (download_status='complete') are skipped.
Already-existing stub folders are skipped unless --force is given.

Usage:
  python scripts/generate_stubs.py              # generate all stubs
  python scripts/generate_stubs.py --force      # overwrite existing stubs
  python scripts/generate_stubs.py --dry-run    # print what would be created
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# Force UTF-8 output on Windows so Unicode song titles don't crash
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))
from _common import DB_PATH, get_db, load_config, setup_logging


def _sanitize(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip(". ")


def _folder_name(artist: str, title: str) -> str:
    return f"{_sanitize(artist)} - {_sanitize(title)}"


def _build_stub(title: str, artist: str, year: str, language: str, genre: str,
                mp3_filename: str, cover_filename: str, usdb_id: int) -> str:
    lines = [
        f"#TITLE:{title}",
        f"#ARTIST:{artist}",
    ]
    if year:
        lines.append(f"#YEAR:{year}")
    if language:
        lines.append(f"#LANGUAGE:{language}")
    if genre:
        lines.append(f"#GENRE:{genre}")
    lines += [
        f"#MP3:{mp3_filename}",
        f"#COVER:{cover_filename}",
        "#BPM:120",
        "#GAP:0",
        f"#USDBID:{usdb_id}",
        "E",
        "",
    ]
    return "\n".join(lines)


def generate(force: bool, dry_run: bool, log) -> None:
    cfg = load_config()
    output_base = Path(cfg["songs_output_dir"])
    output_base.mkdir(parents=True, exist_ok=True)

    conn = get_db()
    rows = conn.execute(
        "SELECT usdb_id, artist, title, year, language, genre, download_status "
        "FROM songs ORDER BY artist, title"
    ).fetchall()

    created = 0
    skipped_complete = 0
    skipped_exists = 0

    for row in rows:
        usdb_id = row["usdb_id"]
        artist = row["artist"] or "Unknown Artist"
        title = row["title"] or "Unknown Title"
        year = row["year"] or ""
        language = row["language"] or ""
        genre = row["genre"] or ""
        status = row["download_status"] or "pending"

        if status == "complete":
            skipped_complete += 1
            continue

        folder = output_base / _folder_name(artist, title)
        txt_path = folder / "song.txt"

        # Skip if a real song.txt already exists with actual note data
        if txt_path.exists() and not force:
            content = txt_path.read_text(encoding="utf-8", errors="replace")
            # Real files have note lines starting with :, *, -, F
            has_notes = any(
                line.startswith((": ", "* ", "- ", "F "))
                for line in content.splitlines()
            )
            if has_notes:
                skipped_exists += 1
                continue

        mp3_filename = f"{_sanitize(artist)} - {_sanitize(title)}.mp3"
        cover_filename = "cover.jpg"

        stub = _build_stub(
            title=title,
            artist=artist,
            year=year,
            language=language,
            genre=genre,
            mp3_filename=mp3_filename,
            cover_filename=cover_filename,
            usdb_id=usdb_id,
        )

        if dry_run:
            print(f"  [DRY] {folder.name}/song.txt")
        else:
            folder.mkdir(parents=True, exist_ok=True)
            txt_path.write_text(stub, encoding="utf-8")
            created += 1
            if created % 500 == 0:
                log.info(f"  Created {created} stubs...")

    print()
    print("=" * 60)
    if dry_run:
        pending = len(rows) - skipped_complete - skipped_exists
        print(f"  DRY RUN: would create {pending} stubs")
    else:
        print(f"  Created  : {created} stub files")
        print(f"  Skipped  : {skipped_complete} already complete")
        print(f"  Skipped  : {skipped_exists} already have real notes")
        print(f"  Output   : {output_base}")
    print("=" * 60)
    print()
    if not dry_run:
        print("Next step: make sure the game's song folder includes:")
        print(f"  {output_base}")
        print("Then rescan songs in the game (Settings → Song Library).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate stub song files for all USDB songs")
    parser.add_argument("--force", action="store_true", help="Overwrite existing stub files")
    parser.add_argument("--dry-run", action="store_true", help="Print without writing")
    args = parser.parse_args()
    log = setup_logging("generate_stubs")
    generate(args.force, args.dry_run, log)


if __name__ == "__main__":
    main()
