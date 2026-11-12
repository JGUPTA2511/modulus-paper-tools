#!/bin/bash
# Shared Mac runner. Cropping stays in Claude's unchanged question_boundaries.py.
# Updates download code only. Private PDFs are never sent to the public tool repo.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
  if [ -x "$b" ]; then eval "$("$b" shellenv)"; break; fi
done
if ! command -v brew >/dev/null 2>&1; then
  echo 'Homebrew is missing. Use the existing Claude setup first; no repository changes made.'
  exit 1
fi
# Only missing helper programs are installed; no brew upgrade.
for pkg in poppler tesseract python gh; do
  case "$pkg" in poppler) exe=pdftoppm;; python) exe=python3;; *) exe="$pkg";; esac
  if ! command -v "$exe" >/dev/null 2>&1; then brew install "$pkg"; fi
done
if [ ! -d "$HERE/.venv" ]; then python3 -m venv "$HERE/.venv"; fi
# shellcheck disable=SC1091
source "$HERE/.venv/bin/activate"
if ! python3 -c 'import PIL,numpy,cv2,pytesseract,pdfplumber' 2>/dev/null; then
  python3 -m pip install --disable-pip-version-check pillow numpy opencv-python-headless pytesseract pdfplumber
fi
BASE='https://raw.githubusercontent.com/JGUPTA2511/modulus-paper-tools/main'
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
if curl -fsSL --max-time 30 "$BASE/workspace_client.py" -o "$TMP" && python3 -m py_compile "$TMP"; then
  if ! cmp -s "$TMP" "$HERE/workspace_client.py"; then
    [ ! -f "$HERE/workspace_client.py" ] || cp "$HERE/workspace_client.py" "$HERE/workspace_client.py.previous"
    mv "$TMP" "$HERE/workspace_client.py"
  fi
elif [ ! -f "$HERE/workspace_client.py" ]; then
  echo 'Could not download workspace_client.py. Your original PDFs have not been touched.'
  exit 1
else
  echo 'Using the installed workspace client; update check unavailable.'
fi
if ! gh auth status --hostname github.com >/dev/null 2>&1; then
  echo 'ONE-TIME CONNECTION: sign in to your GitHub account in the browser.'
  echo 'Do not paste passwords or access tokens into a chat.'
  gh auth login --hostname github.com --web --git-protocol https
fi
python3 "$HERE/workspace_client.py" --interactive
