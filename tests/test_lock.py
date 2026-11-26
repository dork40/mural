"""The hyprlock lock-screen integration: all plain-text config editing, no GPU or hyprlock needed."""
import struct
import zlib

import pytest

from mural import lock

HYPRLOCK = """\
general {
    hide_cursor = true
}

background {
    monitor =
    path = ~/.cache/current-wallpaper
    blur_passes = 3
    blur_size = 8
    color = rgb(000000)
}

input-field {
    monitor =
    size = 320, 56
}
"""


def test_reads_the_current_background_path():
    assert lock.background_path(HYPRLOCK) == "~/.cache/current-wallpaper"


def test_set_background_only_touches_the_path_line():
    out = lock.set_background(HYPRLOCK, "/home/me/.cache/mural/lock.png")
    assert lock.background_path(out) == "/home/me/.cache/mural/lock.png"
    # everything else in the block is left alone
    assert "blur_passes = 3" in out and "blur_size = 8" in out and "color = rgb(000000)" in out
    assert "size = 320, 56" in out                      # the input-field block is untouched
    assert out.count("path =") == 1                     # no duplicate path line


def test_set_background_writes_a_clean_path_no_trailing_comment():
    # hyprlock does not strip an inline `# ...`, so the path line must be only the path
    out = lock.set_background(HYPRLOCK, "/home/me/.cache/mural/lock.png")
    line = next(ln for ln in out.splitlines() if ln.strip().startswith("path ="))
    assert line.strip() == "path = /home/me/.cache/mural/lock.png"
    assert "#" not in line


def test_set_background_is_idempotent():
    once = lock.set_background(HYPRLOCK, str(lock.LOCK_IMAGE))
    twice = lock.set_background(once, str(lock.LOCK_IMAGE))
    assert once == twice and lock.is_managed(once)


def test_is_managed_only_when_pointed_at_the_lock_image():
    assert not lock.is_managed(HYPRLOCK)
    assert lock.is_managed(lock.set_background(HYPRLOCK, str(lock.LOCK_IMAGE)))


def test_set_background_inserts_a_path_when_the_block_has_none():
    conf = "background {\n    monitor =\n    blur_passes = 2\n}\n"
    out = lock.set_background(conf, "/x/lock.png")
    assert lock.background_path(out) == "/x/lock.png" and "blur_passes = 2" in out


def test_set_background_without_a_block_is_an_error():
    with pytest.raises(RuntimeError):
        lock.set_background("general {\n    hide_cursor = true\n}\n", "/x/lock.png")


def _png(path, w, h):
    raw = (b"\x00" + b"\xff\x00\x00" * w) * h
    chunk = lambda t, d: (struct.pack(">I", len(d)) + t + d
                          + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_save_lock_image_picks_the_largest_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(lock.const, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(lock, "LOCK_IMAGE", tmp_path / "lock.png")
    _png(tmp_path / "snapshot-eDP-1.png", 1920, 1200)
    _png(tmp_path / "snapshot-HDMI-A-1.png", 800, 600)
    assert lock.save_lock_image() is True
    assert lock._png_area(tmp_path / "lock.png") == 1920 * 1200


def test_save_lock_image_without_snapshots_returns_false(tmp_path, monkeypatch):
    monkeypatch.setattr(lock.const, "CACHE_DIR", tmp_path)
    assert lock.save_lock_image() is False
