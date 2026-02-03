#!/bin/bash
cd "$(dirname "$0")"

clear
echo "Activating virtual environment..."
source .venv/bin/activate

echo
echo "Paste a YouTube URL and press Enter:"
echo

python yt2mp3.py

deactivate

echo
echo "Done. Press Enter to close."
read
