"""X11: turn a GTK window into a desktop-type window on one monitor (below everything, on all
workspaces, out of taskbars). Uses python-xlib; only imported on X11."""
import logging

import gi

gi.require_version("GdkX11", "4.0")
from gi.repository import GdkX11  # noqa: E402,F401

log = logging.getLogger(__name__)


def make_desktop_window(window, geo):
    try:
        from Xlib import Xatom, display
    except ImportError:
        log.error("python-xlib is needed on X11 (package python-xlib / python3-xlib)")
        return False
    surface = window.get_surface()
    xid = surface.get_xid()
    d = display.Display()
    try:
        w = d.create_resource_object("window", xid)
        atom = d.intern_atom
        w.change_property(atom("_NET_WM_WINDOW_TYPE"), Xatom.ATOM, 32, [atom("_NET_WM_WINDOW_TYPE_DESKTOP")])
        w.change_property(atom("_NET_WM_STATE"), Xatom.ATOM, 32,
                          [atom(a) for a in ("_NET_WM_STATE_BELOW", "_NET_WM_STATE_STICKY",
                                             "_NET_WM_STATE_SKIP_TASKBAR", "_NET_WM_STATE_SKIP_PAGER")])
        w.change_property(atom("_NET_WM_DESKTOP"), Xatom.CARDINAL, 32, [0xFFFFFFFF])   # every workspace
        w.configure(x=geo.x, y=geo.y, width=geo.width, height=geo.height)
        d.flush()
        return True
    finally:
        d.close()
