"""The background process: one wallpaper window per screen, kept in sync with the config.

Run through `mural start` (or `python -m mural.daemon`). Controlled over D-Bus actions
(set-default, pause, resume, toggle, reload, quit) exported by Gtk.Application.
"""
import json
import logging
import os
import signal
import sys

from . import const


def _ensure_layer_shell_preloaded():
    """gtk4-layer-shell must be loaded before libwayland; re-exec once with LD_PRELOAD if needed."""
    from . import surface
    if (os.environ.get("WAYLAND_DISPLAY") and not surface.layer_shell_preloaded()
            and not os.environ.get("MURAL_NO_PRELOAD")):
        lib = surface.layer_shell_path()
        if lib:
            env = dict(os.environ, MURAL_NO_PRELOAD="1")
            env["LD_PRELOAD"] = ":".join(p for p in (lib, os.environ.get("LD_PRELOAD")) if p)
            os.execve(sys.executable, [sys.executable, "-m", "mural.daemon", *sys.argv[1:]], env)


def main():
    logging.basicConfig(level=os.environ.get("MURAL_LOG", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _ensure_layer_shell_preloaded()
    # Our content is OpenGL (shaders) and GL video frames: GTK's GL renderer composites them directly,
    # while the Vulkan one costs ~7x the CPU (measured: 21% vs 3% for a 30 fps shader).
    os.environ.setdefault("GSK_RENDERER", "gl")

    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gdk, Gio, GLib, Gtk

    from . import config, library, palette
    from .pause import PauseManager
    from .render import ImageView, ShaderView, VideoView
    from .surface import WallpaperWindow, gnome_extension_enabled, pick_backend

    log = logging.getLogger("mural.daemon")

    class Daemon(Gtk.Application):
        def __init__(self):
            super().__init__(application_id=const.DAEMON_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
            self.windows = {}          # connector -> WallpaperWindow
            self.shown = {}            # connector -> wallpaper ref currently displayed
            self.cfg = config.load()
            self.palette = palette.load(self.cfg["colors"])
            self.pauser = PauseManager()
            self.backend = None
            self._monitors = []
            self._file_watches = []

        def do_startup(self):
            Gtk.Application.do_startup(self)
            if Gdk.Display.get_default() is None:
                log.error("can't reach the desktop (no Wayland or X11 display). Run Mural from inside your"
                          " desktop session; WAYLAND_DISPLAY=%r DISPLAY=%r",
                          os.environ.get("WAYLAND_DISPLAY"), os.environ.get("DISPLAY"))
                self.write_status(error="no display")
                self.no_display = True
                return
            self.hold()   # stay alive even with no screens (e.g. while monitors reconnect)
            css = Gtk.CssProvider()
            css.load_from_string("window.mural-wallpaper { background: black; }")
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                      Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            for name, handler, ptype in (("pause", lambda *_: self.pauser.set_manual(True), None),
                                         ("resume", lambda *_: self.pauser.set_manual(False), None),
                                         ("toggle", lambda *_: self.pauser.set_manual(not self.pauser.manual), None),
                                         ("reload", lambda *_: self.reload(), None),
                                         ("snapshot", lambda *_: self.snapshot(), None),
                                         ("quit", lambda *_: self.shutdown(), None),
                                         ("external-pause",
                                          lambda _a, v: self.pauser.set_external_fullscreen(v.get_boolean()),
                                          GLib.VariantType.new("b"))):
                action = Gio.SimpleAction.new(name, ptype)
                action.connect("activate", handler)
                self.add_action(action)
            try:
                gi.require_version("GLibUnix", "2.0")
                from gi.repository import GLibUnix
                add_signal = GLibUnix.signal_add
            except (ValueError, ImportError):          # older GLib
                add_signal = GLib.unix_signal_add
            for sig in (signal.SIGINT, signal.SIGTERM):
                add_signal(GLib.PRIORITY_DEFAULT, sig, lambda *_: self.shutdown() or GLib.SOURCE_REMOVE)

        PAUSE_XML = (
            "<node><interface name='io.github.dork40.Mural.Pause'>"
            "<method name='SetFullscreen'><arg type='b' direction='in'/></method>"
            "</interface></node>")

        def do_dbus_register(self, connection, object_path):
            node = Gio.DBusNodeInfo.new_for_xml(self.PAUSE_XML)

            def call(_conn, _sender, _path, _iface, method, params, invocation):
                if method == "SetFullscreen":
                    self.pauser.set_external_fullscreen(params.unpack()[0])
                invocation.return_value(None)
            self._pause_reg = connection.register_object(object_path, node.interfaces[0], call, None, None)
            return Gtk.Application.do_dbus_register(self, connection, object_path)

        def do_dbus_unregister(self, connection, object_path):
            if getattr(self, "_pause_reg", 0):
                connection.unregister_object(self._pause_reg)
                self._pause_reg = 0
            Gtk.Application.do_dbus_unregister(self, connection, object_path)

        def _load_kwin_script(self):
            """KDE Plasma (Wayland): KWin only tells scripts about fullscreen windows, so load one."""
            if "kde" not in os.environ.get("XDG_CURRENT_DESKTOP", "").lower() or os.environ.get("DISPLAY") and \
                    not os.environ.get("WAYLAND_DISPLAY"):
                return
            script = os.path.join(os.path.dirname(__file__), "kwin", "main.js")
            bus = self.get_dbus_connection()
            try:
                bus.call_sync("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", "unloadScript",
                              GLib.Variant("(s)", ("mural",)), None, Gio.DBusCallFlags.NONE, 2000, None)
                res = bus.call_sync("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", "loadScript",
                                    GLib.Variant("(ss)", (script, "mural")), GLib.VariantType("(i)"),
                                    Gio.DBusCallFlags.NONE, 2000, None)
                sid = res.unpack()[0]
                for path in (f"/Scripting/Script{sid}", f"/{sid}"):
                    try:
                        bus.call_sync("org.kde.KWin", path, "org.kde.kwin.Script", "run", None, None,
                                      Gio.DBusCallFlags.NONE, 2000, None)
                        break
                    except GLib.Error:
                        continue
                self.pauser.source = "kwin"
                log.info("KWin fullscreen helper loaded")
            except GLib.Error as e:
                log.warning("could not load the KWin helper, fullscreen pausing is off: %s", e.message)

        def do_activate(self):
            if getattr(self, "no_display", False):
                self.quit()
                return
            if self.backend is not None:
                return
            display = Gdk.Display.get_default()
            self.backend = pick_backend(display)
            log.info("Mural %s, backend %s", const.VERSION, self.backend)
            if self.backend == "unsupported":
                log.error("this desktop has no way to place a wallpaper window (no layer-shell, not X11)")
                self.write_status(error="unsupported desktop")
                self.quit()
                return
            if self.backend == "gnome" and not gnome_extension_enabled():
                log.warning("GNOME: the Mural extension is not enabled, wallpapers will show as normal windows."
                            " Run: gnome-extensions enable mural@dork40.github.io (log out and in after installing)")
            self._monitors = display.get_monitors()
            self._monitors.connect("items-changed", lambda *_: GLib.idle_add(self.sync_monitors))
            self.pauser.connect("changed", lambda *_: self.apply_pause())
            self.pauser.start()
            self._load_kwin_script()
            self._watch(const.CONFIG_PATH, self.reload)
            for _name, path in palette.candidates():
                self._watch(path, self.reload_palette)
            self.sync_monitors()

        def _watch(self, path, callback):
            path = os.fspath(path)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            mon = Gio.File.new_for_path(path).monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, None)
            mon.set_rate_limit(300)
            mon.connect("changed", lambda *_a: callback())
            self._file_watches.append(mon)

        # -- screens ---------------------------------------------------------------
        def sync_monitors(self):
            current = {}
            for i in range(self._monitors.get_n_items()):
                m = self._monitors.get_item(i)
                current[m.get_connector() or f"monitor-{i}"] = m
            self.pauser.all_screens = tuple(current)
            geos = {}
            for conn, m in current.items():
                g = m.get_geometry()
                sc = m.get_scale_factor() if self.backend == "x11" else 1   # X11 window coords are device pixels
                geos[conn] = (g.x * sc, g.y * sc, g.width * sc, g.height * sc)
            self.pauser.screen_geometry = geos
            for conn in list(self.windows):
                if conn not in current or self.windows[conn].monitor is not current[conn]:
                    log.info("screen %s gone or changed", conn)
                    self.windows.pop(conn).close_wallpaper()
                    self.shown.pop(conn, None)
            for conn, mon in current.items():
                if conn not in self.windows:
                    log.info("screen %s added", conn)
                    win = WallpaperWindow(self, mon, self.backend)
                    self.windows[conn] = win
                    win.present()
            self.apply_wallpapers()
            return GLib.SOURCE_REMOVE

        # -- content ---------------------------------------------------------------
        def make_view(self, wp):
            if wp.kind == "shader":
                try:
                    src = wp.path.read_text(encoding="utf-8")
                except OSError as e:
                    log.error("cannot read %s: %s", wp.path, e)
                    src = "void main(){ fragColor = vec4(uBackground, 1.0); }"
                return ShaderView(src, self.palette, self.cfg["fps"])
            if wp.kind == "video":
                return VideoView(wp.path)
            return ImageView(wp.path)

        def apply_wallpapers(self):
            for conn, win in self.windows.items():
                ref = config.wallpaper_for(self.cfg, conn)
                wp = library.resolve(ref)
                if wp is None:
                    log.warning("wallpaper %r not found on %s: using the default", ref, conn)
                    wp = library.resolve(config.DEFAULTS["wallpaper"])
                if self.shown.get(conn) == wp.ref and win.view is not None:
                    continue
                log.info("%s: %s (%s)", conn, wp.name, wp.kind)
                win.set_view(self.make_view(wp))
                self.shown[conn] = wp.ref
            self.apply_pause()

        def apply_pause(self):
            for conn, win in self.windows.items():
                paused, _reason = self.pauser.state(conn, self.cfg)
                if win.view is None:
                    continue
                if paused and not win.view.paused:
                    win.pause()
                elif not paused and win.view.paused:
                    win.resume()
            self.write_status()

        def reload(self):
            new = config.load()
            fps_changed = new["fps"] != self.cfg["fps"]
            colors_changed = new["colors"] != self.cfg["colors"]
            self.cfg = new
            if colors_changed:
                self.reload_palette()
            if fps_changed:
                for win in self.windows.values():
                    if isinstance(win.view, ShaderView):
                        win.view.set_fps(self.cfg["fps"])
            self.apply_wallpapers()
            if self.cfg.get("lock_screen"):
                GLib.timeout_add(600, self._refresh_lock)   # let the new frame draw first

        def _refresh_lock(self):
            """Snapshot the current wallpaper and copy it to hyprlock's lock image."""
            from . import lock
            try:
                self.snapshot()
                lock.save_lock_image()
            except Exception as e:  # noqa: BLE001 - a failed refresh must never take the daemon down
                log.info("lock image not refreshed: %s", e)
            return False

        def reload_palette(self):
            new = palette.load(self.cfg["colors"])
            if new != self.palette:
                self.palette = new
                log.info("colours from %s", new.source)
                for win in self.windows.values():
                    if isinstance(win.view, ShaderView):
                        win.view.set_palette(new)

        # -- status ----------------------------------------------------------------
        def snapshot(self):
            """Save what each screen's wallpaper shows right now to ~/.cache/mural/snapshot-<screen>.png."""
            const.CACHE_DIR.mkdir(parents=True, exist_ok=True)
            for conn, win in self.windows.items():
                view = win.view
                w, h = (view.get_width(), view.get_height()) if view else (0, 0)
                if not view or w <= 0 or h <= 0:
                    continue
                snap = Gtk.Snapshot()
                Gtk.WidgetPaintable.new(view).snapshot(snap, w, h)
                node = snap.to_node()
                renderer = win.get_renderer()
                if node is None or renderer is None:
                    continue
                path = const.CACHE_DIR / f"snapshot-{conn}.png"
                renderer.render_texture(node, None).save_to_png(str(path))
                log.info("snapshot of %s saved to %s", conn, path)

        def write_status(self, error=None):
            screens = {}
            for conn, win in self.windows.items():
                paused, reason = self.pauser.state(conn, self.cfg)
                view = win.view
                screens[conn] = {"wallpaper": self.shown.get(conn), "paused": paused, "reason": reason,
                                 "error": getattr(view, "error", None)}
            data = {"pid": os.getpid(), "version": const.VERSION, "backend": self.backend,
                    "pause_source": self.pauser.source, "manual_pause": self.pauser.manual,
                    "colors": self.palette.source, "screens": screens, "error": error}
            try:
                const.STATE_DIR.mkdir(parents=True, exist_ok=True)
                tmp = const.STATUS_PATH.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, indent=2))
                os.replace(tmp, const.STATUS_PATH)
            except OSError as e:
                log.debug("status not written: %s", e)

        def shutdown(self):
            if getattr(self, "_stopping", False):
                return
            self._stopping = True
            log.info("stopping")
            for win in list(self.windows.values()):
                win.close_wallpaper()
            self.windows.clear()
            try:
                const.STATUS_PATH.unlink()
            except OSError:
                pass
            self.release()
            self.quit()

    const.ensure_dirs()
    return Daemon().run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
