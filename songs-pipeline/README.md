# Songs Pipeline

A personal karaoke song pipeline for UltraStar Play.  
Downloads song lyrics (TXT), covers, and music videos from USDB + YouTube.

> **For personal use only.** Respect copyright and USDB terms of service.

---

## Prerequisites

| Tool | Install |
|------|---------|
| Python 3.10+ | [python.org](https://python.org) |
| yt-dlp | `pipx install yt-dlp` |
| ffmpeg | `choco install ffmpeg` or [ffmpeg.org](https://ffmpeg.org) |
| USDB account | [usdb.animux.de](https://usdb.animux.de) — free registration |

> **Note:** `usdb_syncer` (the GUI app) is NOT used by these scripts. The pipeline uses
> a direct urllib-based USDB client. Only `yt-dlp` and `ffmpeg` are required.

---

## Setup

1. **Clone / open the project** — all scripts live in `songs-pipeline/scripts/`.

2. **Edit `config.json`** with your credentials:

```json
{
  "usdb_username": "your_usdb_username",
  "usdb_password": "your_usdb_password",
  "deno_path": "C:\\path\\to\\deno.exe",
  "songs_output_dir": "D:\\path\\to\\songs-pipeline\\downloads",
  "ultrastar_songs_dir": "D:\\path\\to\\UltraStar Play\\StreamingAssets\\Songs",
  "download_video": true,
  "video_format": "mp4",
  "language_filter": "English",
  "rate_limit_seconds": 2,
  "yt_dlp_cookies_from_browser": "edge",
  "yt_dlp_cookies_file": ""
}
```

`yt_dlp_cookies_from_browser` and `yt_dlp_cookies_file` are optional. If omitted,
the downloader still runs and will try common browser-cookie fallbacks when YouTube
returns a bot-check challenge.

3. **Verify tools are on PATH:**
```
yt-dlp --version
ffmpeg -version
```

---

## Test download

```
python scripts/fetch_song.py "Talking to the Moon Bruno Mars"
```

Expected output:
```
Song     : Bruno Mars - Talking To The Moon
USDB ID  : 11156
YouTube  : hk9K032K7UM
TXT      : OK
Video    : OK -> video.mp4
Cover    : OK
Status   : COMPLETE
```

Output folder: `downloads/Bruno Mars - Talking To The Moon/`
- `song.txt` — UltraStar timed lyrics
- `video.mp4` — music video (video + audio merged)
- `cover.jpg` — album art

---

## Usage

### Fetch any song on demand

```bash
python scripts/fetch_song.py "Bohemian Rhapsody Queen"
python scripts/fetch_song.py "Shape of You Ed Sheeran"
python scripts/fetch_song.py "Mr. Brightside The Killers"
python scripts/fetch_song.py --id 12345          # by USDB song ID
```

The script:
1. Checks local `songs.db` — if already complete, prints the folder path and exits
2. Searches USDB for the best match (tries multiple query strategies)
3. Downloads `song.txt` (timed lyrics + pitch data)
4. Extracts the YouTube ID from the TXT `#VIDEO` tag
5. Downloads the full video with `yt-dlp` + merges with `ffmpeg`
6. Downloads the cover image
7. Writes the record to `songs.db` with `download_status = complete`

### Search your local library

```bash
python scripts/search.py "Bruno Mars"           # search by artist/title
python scripts/search.py "pop" --genre          # filter by genre
python scripts/search.py --status complete      # all fully downloaded songs
python scripts/search.py --status pending       # indexed but not downloaded
python scripts/search.py --limit 100            # increase result limit
```

### Check pipeline status

```bash
python scripts/status.py                # show counts
python scripts/status.py --sync        # sync downloads/ folder into songs.db
python scripts/status.py --fix         # re-download missing videos
```

### Bulk index English songs from USDB

This downloads metadata only (no videos) for thousands of songs at once:

```bash
python scripts/bulk_index.py                    # all English songs (~10k+)
python scripts/bulk_index.py --max-pages 10    # limit to 500 songs for testing
python scripts/bulk_index.py --resume          # skip already-indexed songs
python scripts/bulk_index.py --language French # different language
```

After indexing, use `fetch_song.py` to download individual songs.

---

## How UltraStar Play picks up songs

1. Open **UltraStar Play** → Settings → Song Library
2. Add the `downloads/` directory (or individual song folders) as a **Songs folder**
3. Alternatively, point the game at:
   `songs-pipeline/downloads/`
   and it will scan all subfolders automatically.

The game expects each song folder to contain:
- `*.txt` — UltraStar format lyrics file with `#ARTIST`, `#TITLE`, `#BPM`, `#GAP` headers
- `*.mp3` / `*.mp4` — audio or video file referenced by `#MP3` or `#VIDEO` in the TXT
- `*.jpg` — cover image referenced by `#COVER` (optional)

> **Note:** The downloaded `video.mp4` contains both video and audio (merged by ffmpeg).
> UltraStar Play can use the mp4 as the audio source too — update the `#MP3` header
> in `song.txt` to point to `video.mp4` if the game doesn't play audio automatically.

---

## File layout

```
songs-pipeline/
  config.json          ← credentials + paths (keep private, in .gitignore)
  songs.db             ← SQLite database of all indexed/downloaded songs
  downloads/           ← one subfolder per song: Artist - Title/
    Bruno Mars - Talking To The Moon/
      song.txt
      video.mp4
      cover.jpg
  logs/
    download.log       ← full debug log of all operations
  scripts/
    _common.py         ← shared USDB client + helpers (stdlib only)
    fetch_song.py      ← on-demand song downloader
    search.py          ← search local songs.db
    status.py          ← pipeline health report
    bulk_index.py      ← index USDB metadata in bulk
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `USDB login failed` | Check `usdb_username` / `usdb_password` in config.json |
| `No USDB results` | Try a shorter/different query; check USDB site is up |
| Video download fails | Run `yt-dlp "https://youtube.com/watch?v=ID"` manually to test |
| `Sign in to confirm you're not a bot` | Set `yt_dlp_cookies_from_browser` (e.g. `edge` or `chrome`) in config.json, then rerun `status.py --fix` |
| `Could not copy Chrome cookie database` | Close the browser and retry, or export cookies to a file and set `yt_dlp_cookies_file` |
| Streams not merged | Ensure `ffmpeg` is on PATH: `ffmpeg -version` |
| `TXT response didn't look like UltraStar` | Rate limit hit — wait 30s and retry |
| Song shows as `pending` in search | Run `fetch_song.py` to actually download it |

---

## Rate limits

USDB limits TXT downloads per session. The pipeline enforces a `rate_limit_seconds` 
delay between requests (default: 2s). If you hit limits, increase this in `config.json`.
