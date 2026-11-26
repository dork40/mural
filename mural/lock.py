"""Mural on the Hyprland lock screen (hyprlock).

hyprlock can only draw a still image as its background, so Mural keeps a snapshot of the current
wallpaper at a fixed path (``~/.cache/mural/lock.png``) and points hyprlock's ``background`` at it.
The daemon refreshes that snapshot whenever the wallpaper changes, so the lock screen keeps up.

Editing hyprlock.conf is reversible: the previous ``path`` value is saved before the first change
and put back on restore, and a ``.mural.bak`` copy is written next to the file each time. The GLSL
rendering lives in the daemon; everything here is plain text/file work so it can be tested without a
GPU, hyprlock or a compositor."""
import re
import shutil
import struct
from pathlib import Path

from . import const

LOCK_IMAGE = const.CACHE_DIR / "lock.png"
CONFIG_PATH = Path.home() / ".config" / "hypr" / "hyprlock.conf"
PREV_PATH = const.STATE_DIR / "hyprlock-previous-path"
MARK = "  # Mural"


# -- what's on this machine ------------------------------------------------------------------
def hyprlock_installed():
    return shutil.which("hyprlock") is not None


def config_path():
    """hyprlock's config, or None if there isn't one yet."""
    return CONFIG_PATH if CONFIG_PATH.exists() else None


# -- editing the config ----------------------------------------------------------------------
def _first_background_block(lines):
    """(start, end) line indices of the first ``background { ... }`` block, or None."""
    for i, line in enumerate(lines):
        if re.match(r"\s*background\s*\{", line):
            depth = line.count("{") - line.count("}")
            j = i
            while depth > 0 and j + 1 < len(lines):
                j += 1
                depth += lines[j].count("{") - lines[j].count("}")
            return i, j
    return None


def _path_line(lines, block):
    for k in range(block[0], block[1] + 1):
        if re.match(r"\s*path\s*=", lines[k]):
            return k
    return None


def background_path(text):
    """The current ``path =`` value in the first background block (``#`` comment stripped), or None."""
    lines = text.splitlines()
    block = _first_background_block(lines)
    if not block:
        return None
    k = _path_line(lines, block)
    if k is None:
        return None
    value = lines[k].split("=", 1)[1]
    return value.split("#", 1)[0].strip() or None


def set_background(text, image):
    """Return `text` with the first background block's ``path`` pointed at `image` (idempotent)."""
    image = str(image)
    lines = text.splitlines()
    block = _first_background_block(lines)
    if not block:
        raise RuntimeError("hyprlock.conf has no `background { }` block to put a wallpaper in")
    keep_nl = text.endswith("\n")
    k = _path_line(lines, block)
    if k is not None:
        indent = re.match(r"(\s*)", lines[k]).group(1)
        lines[k] = f"{indent}path = {image}{MARK}"
    else:
        indent = re.match(r"(\s*)", lines[block[0]]).group(1) + "    "
        lines.insert(block[0] + 1, f"{indent}path = {image}{MARK}")
    return "\n".join(lines) + ("\n" if keep_nl else "")


def is_managed(text):
    """True when hyprlock's background already points at Mural's lock image."""
    return background_path(text) == str(LOCK_IMAGE)


# -- the lock image --------------------------------------------------------------------------
def _png_area(path):
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            return 0
        w, h = struct.unpack(">II", head[16:24])
        return w * h
    except (OSError, struct.error):
        return 0


def snapshot_files():
    return list(const.CACHE_DIR.glob("snapshot-*.png"))


def save_lock_image():
    """Copy the daemon's best current snapshot to LOCK_IMAGE. Returns True if one was written."""
    snaps = snapshot_files()
    if not snaps:
        return False
    best = max(snaps, key=lambda p: (_png_area(p), p.stat().st_mtime))
    const.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(best, LOCK_IMAGE)
    return True


# -- state -----------------------------------------------------------------------------------
def current_state():
    """{"installed", "config", "managed", "image"} for the lock screen."""
    conf = config_path()
    text = conf.read_text() if conf else ""
    return {"installed": hyprlock_installed(), "config": conf is not None,
            "managed": bool(text) and is_managed(text), "image": LOCK_IMAGE.exists()}


# -- enabling / disabling (the file writes) --------------------------------------------------
def enable():
    """Point hyprlock at Mural's lock image, remembering what it pointed at before. The caller
    refreshes the image first (save_lock_image) so there's something to show."""
    conf = config_path()
    if conf is None:
        raise RuntimeError("hyprlock has no config at ~/.config/hypr/hyprlock.conf yet")
    text = conf.read_text()
    if not is_managed(text):
        PREV_PATH.parent.mkdir(parents=True, exist_ok=True)
        PREV_PATH.write_text(background_path(text) or "")
    shutil.copyfile(conf, conf.with_suffix(conf.suffix + ".mural.bak"))
    conf.write_text(set_background(text, LOCK_IMAGE))


def disable():
    """Put hyprlock's background path back to whatever it was before Mural touched it."""
    conf = config_path()
    if conf is None:
        return
    text = conf.read_text()
    if not is_managed(text):
        return
    prev = PREV_PATH.read_text().strip() if PREV_PATH.exists() else ""
    shutil.copyfile(conf, conf.with_suffix(conf.suffix + ".mural.bak"))
    if prev:
        conf.write_text(set_background(text, prev).replace(f"path = {prev}{MARK}", f"path = {prev}"))
    else:  # nothing was there before: drop the managed line
        lines = [ln for ln in text.splitlines() if not ln.strip().endswith(MARK.strip())]
        conf.write_text("\n".join(lines) + ("\n" if text.endswith("\n") else ""))
    PREV_PATH.unlink(missing_ok=True)
