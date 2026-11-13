#!/bin/bash
# Shared Mac runner. Existing Claude cropper and installed packages are reused.
# Authentication is optional and never gates local paper processing.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
  if [ -x "$b" ]; then eval "$("$b" shellenv)"; break; fi
done
install_missing() {
  if ! command -v brew >/dev/null 2>&1; then
    echo "Missing local helper: $1. Existing papers/results have not been changed."
    exit 1
  fi
  echo "Installing missing local helper: $1"
  brew install "$1"
}
# GitHub CLI is optional; it is installed only when the owner selects Connect.
for pkg in poppler tesseract; do
  case "$pkg" in poppler) exe=pdftoppm;; *) exe="$pkg";; esac
  if ! command -v "$exe" >/dev/null 2>&1; then install_missing "$pkg"; fi
done
# Prefer this tool's already-installed environment, even if system Python changes.
if [ -x "$HERE/.venv/bin/python3" ]; then
  MODULUS_PYTHON="$HERE/.venv/bin/python3"
else
  if ! command -v python3 >/dev/null 2>&1; then install_missing python; fi
  MODULUS_PYTHON="$(command -v python3)"
  # Reuse a working existing Python before creating/installing another environment.
  if ! "$MODULUS_PYTHON" -c 'import PIL,numpy,cv2,pytesseract,pdfplumber' 2>/dev/null; then
    "$MODULUS_PYTHON" -m venv "$HERE/.venv"
    MODULUS_PYTHON="$HERE/.venv/bin/python3"
  fi
fi
if ! "$MODULUS_PYTHON" -c 'import PIL,numpy,cv2,pytesseract,pdfplumber' 2>/dev/null; then
  echo 'Installing missing local Python packages (first setup only)...'
  "$MODULUS_PYTHON" -m pip install --quiet --disable-pip-version-check pillow numpy opencv-python-headless pytesseract pdfplumber
fi
BASE='https://raw.githubusercontent.com/JGUPTA2511/modulus-paper-tools/main'
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
if curl -fsSL --max-time 15 "$BASE/workspace_client.py" -o "$TMP" && "$MODULUS_PYTHON" -m py_compile "$TMP"; then
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
# No gh install, auth status, login or Git credential prompts at startup.
# The client offers an explicit Connect action; paper work starts immediately.
"$MODULUS_PYTHON" "$HERE/workspace_client.py" --interactive
