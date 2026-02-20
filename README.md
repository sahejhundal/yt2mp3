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
