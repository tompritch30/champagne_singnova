"""Apply sanitize_filename() to all existing downloads and DB paths.

    python scripts/fix_filenames.py [--dry-run]

1. Renames every download folder using sanitize_filename().
2. Renames every file inside (mp3, cover, txt) using sanitize_filename().
3. Updates folder_path in songs.db to match the new names.
4. Patches song.txt: normalizes #MP3: tag, adds #ENCODING:UTF8.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).parent))
from _common import get_db, load_config, sanitize_filename


def _read_header(lines: list[str], tag: str) -> str:
    prefix = f"#{tag.upper()}:"
    for line in lines:
        if line.upper().startswith(prefix):
            return line.split(":", 1)[1].strip()
    return ""


def patch_txt(txt_path: Path, dry: bool, conn) -> None:
    """Normalize #MP3:, add #ENCODING:UTF8, add #USDBID: if found in DB."""
    try:
        lines = txt_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except Exception as e:
        print(f"  [WARN] cannot read {txt_path}: {e}")
        return

    has_encoding = any(l.upper().startswith("#ENCODING:") for l in lines)
    has_usdbid = any(l.upper().startswith("#USDBID:") for l in lines)
    new_lines = []
    changed = False

    for line in lines:
        if line.upper().startswith("#MP3:"):
            raw = line.split(":", 1)[1].strip()
            clean = sanitize_filename(raw)
            if clean != raw:
                new_lines.append(f"#MP3:{clean}\n")
                changed = True
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    if not has_encoding and new_lines:
        new_lines.insert(1, "#ENCODING:UTF8\n")
        changed = True

    if not has_usdbid and new_lines:
        artist = _read_header(lines, "ARTIST")
        title = _read_header(lines, "TITLE")
        if artist and title:
            row = conn.execute(
                "SELECT usdb_id FROM songs WHERE LOWER(artist) = LOWER(?) AND LOWER(title) = LOWER(?)",
                (artist, title),
            ).fetchone()
            if row and row[0]:
                new_lines.insert(1, f"#USDBID:{row[0]}\n")
                changed = True

    if changed and not dry:
        txt_path.write_text("".join(new_lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    dry = args.dry_run

    cfg = load_config()
    downloads_dir = Path(cfg["songs_output_dir"])
    conn = get_db()

    if dry:
        print("[DRY RUN]\n")

    for folder in sorted(downloads_dir.iterdir()):
        if not folder.is_dir():
            continue

        # 1. Rename the folder itself
        clean_folder_name = sanitize_filename(folder.name)
        new_folder = folder.parent / clean_folder_name
        if clean_folder_name != folder.name:
            print(f"RENAME folder: {folder.name!r} -> {clean_folder_name!r}")
            if not dry:
                if not new_folder.exists():
                    folder.rename(new_folder)
                    conn.execute(
                        "UPDATE songs SET folder_path = ? WHERE folder_path = ?",
                        (str(new_folder), str(folder)),
                    )
                    conn.commit()
                    folder = new_folder
                else:
                    print(f"  [SKIP] target already exists")
                    continue

        # 2. Rename files inside the folder
        for f in list(folder.iterdir()):
            if f.is_dir():
                continue
            clean_file_name = sanitize_filename(f.stem) + f.suffix
            if clean_file_name != f.name:
                print(f"  RENAME file: {f.name!r} -> {clean_file_name!r}")
                if not dry:
                    new_f = f.parent / clean_file_name
                    if not new_f.exists():
                        f.rename(new_f)

        # 3. Patch song.txt
        txt = folder / "song.txt"
        if txt.exists():
            patch_txt(txt, dry, conn)

    # 4. Also normalize all folder_path values in DB (catches any leftover paths)
    if not dry:
        rows = conn.execute("SELECT rowid, folder_path FROM songs WHERE folder_path IS NOT NULL").fetchall()
        for row in rows:
            parts = Path(row["folder_path"]).parts
            clean_parts = [sanitize_filename(p) if i == len(parts) - 1 else p for i, p in enumerate(parts)]
            clean_path = str(Path(*clean_parts))
            if clean_path != row["folder_path"]:
                conn.execute("UPDATE songs SET folder_path = ? WHERE rowid = ?", (clean_path, row["rowid"]))
        conn.commit()
        print("\nDB folder_path values normalized.")

    print("\nDone" + (" (dry run)" if dry else "") + ".")


if __name__ == "__main__":
    main()
