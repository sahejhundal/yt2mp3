# yt2mp3

A simple Python script to download YouTube videos and playlists as high-quality MP3 files.

## Features

- Download single videos or entire playlists
- **Download an artist's full discography** (albums, singles, EPs, or all)
- Automatic conversion to 320kbps MP3
- Skip videos that fail (copyright claims, unavailable videos)
- Track downloaded videos to prevent duplicates
- Interactive URL input or command-line argument support

## Requirements

- Python 3.6+
- FFmpeg (for audio conversion)
- **Deno** (JavaScript runtime) — required so yt-dlp can solve YouTube's
  signature / n-sig challenges. Without it, some tracks return only storyboard
  images ("Only images are available for download"). Install with
  `brew install deno` (macOS) and make sure `deno` is on your `PATH`.
  Also install the solver scripts: `pip install -U --pre "yt-dlp[default]"`.

## Installation

1. Clone this repository:
```bash
git clone https://github.com/yourusername/yt2mp3.git
cd yt2mp3
```

2. Create a virtual environment (recommended):
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install yt-dlp
```

4. Install FFmpeg:
   - **macOS**: `brew install ffmpeg`
   - **Ubuntu/Debian**: `sudo apt install ffmpeg`
   - **Windows**: Download from [ffmpeg.org](https://ffmpeg.org/download.html)

## Usage

### Interactive Mode
Simply run the script and paste your URL when prompted:
```bash
python yt2mp3.py
```

### Command-line Argument
Pass the URL as an argument (remember to use quotes for URLs with special characters):
```bash
python yt2mp3.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

### Force Re-download
If a video is already in `downloads/.downloaded.txt`, you can override skip behavior:
```bash
python yt2mp3.py --force "https://www.youtube.com/watch?v=VIDEO_ID"
```
Short flag:
```bash
python yt2mp3.py -f "https://www.youtube.com/watch?v=VIDEO_ID"
```

### Downloading Playlists
The script automatically handles playlists:
```bash
python yt2mp3.py "https://www.youtube.com/playlist?list=PLAYLIST_ID"
```

### Artist Discography
Download an artist's full discography from YouTube Music:
```bash
python yt2mp3.py --artist "Kendrick Lamar"
```

Filter by release type — `albums`, `singles`, `eps`, or `all` (default):
```bash
python yt2mp3.py --artist "Frank Ocean" --category albums
python yt2mp3.py -a "SZA" -c singles
python yt2mp3.py -a "Tyler, The Creator" -c eps
```

Combine with `--force` to re-download everything:
```bash
python yt2mp3.py -a "The Weeknd" -c all -f
```

Files are organized as `downloads/Artist/Release Name/01 - Track.mp3`.

### Complete Catalogue (robust — never misses a track)

YouTube Music's artist pages sometimes fail to parse (ytmusicapi raises
`musicImmersiveHeaderRenderer` / `musicCarouselShelfRenderer` errors), which
makes the discography mode silently skip singles — or an entire artist. The
`--complete` mode bypasses ytmusicapi entirely and enumerates each channel's
auto-generated **"Uploads from …"** playlist with yt-dlp, so every single,
album track and video is captured even if it was just released.

```bash
python yt2mp3.py --complete \
  "https://music.youtube.com/channel/UCxxxx" \
  "https://music.youtube.com/channel/UCyyyy" \
  --override-artist "Artist Name"
```

- Pass **multiple channel URLs** to merge aliases of one artist; tracks are
  de-duplicated globally by title (use `--keep-duplicates` to keep every upload).
- Files land flat in `downloads/<Artist Name>/`.

#### One-shot: download + clean + dedupe

Add `--clean` and/or `--dedupe` to run the post-processing automatically on the
output folder right after downloading:

```bash
python yt2mp3.py --complete "https://music.youtube.com/channel/UCxxxx" \
  --override-artist "Artist Name" --clean --dedupe
```

- `--clean` → runs `clean_mp3_metadata.py` (normalise titles, set artist, rename).
- `--dedupe` → runs `dedupe_audio.py` and **quarantines** acoustic duplicates
  (reversible). Add `--dedupe-delete` to delete them instead.
- Auto-dedupe only scans the freshly downloaded folder. To catch duplicates
  **across** folders/artists, run `dedupe_audio.py` manually with several paths.

### Age-restricted tracks (cookies)

Some tracks require a logged-in account. Pass cookies with `--cookies`, which
accepts **either** a Netscape `cookies.txt` **or** a browser-extension JSON
export (auto-converted):

```bash
python yt2mp3.py --complete "https://music.youtube.com/channel/UCxxxx" \
  --override-artist "Artist Name" --cookies cookies.json
```

Each concurrent worker gets a private copy of the cookies so the file is never
corrupted by yt-dlp's token refresh. `cookies.txt`/`cookies.json` are gitignored.

## De-duplicating by audio (dedupe_audio.py)

Metadata lies — the same recording can show up under different titles, artists
or folders. `dedupe_audio.py` matches tracks by their **acoustic fingerprint**
(Chromaprint / AcoustID tech), so it finds true duplicates regardless of tags,
bitrate or filename, while keeping remixes and "slowed + reverb" edits separate.
It runs fully offline, so it also works on unreleased/leaked tracks.

Install the fingerprinter once:

```bash
brew install chromaprint        # provides `fpcalc`
```

Usage:

```bash
# Dry-run report across one or more folders (nothing is changed)
python dedupe_audio.py "downloads/Nine Vicious" "downloads/Nine Vicious Vault"

# Quarantine duplicates into <root>/_duplicates (safe; reversible)
python dedupe_audio.py "downloads" --apply

# Actually delete duplicates
python dedupe_audio.py "downloads" --apply --delete

# Prefer which copy survives, and tune strictness
python dedupe_audio.py "downloads/Nine Vicious" "downloads/Nine Vicious Vault" \
  --prefer "Nine Vicious/,Vault" --threshold 0.92
```

How it decides: two files are the same recording when their fingerprints match
above `--threshold` (default `0.90`). In practice real duplicates score
93–99%+ while different songs stay below ~75%, leaving a wide safe margin. The
kept copy is chosen by `--prefer` folder order, then highest bitrate, then
largest file. Fingerprints are cached in `.fingerprints.json` for fast re-runs.

## Output

Downloaded files are saved to the `downloads/` folder in the same directory as the script. Files are named using the video title.

A hidden file `.downloaded.txt` is created in the downloads folder to track previously downloaded videos and prevent duplicates.

## How It Works

- Downloads the best available audio quality from YouTube
- Converts to MP3 format at 320kbps
- Skips videos that are unavailable or blocked by copyright
- Maintains a download archive to avoid re-downloading

## License

MIT

## Disclaimer

This tool is for personal use only. Respect copyright laws and YouTube's Terms of Service. Only download content you have permission to download.
