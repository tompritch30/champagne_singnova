"""Export the list of complete downloaded songs to shortlists/my_downloads.csv."""
import sys, csv
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from _common import get_db, PIPELINE_DIR

conn = get_db()
rows = conn.execute(
    "SELECT usdb_id, artist, title FROM songs WHERE download_status = 'complete' ORDER BY artist, title"
).fetchall()
out = PIPELINE_DIR / "shortlists" / "my_downloads.csv"
out.parent.mkdir(exist_ok=True)
with out.open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["usdb_id", "artist", "title"])
    w.writeheader()
    for r in rows:
        w.writerow({"usdb_id": r[0], "artist": r[1] or "", "title": r[2] or ""})
print(f"Saved {len(rows)} songs -> {out}")
