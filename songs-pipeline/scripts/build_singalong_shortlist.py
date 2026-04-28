"""Build a curated singalong shortlist from songs.db.

Usage:
  python scripts/build_singalong_shortlist.py
  python scripts/build_singalong_shortlist.py --target 400
  python scripts/build_singalong_shortlist.py --output shortlists/singalong_400.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = PIPELINE_DIR / "songs.db"
DEFAULT_OUTPUT = PIPELINE_DIR / "shortlists" / "singalong_400.csv"


@dataclass(frozen=True)
class ArtistRule:
    label: str
    patterns: tuple[str, ...]
    cap: int
    bucket: str


RULES: tuple[ArtistRule, ...] = (
    ArtistRule("ABBA", ("ABBA",), 999, "core-classics"),
    ArtistRule("Queen", ("Queen",), 70, "core-classics"),
    ArtistRule("Rihanna", ("Rihanna",), 35, "noughties-pop"),
    ArtistRule("Bruno Mars", ("Bruno Mars",), 25, "noughties-pop"),
    ArtistRule("The Killers", ("The Killers",), 20, "uk-singalong"),
    ArtistRule("Coldplay", ("Coldplay",), 25, "uk-singalong"),
    ArtistRule("Oasis", ("Oasis",), 20, "uk-singalong"),
    ArtistRule("Adele", ("Adele",), 20, "uk-singalong"),
    ArtistRule("Arctic Monkeys", ("Arctic Monkeys",), 12, "uk-singalong"),
    ArtistRule("Amy Winehouse", ("Amy Winehouse",), 8, "uk-singalong"),
    ArtistRule("Robbie Williams", ("Robbie Williams",), 18, "uk-singalong"),
    ArtistRule("Take That", ("Take That",), 18, "uk-singalong"),
    ArtistRule("Girls Aloud", ("Girls Aloud",), 12, "uk-singalong"),
    ArtistRule("Westlife", ("Westlife",), 12, "uk-singalong"),
    ArtistRule("Spice Girls", ("Spice Girls",), 12, "uk-singalong"),
    ArtistRule("Kylie Minogue", ("Kylie Minogue",), 15, "uk-singalong"),
    ArtistRule("S Club 7", ("S Club 7",), 8, "uk-singalong"),
    ArtistRule("Busted", ("Busted",), 10, "uk-singalong"),
    ArtistRule("McFly", ("McFly",), 4, "uk-singalong"),
    ArtistRule("Leona Lewis", ("Leona Lewis",), 10, "uk-singalong"),
    ArtistRule("Duffy", ("Duffy",), 5, "uk-singalong"),
    ArtistRule("JLS", ("JLS",), 3, "uk-singalong"),
    ArtistRule("One Direction", ("One Direction",), 15, "uk-singalong"),
    ArtistRule("Ed Sheeran", ("Ed Sheeran",), 20, "uk-singalong"),
    ArtistRule("Sam Smith", ("Sam Smith",), 10, "uk-singalong"),
    ArtistRule("George Ezra", ("George Ezra",), 7, "uk-singalong"),
    ArtistRule("Lewis Capaldi", ("Lewis Capaldi",), 6, "uk-singalong"),
    ArtistRule("Lady Gaga", ("Lady Gaga",), 25, "global-pop"),
    ArtistRule("Katy Perry", ("Katy Perry",), 20, "global-pop"),
    ArtistRule("Britney Spears", ("Britney Spears",), 20, "global-pop"),
    ArtistRule("Backstreet Boys", ("Backstreet Boys",), 12, "global-pop"),
    ArtistRule("Justin Timberlake", ("Justin Timberlake",), 12, "global-pop"),
    ArtistRule("Pink", ("P!nk", "Pink"), 15, "global-pop"),
    ArtistRule("Kelly Clarkson", ("Kelly Clarkson",), 12, "global-pop"),
    ArtistRule("Avril Lavigne", ("Avril Lavigne",), 15, "global-pop"),
    ArtistRule("Paramore", ("Paramore",), 12, "global-pop"),
    ArtistRule("Green Day", ("Green Day",), 15, "global-pop"),
    ArtistRule("Blink-182", ("blink-182", "Blink 182"), 12, "global-pop"),
    ArtistRule("Bon Jovi", ("Bon Jovi",), 15, "global-pop"),
    ArtistRule("Journey", ("Journey",), 8, "core-classics"),
    ArtistRule("Wham", ("Wham", "Wham!"), 8, "core-classics"),
    ArtistRule("A-Ha", ("a-ha", "A-Ha"), 8, "core-classics"),
    ArtistRule("The Proclaimers", ("The Proclaimers",), 2, "core-classics"),
    ArtistRule("The Fratellis", ("The Fratellis",), 8, "uk-singalong"),
    ArtistRule("Kings of Leon", ("Kings of Leon",), 8, "uk-singalong"),
    ArtistRule("The Darkness", ("The Darkness",), 8, "uk-singalong"),
    ArtistRule("Scissor Sisters", ("Scissor Sisters",), 7, "uk-singalong"),
    ArtistRule("Black Eyed Peas", ("Black Eyed Peas", "The Black Eyed Peas"), 15, "global-pop"),
    ArtistRule("Outkast", ("Outkast",), 8, "global-pop"),
    ArtistRule("Usher", ("Usher",), 15, "global-pop"),
    ArtistRule("Ne-Yo", ("Ne-Yo", "Ne Yo"), 10, "global-pop"),
    ArtistRule("Shakira", ("Shakira",), 15, "global-pop"),
    ArtistRule("Dua Lipa", ("Dua Lipa",), 15, "global-pop"),
    ArtistRule("Calvin Harris", ("Calvin Harris",), 15, "global-pop"),
    ArtistRule("David Guetta", ("David Guetta",), 15, "global-pop"),
    ArtistRule("Flo Rida", ("Flo Rida",), 12, "global-pop"),
    ArtistRule("Taio Cruz", ("Taio Cruz",), 10, "global-pop"),
    ArtistRule("Pitbull", ("Pitbull",), 12, "global-pop"),
    ArtistRule("Mark Ronson", ("Mark Ronson",), 8, "global-pop"),
    ArtistRule("Carly Rae Jepsen", ("Carly Rae Jepsen",), 10, "global-pop"),
    ArtistRule("Lorde", ("Lorde",), 10, "global-pop"),
    ArtistRule("Miley Cyrus", ("Miley Cyrus",), 15, "global-pop"),
    ArtistRule("Taylor Swift", ("Taylor Swift",), 20, "global-pop"),
    ArtistRule("Maroon 5", ("Maroon 5",), 15, "global-pop"),
    ArtistRule("The Weeknd", ("The Weeknd",), 12, "global-pop"),
)

# Keep obvious non-performance versions out of the first pass.
BLOCKED_TITLE_TOKENS = (
    "instrumental",
    "karaoke",
    "tribute to",
    "chipmunk",
    "8 bit",
    "lyric video",
    "[duet]",
    "(duet",
    "live at",
    "bootleg",
)

BLOCKED_ARTIST_TOKENS = (
    "kidz bop",
    "bootleg",
    " vs ",
)

CONNECTOR_TOKENS = {"feat", "featuring", "ft", "with", "x", "and"}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _tokens(text: str) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9]+", text.lower()) if token]


def _song_key(artist: str, title: str) -> str:
    return f"{_norm(artist)}::{_norm(title)}"


def _load_all_candidates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    sql = (
        "SELECT usdb_id, artist, title, COALESCE(year, '') AS year, COALESCE(genre, '') AS genre, "
        "COALESCE(language, '') AS language, COALESCE(download_status, 'pending') AS download_status "
        "FROM songs "
        "WHERE usdb_id IS NOT NULL "
        "AND (language IS NULL OR language = '' OR LOWER(language) LIKE 'english%') "
        "ORDER BY "
        "CASE WHEN title LIKE '%(%' THEN 1 ELSE 0 END ASC, "
        "CASE WHEN year GLOB '[0-9][0-9][0-9][0-9]' THEN CAST(year AS INTEGER) ELSE 0 END DESC, "
        "title COLLATE NOCASE ASC"
    )
    return conn.execute(sql).fetchall()


def _artist_matches(artist: str, patterns: tuple[str, ...]) -> bool:
    artist_tokens = _tokens(artist)
    if not artist_tokens:
        return False

    for pattern in patterns:
        pattern_tokens = _tokens(pattern)
        if not pattern_tokens:
            continue
        window = len(pattern_tokens)
        for idx in range(0, len(artist_tokens) - window + 1):
            if artist_tokens[idx : idx + window] == pattern_tokens:
                if idx == 0:
                    return True
                if artist_tokens[idx - 1] in CONNECTOR_TOKENS:
                    return True
    return False


def _is_blocked_title(title: str) -> bool:
    lowered = title.lower()
    return any(token in lowered for token in BLOCKED_TITLE_TOKENS)


def _is_blocked_artist(artist: str) -> bool:
    lowered = f" {artist.lower()} "
    return any(token in lowered for token in BLOCKED_ARTIST_TOKENS)


def build_shortlist(conn: sqlite3.Connection, target: int) -> tuple[list[dict], dict[str, int]]:
    selected: list[dict] = []
    seen_ids: set[str] = set()
    seen_keys: set[str] = set()
    per_rule_counts: dict[str, int] = {}
    all_candidates = _load_all_candidates(conn)

    for rule in RULES:
        added = 0
        for row in all_candidates:
            usdb_id = (row["usdb_id"] or "").strip()
            artist = (row["artist"] or "").strip()
            title = (row["title"] or "").strip()

            if not usdb_id or not artist or not title:
                continue
            if not _artist_matches(artist, rule.patterns):
                continue
            if _is_blocked_artist(artist):
                continue
            if _is_blocked_title(title):
                continue
            if usdb_id in seen_ids:
                continue

            key = _song_key(artist, title)
            if key in seen_keys:
                continue

            selected.append(
                {
                    "usdb_id": usdb_id,
                    "artist": artist,
                    "title": title,
                    "year": row["year"] or "",
                    "genre": row["genre"] or "",
                    "language": row["language"] or "",
                    "source_bucket": rule.bucket,
                    "source_rule": rule.label,
                }
            )
            seen_ids.add(usdb_id)
            seen_keys.add(key)
            added += 1

            if added >= rule.cap or len(selected) >= target:
                break

        per_rule_counts[rule.label] = added
        if len(selected) >= target:
            break

    return selected, per_rule_counts


def write_outputs(rows: list[dict], output_csv: Path) -> tuple[Path, Path]:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    ids_path = output_csv.with_suffix(".ids.txt")

    with output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "rank",
                "usdb_id",
                "artist",
                "title",
                "year",
                "genre",
                "language",
                "source_bucket",
                "source_rule",
            ],
        )
        writer.writeheader()
        for idx, row in enumerate(rows, start=1):
            writer.writerow(
                {
                    "rank": idx,
                    **row,
                }
            )

    with ids_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(f"{row['usdb_id']}\n")

    return output_csv, ids_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a curated singalong shortlist from songs.db")
    parser.add_argument("--target", type=int, default=400, help="Target number of songs (default: 400)")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Output CSV path (default: shortlists/singalong_400.csv)",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows, per_rule_counts = build_shortlist(conn, target=args.target)
    output_csv, ids_path = write_outputs(rows, Path(args.output))

    print(f"Built shortlist with {len(rows)} songs")
    print(f"CSV: {output_csv}")
    print(f"IDs: {ids_path}")
    print()
    print("Top artists included:")
    for name, count in sorted(per_rule_counts.items(), key=lambda kv: kv[1], reverse=True):
        if count:
            print(f"  {name:20} {count:3d}")


if __name__ == "__main__":
    main()
