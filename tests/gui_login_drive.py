"""Open Mural, switch to the Login screen tab, try each background in the preview, screenshot it.
Doesn't press "Use" (that needs your password): python3 tests/gui_login_drive.py <outdir>"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("GSK_RENDERER", "gl")

import gi  # noqa: E402
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from mural import gui, login  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/mural-login-gui")
OUT.mkdir(parents=True, exist_ok=True)
log = []


def shot(win, name):
    w, h = win.get_width(), win.get_height()
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(win).snapshot(snap, w, h)
    node = snap.to_node()
    if node is not None:
        win.get_renderer().render_texture(node, None).save_to_png(str(OUT / f"{name}.png"))


def steps(app):
    win = app.window
    yield 2500
    win.stack.set_visible_child_name("login")
    yield 2500
    page = win.login_page
    log.append("now: " + page.now_label.get_label().replace("\n", " / "))
    log.append("header buttons hidden: " + str([w.get_visible() for w in win._wall_only]))
    log.append("choices: " + ", ".join(f"{w.name}({w.kind})" for w in page.walls))
    shot(win, "login-tab")
    log.append("mode loaded: " + page._mode() + "  rates: " + str(page._rates()))
    for sel, rate in ((1, 0), (2, 2), (3, 0), (0, 0)):
        page.res_row.set_selected(sel)
        page.rate_row.set_selected(rate)
        yield 200
        log.append(f"res {sel} rate {rate} -> {page._mode()}  (rate row shown={page.rate_row.get_visible()})")
    page._load_mode(login.login_display())
    yield 300
    adj = page.get_first_child().get_vadjustment() if hasattr(page.get_first_child(), "get_vadjustment") else None
    shot(win, "login-mode")
    for i, w in enumerate(page.walls):
        page.choice.set_selected(i)
        yield 1800
        log.append(f"{w.name}: preview={type(page.preview_view).__name__} quality shown={page.quality.get_visible()}")
        shot(win, f"login-{i}")
    theme = login.build_theme(page.walls[0], win.palette, "balanced", dest="/tmp/mural-login-built")
    log.append("built: " + ", ".join(sorted(p.name for p in theme.iterdir())))
    log.append("command: " + " ".join(login.apply_command(theme, page._mode())))
    win.stack.set_visible_child_name("wallpaper")
    yield 800
    log.append("back on wallpaper tab, header buttons: " + str([w.get_visible() for w in win._wall_only]))
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
