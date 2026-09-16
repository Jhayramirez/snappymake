#!/usr/bin/env bash
# Package the SnappyMake IMAP extension into a distributable ZIP for AdsPower.
# The manifest.json is placed at the ROOT of the archive (AdsPower requires
# manifest.json at the top level of the extracted extension).
set -euo pipefail

# Always operate from the extension folder (this script's directory).
cd "$(dirname "$0")"

DIST_DIR="dist"
OUT="$DIST_DIR/snappymake-imap-extension.zip"

# Files/folders that make up the extension (paths are relative to this folder,
# so they land at the ZIP root — no nested wrapper directory).
ASSETS=(
  "manifest.json"
  "popup.html"
  "popup.js"
  "popup.css"
  "background.js"
  "icons"
  "README.md"
)

mkdir -p "$DIST_DIR"
rm -f "$OUT"

# -r recurse into icons/, -X strip extra file attributes for reproducibility.
zip -r -X "$OUT" "${ASSETS[@]}" >/dev/null

echo "Built $OUT"
echo "Top-level entries:"
unzip -l "$OUT"
