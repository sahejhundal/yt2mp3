#!/bin/bash
cd "$(dirname "$0")"

clear
echo "Activating virtual environment..."
source .venv/bin/activate

# Use certifi's CA bundle when available to avoid macOS/work-network SSL trust issues.
CERT_PATH="$(python -c 'import certifi; print(certifi.where())' 2>/dev/null)"
if [ -n "$CERT_PATH" ]; then
  export SSL_CERT_FILE="$CERT_PATH"
  echo "Using SSL cert bundle: $SSL_CERT_FILE"
else
  echo "certifi not found in venv; continuing with system certificates."
fi

echo
echo "Paste a YouTube URL and press Enter:"
echo

python yt2mp3.py

deactivate

echo
echo "Done. Press Enter to close."
read
