#!/usr/bin/env bash
# Show each built-in shader for a moment and save a snapshot of it: tests/shots/<name>.png
# Needs a running graphical session (it uses the real daemon). Restores your wallpaper after.
set -euo pipefail
cd "$(dirname "$0")/.."
M="python3 -m mural"
out=tests/shots; mkdir -p "$out"
cfg="${XDG_CONFIG_HOME:-$HOME/.config}/mural/config.json"
backup=""; if [ -f "$cfg" ]; then backup=$(mktemp); cp "$cfg" "$backup"; fi
for s in ${*:-aurora starfield gradient liquid nebula}; do
  $M stop >/dev/null 2>&1 || true
  $M set "$s" >/dev/null
  sleep "${WAIT:-5}"
  $M snapshot
  sleep 1
  cp "${XDG_CACHE_HOME:-$HOME/.cache}"/mural/snapshot-*.png "$out/$s.png" 2>/dev/null && echo "  $out/$s.png"
done
if [ -n "$backup" ]; then cp "$backup" "$cfg"; rm -f "$backup"; else rm -f "$cfg"; fi
$M stop >/dev/null 2>&1 || true
