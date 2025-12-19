# yt2mp3

A simple Python script to download YouTube videos and playlists as high-quality MP3 files.

## Features

- Download single videos or entire playlists
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

### Downloading Playlists
The script automatically handles playlists:
```bash
python yt2mp3.py "https://www.youtube.com/playlist?list=PLAYLIST_ID"
```

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
