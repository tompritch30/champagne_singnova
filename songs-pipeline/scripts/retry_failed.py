"""Re-download songs with incomplete or missing audio.

Run fix_filenames.py first, then:
    python scripts/retry_failed.py [--dry-run] [--limit N]

Finds every folder in downloads/ that has:
  - a .part file (interrupted download), OR
  - song.txt but the #MP3: file is missing

Cleans .part files, resets DB status, re-runs fetch_song.py --id.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).parent))
from _common import get_db, load_config

PIPELINE_DIR = Path(__file__).resolve().parent.parent


def _mp3_from_txt(txt_path: Path) -> str | None:
    try:
        for line in txt_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.upper().startswith("#MP3:"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    dry = args.dry_run

    cfg = load_config()
    downloads_dir = Path(cfg["songs_output_dir"])
    conn = get_db()

    if dry:
        print("[DRY RUN]\n")

    to_retry: list[tuple[str, Path, list[Path]]] = []

    for folder in sorted(downloads_dir.iterdir()):
        if not folder.is_dir():
            continue
        txt = folder / "song.txt"
        if not txt.exists():
            continue

        part_files = list(folder.glob("*.part"))
        mp3_name = _mp3_from_txt(txt)
        has_mp3 = bool(mp3_name and (folder / mp3_name).exists())

        if not part_files and has_mp3:
            continue  # complete — skip

        row = conn.execute(
            "SELECT usdb_id FROM songs WHERE folder_path = ?", (str(folder),)
        ).fetchone()

        if not row:
            # Fallback: parse "Artist - Title" from folder name
            if " - " in folder.name:
                artist_guess, title_guess = folder.name.split(" - ", 1)
                row = conn.execute(
                    "SELECT usdb_id FROM songs WHERE artist = ? AND title = ?",
                    (artist_guess.strip(), title_guess.strip()),
                ).fetchone()
                if not row:
                    # Looser match
                    row = conn.execute(
                        "SELECT usdb_id FROM songs WHERE LOWER(artist) = LOWER(?) AND LOWER(title) = LOWER(?)",
                        (artist_guess.strip(), title_guess.strip()),
                    ).fetchone()

        if not row:
            # Still not found — skip
            continue

        to_retry.append((row["usdb_id"], folder, part_files))

    if args.limit:
        to_retry = to_retry[: args.limit]

    print(f"{len(to_retry)} song(s) to retry\n")

    fetch_script = Path(__file__).parent / "fetch_song.py"
    ok = fail = 0

    for i, (usdb_id, folder, part_files) in enumerate(to_retry, 1):
        row = conn.execute("SELECT artist, title FROM songs WHERE usdb_id = ?", (usdb_id,)).fetchone()
        label = f"{row['artist']} - {row['title']}" if row else usdb_id
        print(f"[{i}/{len(to_retry)}] {label}")

        if part_files:
            for pf in part_files:
                print(f"  delete .part: {pf.name}")
                if not dry:
                    try:
                        pf.unlink()
                    except Exception as e:
                        print(f"  [WARN] {e}")

        if dry:
            print(f"  would run: fetch_song.py --id {usdb_id}")
            continue

        conn.execute("UPDATE songs SET download_status = 'pending' WHERE usdb_id = ?", (usdb_id,))
        conn.commit()

        proc = subprocess.run(
            [sys.executable, str(fetch_script), "--id", usdb_id],
            cwd=str(PIPELINE_DIR),
        )
        if proc.returncode == 0:
            print("  OK")
            ok += 1
        else:
            print(f"  FAILED (exit {proc.returncode})")
            fail += 1

        if i < len(to_retry):
            time.sleep(2)

    if not dry:
        print(f"\nDone — {ok} ok, {fail} failed")
    else:
        print(f"\n[DRY RUN] {len(to_retry)} candidates. Run without --dry-run to download.")


if __name__ == "__main__":
    main()
