"""Thumbnails for videos and pictures, cached in ~/.cache/mural/thumbs.

Videos: one frame from about a tenth of the way in (the very first frame is often black), through a
throwaway GStreamer pipeline with no audio branch. Pictures: decoded at thumbnail size.
Everything here is safe to call from a worker thread; it returns a file path or None."""
import hashlib
import logging
import os
from pathlib import Path

from . import const

log = logging.getLogger(__name__)

WIDTH = 480
THUMB_DIR = const.CACHE_DIR / "thumbs"


def cache_path(path):
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return None
    key = hashlib.sha1(f"{p.resolve()}:{st.st_size}:{int(st.st_mtime)}:{WIDTH}".encode()).hexdigest()
    return THUMB_DIR / f"{key}.png"


def thumbnail(path, kind):
    out = cache_path(path)
    if out is None:
        return None
    if out.exists():
        return out
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    try:
        ok = _video(path, out) if kind == "video" else _image(path, out)
    except Exception as e:  # noqa: BLE001 - a broken file just gets no thumbnail
        log.info("no thumbnail for %s: %s", path, e)
        ok = False
    return out if ok else None


def _image(path, out):
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), WIDTH * 2, -1, True)
    except Exception:  # noqa: BLE001 - formats only glycin knows (AVIF, JXL...): go through GDK
        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk
        tex = Gdk.Texture.new_from_filename(str(path))
        tmp = out.with_suffix(".full.png")
        tex.save_to_png(str(tmp))
        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(tmp), WIDTH * 2, -1, True)
        tmp.unlink(missing_ok=True)
    pb.savev(str(out), "png", [], [])
    return True


def _video(path, out):
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    if not Gst.is_initialized():
        Gst.init(None)
    uri = Gst.filename_to_uri(os.path.abspath(str(path)))
    pipe = Gst.parse_launch(
        f'uridecodebin uri="{uri}" caps=video/x-raw(ANY) ! videoconvert ! videoscale ! '
        f"video/x-raw,format=RGB,width={WIDTH * 2},pixel-aspect-ratio=1/1 ! appsink name=sink sync=false")
    sink = pipe.get_by_name("sink")
    try:
        pipe.set_state(Gst.State.PAUSED)
        if pipe.get_state(10 * Gst.SECOND)[0] == Gst.StateChangeReturn.FAILURE:
            return False
        ok, dur = pipe.query_duration(Gst.Format.TIME)
        if ok and dur > 0:
            pipe.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, dur // 10)
            pipe.get_state(10 * Gst.SECOND)
        sample = sink.emit("pull-preroll")
        if sample is None:
            return False
        s = sample.get_caps().get_structure(0)
        w, h = s.get_value("width"), s.get_value("height")
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            return False
        try:
            data = bytes(info.data)
        finally:
            buf.unmap(info)
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf, GLib
        stride = len(data) // h
        pb = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(data), GdkPixbuf.Colorspace.RGB, False, 8, w, h, stride)
        pb.savev(str(out), "png", [], [])
        return True
    finally:
        pipe.set_state(Gst.State.NULL)
