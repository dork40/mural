"""One borderless window per screen, kept behind everything else.

Backends:
  layer  - wlr-layer-shell BACKGROUND layer (Hyprland, Sway, river, niri, KDE Plasma, Wayfire, labwc...)
  x11    - a _NET_WM_WINDOW_TYPE_DESKTOP window per monitor (i3, bspwm, XFCE, Openbox, ...)
  gnome  - handled by the Mural GNOME Shell extension (GNOME has no layer-shell)
"""
import ctypes.util
import glob
import logging
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

log = logging.getLogger(__name__)

LAYER_SHELL_LIB = "libgtk4-layer-shell.so.0"
NAMESPACE = "mural"


def layer_shell_path():
    """Full path of libgtk4-layer-shell, for LD_PRELOAD (it must load before libwayland)."""
    for d in ("/app/lib", "/usr/lib", "/usr/lib64", "/usr/local/lib", "/usr/lib/x86_64-linux-gnu",
              "/usr/lib/aarch64-linux-gnu"):
        p = os.path.join(d, LAYER_SHELL_LIB)
        if os.path.exists(p):
            return p
    hits = glob.glob("/usr/lib*/**/" + LAYER_SHELL_LIB, recursive=True)
    if hits:
        return hits[0]
    return LAYER_SHELL_LIB if ctypes.util.find_library("gtk4-layer-shell") else None


def layer_shell_preloaded():
    return "gtk4-layer-shell" in os.environ.get("LD_PRELOAD", "")


def display_kind(display=None):
    display = display or Gdk.Display.get_default()
    name = type(display).__name__
    if "Wayland" in name:
        return "wayland"
    if "X11" in name:
        return "x11"
    return name


def wants_bottom_layer():
    """KDE Plasma draws its own wallpaper in the background layer, above ours, so we sit one layer up
    (still under windows and panels). MURAL_LAYER=background|bottom overrides."""
    forced = os.environ.get("MURAL_LAYER", "").lower()
    if forced in ("background", "bottom"):
        return forced == "bottom"
    return "kde" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower().split(":")


def is_gnome():
    desktops = os.environ.get("XDG_CURRENT_DESKTOP", "").lower().split(":")
    return "gnome" in desktops or os.environ.get("DESKTOP_SESSION", "").lower().startswith("gnome")


GNOME_EXTENSION = "mural@dork40.github.io"


def gnome_extension_enabled():
    """True if GNOME Shell reports the Mural extension as enabled (state 1 = ENABLED)."""
    try:
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        res = bus.call_sync("org.gnome.Shell.Extensions", "/org/gnome/Shell/Extensions",
                            "org.gnome.Shell.Extensions", "GetExtensionInfo",
                            GLib.Variant("(s)", (GNOME_EXTENSION,)), None, Gio.DBusCallFlags.NONE, 2000, None)
        info = res.unpack()[0]
        return float(info.get("state", 0)) == 1.0
    except Exception:  # noqa: BLE001 - no shell, no extension service: treat as not enabled
        return False


def pick_backend(display=None):
    forced = os.environ.get("MURAL_BACKEND")
    if forced:
        return forced
    kind = display_kind(display)
    if kind == "x11":
        return "x11"
    if kind == "wayland":
        try:
            gi.require_version("Gtk4LayerShell", "1.0")
            from gi.repository import Gtk4LayerShell as LayerShell
            if LayerShell.is_supported():
                return "layer"
        except (ValueError, ImportError):
            pass
        if is_gnome():
            return "gnome"
    return "unsupported"


class WallpaperWindow(Gtk.Window):
    def __init__(self, app, monitor, backend):
        super().__init__(application=app, decorated=False, title="Mural wallpaper",
                         focusable=False, can_focus=False)
        self.monitor = monitor
        self.backend = backend
        self.connector = monitor.get_connector() or "unknown"
        self.view = None
        self.add_css_class("mural-wallpaper")
        self.set_cursor(None)
        if backend == "layer":
            gi.require_version("Gtk4LayerShell", "1.0")
            from gi.repository import Gtk4LayerShell as LayerShell
            LayerShell.init_for_window(self)
            LayerShell.set_namespace(self, NAMESPACE)
            LayerShell.set_layer(self, LayerShell.Layer.BOTTOM if wants_bottom_layer() else LayerShell.Layer.BACKGROUND)
            LayerShell.set_monitor(self, monitor)
            for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM, LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
                LayerShell.set_anchor(self, edge, True)
            LayerShell.set_exclusive_zone(self, -1)          # cover the whole screen, ignore panels
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
        elif backend in ("x11", "gnome"):
            geo = monitor.get_geometry()
            self.set_default_size(geo.width, geo.height)
            if backend == "gnome":                  # the extension finds us by this title
                self.set_title(f"@mural!{geo.x},{geo.y},{geo.width},{geo.height}")
            if backend == "x11":
                self.connect("realize", self._x11_setup)
        self.connect("realize", self._pass_input_through)

    def _pass_input_through(self, *_):
        """Clicks go to whatever is behind/below (desktop icons, the compositor), never to us."""
        surface = self.get_surface()
        if surface is not None:
            try:
                import cairo
                surface.set_input_region(cairo.Region())
            except (ImportError, TypeError, AttributeError) as e:
                log.debug("could not clear input region: %s", e)

    def _x11_setup(self, *_):
        from . import x11
        x11.make_desktop_window(self, self.monitor.get_geometry())

    def set_view(self, view):
        if self.view is not None:
            self.view.stop()
        self.view = view
        self.set_child(view)

    def pause(self):
        if self.view is not None:
            self.view.pause()

    def resume(self):
        if self.view is not None:
            self.view.resume()

    def close_wallpaper(self):
        if self.view is not None:
            self.view.stop()
            self.view = None
        self.destroy()
