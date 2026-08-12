#!/usr/bin/env bash
# Gmail Organizer installer — installs Python deps and copies the script into
# ~/.local/bin. Google Cloud setup is manual (see README, ~3 minutes).
set -euo pipefail

BIN_DIR="${HOME}/.local/bin"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/gmail-organizer.py"
DEST="${BIN_DIR}/gmail-organizer.py"

echo "==> installing Python dependencies"
python3 -m pip install --user google-api-python-client google-auth-oauthlib google-auth pyyaml

echo "==> installing script"
mkdir -p "$BIN_DIR"
cp "$SRC" "$DEST"
chmod +x "$DEST"
echo "✅ installed: $DEST"

echo
echo "Next: enable the Gmail API (see README), then first run:"
echo "  python3 ${DEST} --dry-run"
