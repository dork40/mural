#!/usr/bin/env bash
# Preview built login themes in SDDM's test mode and screenshot each: login-shots.sh <theme-dir>...
out=${OUT:-/tmp/mlt-shots}; mkdir -p "$out"
for d in "$@"; do
    sddm-greeter-qt6 --test-mode --theme "$d" > "$out/$(basename "$d").log" 2>&1 &
    pid=$!
    sleep 6
    grim -o eDP-1 "$out/$(basename "$d").png"
    kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
    echo "$out/$(basename "$d").png"
done
