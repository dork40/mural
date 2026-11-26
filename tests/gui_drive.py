"""Drive Mural's window like a person would and save screenshots: python3 tests/gui_drive.py <outdir>"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("GSK_RENDERER", "gl")

import gi  # noqa: E402
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from mural import cli, gui, library  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/mural-gui")
OUT.mkdir(parents=True, exist_ok=True)
TESTDATA = Path(__file__).resolve().parent.parent / "testdata"
log = []


def shot(win, name):
    w, h = win.get_width(), win.get_height()
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(win).snapshot(snap, w, h)
    node = snap.to_node()
    if node is not None:
        win.get_renderer().render_texture(node, None).save_to_png(str(OUT / f"{name}.png"))
        log.append(f"shot {name}")


def steps(app):
    win = app.window
    yield 4000
    shot(win, "1-open")
    log.append("status: " + win.status_label.get_label())
    win.add_files([str(TESTDATA / "nasa-earth-ocean.mp4"), str(TESTDATA / "test-image.jpg"), "/etc/hostname"])
    yield 5000
    shot(win, "2-added")
    log.append("cards: " + ", ".join(f"{c.wp.name}({c.wp.kind})" for c in win.cards))
    pic = next(c for c in win.cards if c.wp.kind == "image")
    pic.emit("clicked")
    yield 3000
    log.append("after picture click: " + win.status_label.get_label())
    win.toggle_pause()
    yield 2000
    log.append("after pause: " + win.status_label.get_label() + " | button " + win.pause_btn.get_icon_name())
    shot(win, "3-paused")
    win.toggle_pause()
    yield 1500
    log.append("after play: " + win.status_label.get_label())
    aurora = next(c for c in win.cards if c.wp.ref == "shader:aurora")
    aurora.emit("clicked")
    yield 2500
    log.append("after aurora: " + win.status_label.get_label())
    current = [c.wp.name for c in win.cards if c.has_css_class("current")]
    log.append("highlighted: " + ", ".join(current))
    # clean up what we added
    for c in list(win.cards):
        if not c.wp.builtin and c.wp.path.parent == gui.const.USER_WALLPAPERS:
            app.activate_action("remove", GLib.Variant("s", str(c.wp.path)))
    yield 1500
    log.append("after remove: " + ", ".join(c.wp.name for c in win.cards))
    shot(win, "4-final")
    app.quit()


def main():
    app = gui.App()
    app.connect("activate", lambda a: GLib.timeout_add(100, lambda: start(a) and False))
    gen = None

    def start(a):
        nonlocal gen
        gen = steps(a)
        advance()

    def advance():
        try:
            GLib.timeout_add(next(gen), lambda: advance() and False)
        except StopIteration:
            pass
    app.run([sys.argv[0]])
    print("\n".join(log))
    print("library now:", [w.name for w in library.list_all() if not w.builtin])
    print("daemon running:", cli.daemon_running())


main()
