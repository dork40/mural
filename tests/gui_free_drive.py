"""Open Mural, look at the Free videos section, click one to download it, check it lands in the grid.
python3 tests/gui_free_drive.py <outdir>"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("GSK_RENDERER", "gl")

import gi  # noqa: E402
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from mural import catalog, gui  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/mural-free-gui")
OUT.mkdir(parents=True, exist_ok=True)
log = []


def shot(win, name):
    w, h = win.get_width(), win.get_height()
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(win).snapshot(snap, w, h)
    node = snap.to_node()
    if node is not None:
        win.get_renderer().render_texture(node, None).save_to_png(str(OUT / f"{name}.png"))


def free_cards(win):
    found = []

    def walk(w):
        if isinstance(w, gui.DownloadCard):
            found.append(w)
        c = w.get_first_child()
        while c is not None:
            walk(c)
            c = c.get_next_sibling()
    walk(win.content)
    return found


def steps(app):
    win = app.window
    win.set_default_size(1100, 900)
    yield 4000
    cards = free_cards(win)
    log.append("free cards: " + ", ".join(c.clip.name for c in cards))
    adj = win.content.get_parent().get_parent().get_vadjustment() if win.content.get_parent() else None
    if adj:
        adj.set_value(adj.get_upper())
    yield 2500
    shot(win, "free-section")
    target = next(c for c in cards if c.clip.file == "first-light.mp4")
    target.emit("clicked")
    yield 600
    log.append(f"downloading: bar visible={target.bar.get_visible()}")
    for _ in range(60):
        if not target.busy:
            break
        yield 500
    yield 1500
    log.append("first-light installed: " + str(catalog.installed(target.clip)))
    names = [c.wp.name for c in win.cards]
    log.append("now in grid: " + ", ".join(names))
    left = [c.clip.name for c in free_cards(win)]
    log.append("still offered: " + ", ".join(left))
    shot(win, "after-download")
    app.quit()


def main():
    app = gui.App()
    gen = None

    def advance():
        try:
            GLib.timeout_add(next(gen), lambda: advance() and False)
        except StopIteration:
            pass

    def start(a):
        nonlocal gen
        gen = steps(a)
        advance()
    app.connect("activate", lambda a: GLib.timeout_add(100, lambda: start(a) and False))
    app.run([sys.argv[0]])
    print("\n".join(log))


main()
