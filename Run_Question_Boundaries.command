#!/bin/bash
# Stable starter. It fetches the newest tool files, then runs them.
# You should never need to replace this file; updates arrive automatically.
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE" || exit 1
BASE="$(head -n1 "$HERE/update_source.txt" 2>/dev/null | tr -d '[:space:]')"

fetch() {  # fetch <file> <kind>   (only replaces the local copy if the download is complete and valid)
  [ -z "$BASE" ] && return 1
  local tmp; tmp="$(mktemp)"
  if curl -fsSL --max-time 10 "$BASE/$1" -o "$tmp" && [ -s "$tmp" ]; then
    if [ "$2" = py ] && ! python3 -m py_compile "$tmp" 2>/dev/null; then rm -f "$tmp"; return 1; fi
    if [ "$2" = sh ] && ! bash -n "$tmp" 2>/dev/null; then rm -f "$tmp"; return 1; fi
    if ! cmp -s "$tmp" "$HERE/$1"; then
      [ -f "$HERE/$1" ] && cp "$HERE/$1" "$HERE/$1.previous"
      mv "$tmp" "$HERE/$1"; echo "  updated: $1"
    else rm -f "$tmp"; fi
    return 0
  fi
  rm -f "$tmp"; return 1
}

echo "Checking for updates..."
if fetch question_boundaries.py py && fetch run_core.sh sh; then
  echo "  tool is up to date."
else
  echo "  (could not check for updates - using the copy already on this Mac)"
fi
exec bash "$HERE/run_core.sh"
