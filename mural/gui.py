"""Mural's window: pick a wallpaper, pause it, change settings. Everything goes through here."""
import logging
import os
import shutil
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import config, const, library, palette, thumbs  # noqa: E402
from .cli import daemon_action, daemon_running, read_status, start_daemon  # noqa: E402

log = logging.getLogger(__name__)

APP_ID = const.APP_ID
ALL_SCREENS = "*"
AUTOSTART = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart" / "mural.desktop"

REASONS = {
    "manual": "Paused",
    "fullscreen": "Paused while a fullscreen app is open",
    "game": "Paused while you're in a game",
    "gamemode": "Paused while GameMode is on",
    "battery": "Paused on battery",
}

CSS = """
.wall-card { border-radius: 12px; padding: 0; background: none; }
.wall-card .thumb-frame { border-radius: 10px; background: alpha(@window_fg_color, 0.06); }
.wall-card .thumb-frame picture, .wall-card .thumb-frame glarea { border-radius: 10px; }
.wall-card.current .thumb-frame { outline: 2px solid @accent_color; outline-offset: 2px; }
.wall-card:hover .thumb-frame { background: alpha(@window_fg_color, 0.1); }
.wall-card .name { font-weight: 600; }
.kind-badge { background: alpha(black, 0.62); color: white; border-radius: 6px; padding: 3px 8px;
              font-size: 0.85em; font-weight: 700; margin: 8px; }
.status-line { padding: 6px 18px 10px 18px; }
.status-dot { min-width: 8px; min-height: 8px; border-radius: 99px; background: @success_color; }
.status-dot.paused { background: @warning_color; }
.status-dot.off { background: alpha(@window_fg_color, 0.3); }
.section-title { font-weight: 700; font-size: 1.05em; margin: 18px 18px 6px 18px; }
.empty-hint { opacity: 0.55; margin: 0 18px 12px 18px; }
"""


# -- settings that live outside config.json --------------------------------------------------
SERVICE = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user" / "mural.service"


def _systemctl(*args):
    import subprocess
    try:
        return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None


def _session_target_active():
    r = _systemctl("is-active", "graphical-session.target")
    return bool(r and r.stdout.strip() == "active")


def autostart_enabled():
    if SERVICE.exists():
        r = _systemctl("is-enabled", "mural.service")
        return bool(r and r.stdout.strip() == "enabled")
    return AUTOSTART.exists()


def set_autostart(on):
    """Start with the desktop. Where the session runs systemd's graphical-session.target (Hyprland,
    Sway, KDE, GNOME...) a user service does it, because many window managers ignore autostart files;
    everywhere else an XDG autostart entry."""
    AUTOSTART.unlink(missing_ok=True)
    if SERVICE.exists():
        _systemctl("disable", "mural.service")
        SERVICE.unlink(missing_ok=True)
        _systemctl("daemon-reload")
    if not on:
        return
    pkg_root = str(Path(__file__).resolve().parent.parent)
    if _session_target_active():
        SERVICE.parent.mkdir(parents=True, exist_ok=True)
        SERVICE.write_text(
            "[Unit]\nDescription=Mural live wallpaper\nPartOf=graphical-session.target\n"
            "After=graphical-session.target\n\n[Service]\n"
            f"Environment=PYTHONPATH={pkg_root}\n"
            f"ExecStart={sys.executable} -m mural.daemon\nRestart=on-failure\nRestartSec=3\n\n"
            "[Install]\nWantedBy=graphical-session.target\n")
        _systemctl("daemon-reload")
        _systemctl("enable", "mural.service")
        return
    AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
    AUTOSTART.write_text("[Desktop Entry]\nType=Application\nName=Mural\nComment=Live wallpaper\n"
                         f"Exec=env PYTHONPATH={pkg_root} {sys.executable} -m mural start\n"
                         "NoDisplay=true\nX-GNOME-Autostart-Phase=Applications\n")


def add_to_library(path):
    """Link a video or picture into ~/.local/share/mural/wallpapers so it stays in the grid."""
    src = Path(path).expanduser().resolve()
    kind = library.kind_of(src)
    if kind not in ("video", "image"):
        return None
    const.USER_WALLPAPERS.mkdir(parents=True, exist_ok=True)
    dest = const.USER_WALLPAPERS / src.name
    n = 1
    while dest.exists() or dest.is_symlink():
        if dest.resolve() == src:
            return dest
        dest = const.USER_WALLPAPERS / f"{src.stem}-{n}{src.suffix}"
        n += 1
    dest.symlink_to(src)
    return dest


def apply(ref, screen=ALL_SCREENS):
    cfg = config.load()
    if screen == ALL_SCREENS:
        cfg["wallpaper"] = ref
        cfg["monitors"] = {}
    else:
        cfg["monitors"][screen] = ref
    config.save(cfg)
    if daemon_running():
        daemon_action("reload")
    else:
        start_daemon()


def status_text(status):
    """(dot css class, sentence) for the line under the header."""
    if not status or not daemon_running():
        return "off", "Mural is off"
    if status.get("error"):
        return "off", f"Mural can't run here: {status['error']}"
    screens = status.get("screens") or {}
    if not screens:
        return "off", "Waiting for a screen"
    names = []
    reasons = set()
    for st in screens.values():
        wp = library.resolve(st.get("wallpaper") or "")
        if wp and wp.name not in names:
            names.append(wp.name)
        if st.get("paused"):
            reasons.add(st.get("reason") or "manual")
    what = " and ".join(names) if names else "nothing"
    if reasons:
        reason = "manual" if "manual" in reasons else sorted(reasons)[0]
        return "paused", f"{REASONS.get(reason, 'Paused')} · {what}"
    return "", f"Showing {what}"


# -- one wallpaper in the grid -----------------------------------------------------------------
class Card(Gtk.Button):
    def __init__(self, wp, window):
        super().__init__(css_classes=["wall-card", "flat"], tooltip_text=wp.name)
        self.wp = wp
        self.window = window
        self.preview = None
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        frame = Adw.Bin(css_classes=["thumb-frame"], height_request=150, width_request=220)
        frame.set_overflow(Gtk.Overflow.HIDDEN)
        self.frame = frame
        overlay = Gtk.Overlay(child=frame)
        if wp.kind != "shader":                    # say plainly what it is, right on the picture
            badge = Gtk.Box(spacing=5, css_classes=["kind-badge"], halign=Gtk.Align.START,
                            valign=Gtk.Align.START, can_target=False)
            badge.append(Gtk.Image(icon_name="camera-video-symbolic" if wp.kind == "video" else "image-x-generic-symbolic",
                                   pixel_size=12))
            badge.append(Gtk.Label(label="Video" if wp.kind == "video" else "Image"))
            overlay.add_overlay(badge)
        box.append(overlay)
        row = Gtk.Box(spacing=6, margin_start=4, margin_end=4)
        row.append(Gtk.Label(label=wp.name, xalign=0, hexpand=True, ellipsize=3, css_classes=["name"]))
        box.append(row)
        self.set_child(box)
        self.connect("clicked", lambda *_: window.choose(self.wp))

        if wp.kind == "shader":
            self._shader_preview()
        else:
            self._file_preview()

        if not wp.builtin and wp.kind != "shader":
            gesture = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
            gesture.connect("pressed", self._menu)
            self.add_controller(gesture)

    def _shader_preview(self):
        from .render import ShaderView
        try:
            src = self.wp.path.read_text()
        except OSError:
            return
        view = ShaderView(src, self.window.palette, fps=30)
        view._elapsed = 24.0               # a frame from a little way in looks more like the real thing
        view.pause()
        self.preview = view
        self.frame.set_child(view)
        hover = Gtk.EventControllerMotion()
        hover.connect("enter", lambda *_: view.resume())       # it moves while you point at it
        hover.connect("leave", lambda *_: view.pause())
        self.add_controller(hover)

    def _file_preview(self):
        pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
        self.frame.set_child(pic)

        def work():
            path = thumbs.thumbnail(self.wp.path, self.wp.kind)
            GLib.idle_add(lambda: path and pic.set_filename(str(path)) and False)
        threading.Thread(target=work, daemon=True).start()

    def _menu(self, gesture, _n, x, y):
        pop = Gtk.PopoverMenu.new_from_model(self.window.card_menu(self.wp))
        pop.set_parent(self)
        pop.set_pointing_to(Gdk.Rectangle(int(x), int(y), 1, 1))
        pop.popup()

    def set_current(self, on):
        (self.add_css_class if on else self.remove_css_class)("current")


# -- main window -------------------------------------------------------------------------------
class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Mural", default_width=980, default_height=720)
        self.palette = palette.load(config.load().get("colors", "auto"))
        self.cards = []
        self.screen = ALL_SCREENS

        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Add videos or images")
        add.connect("clicked", lambda *_: self.pick_files())
        header.pack_start(add)

        self.pause_btn = Gtk.Button(icon_name="media-playback-pause-symbolic", tooltip_text="Pause")
        self.pause_btn.connect("clicked", lambda *_: self.toggle_pause())
        menu = Gio.Menu()
        menu.append("Preferences", "app.preferences")
        menu.append("About Mural", "app.about")
        menu.append("Turn Mural off", "app.turn-off")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, primary=True,
                                       tooltip_text="Menu"))
        header.pack_end(self.pause_btn)

        self.screen_dd = Gtk.DropDown(tooltip_text="Which screen to change", visible=False)
        self.screen_dd.connect("notify::selected", self._on_screen_changed)
        header.pack_start(self.screen_dd)
        view.add_top_bar(header)

        self.banner = Adw.Banner(title="Mural is off", button_label="Turn on")
        self.banner.connect("button-clicked", lambda *_: self.turn_on())
        view.add_top_bar(self.banner)

        status = Gtk.Box(spacing=8, css_classes=["status-line"])
        self.dot = Gtk.Box(css_classes=["status-dot"], valign=Gtk.Align.CENTER)
        self.status_label = Gtk.Label(xalign=0, css_classes=["dim-label"])
        status.append(self.dot)
        status.append(self.status_label)
        view.add_top_bar(status)

        self.toasts = Adw.ToastOverlay()
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_bottom=18)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroll.set_child(Adw.Clamp(maximum_size=1300, child=self.content))
        self.toasts.set_child(scroll)
        view.set_content(self.toasts)
        self.set_content(view)

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_drop)
        self.add_controller(drop)

        self.build_grid()
        self.refresh_screens()
        self.refresh_status()
        self._watches = []
        for path in (const.STATUS_PATH, const.CONFIG_PATH):
            path.parent.mkdir(parents=True, exist_ok=True)
            mon = Gio.File.new_for_path(str(path)).monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, None)
            mon.set_rate_limit(250)
            mon.connect("changed", lambda *_: GLib.idle_add(self._on_changed))
            self._watches.append(mon)
        lib_dir = Gio.File.new_for_path(str(const.USER_WALLPAPERS))
        const.USER_WALLPAPERS.mkdir(parents=True, exist_ok=True)
        mon = lib_dir.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
        mon.set_rate_limit(500)
        mon.connect("changed", lambda *_: GLib.idle_add(self.build_grid))
        self._watches.append(mon)
        GLib.timeout_add_seconds(3, self._poll)      # notices the daemon stopping or crashing

    # -- grid ------------------------------------------------------------------------------
    def build_grid(self):
        for card in self.cards:
            if card.preview is not None:
                card.preview.pause()
        self.cards = []
        while (child := self.content.get_first_child()) is not None:
            self.content.remove(child)
        walls = library.list_all()
        animated = [w for w in walls if w.kind == "shader"]
        mine = [w for w in walls if w.kind != "shader"]
        self._section("Animated", animated, None)
        self._section("Your videos and images", mine,
                      "Drop videos or images here, or use the + button.")
        self.mark_current()
        return False

    def _section(self, title, walls, empty):
        self.content.append(Gtk.Label(label=title, xalign=0, css_classes=["section-title"]))
        if not walls:
            if empty:
                self.content.append(Gtk.Label(label=empty, xalign=0, wrap=True, css_classes=["empty-hint"]))
            return
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, min_children_per_line=2,
                           max_children_per_line=6, column_spacing=14, row_spacing=14,
                           margin_start=18, margin_end=18)
        for wp in walls:
            card = Card(wp, self)
            self.cards.append(card)
            flow.append(card)
        self.content.append(flow)

    def mark_current(self):
        cfg = config.load()
        if self.screen == ALL_SCREENS:
            current = {cfg["wallpaper"]} if not cfg["monitors"] else set(cfg["monitors"].values()) | {cfg["wallpaper"]}
        else:
            current = {config.wallpaper_for(cfg, self.screen)}
        for card in self.cards:
            card.set_current(card.wp.ref in current)

    def card_menu(self, wp):
        m = Gio.Menu()
        m.append("Remove from Mural", f"app.remove::{wp.path}")
        return m

    # -- actions ---------------------------------------------------------------------------
    def choose(self, wp):
        apply(wp.ref, self.screen)
        where = "" if self.screen == ALL_SCREENS else f" on {self.screen}"
        self.toasts.add_toast(Adw.Toast(title=f"{wp.name}{where}", timeout=2))
        self.mark_current()
        GLib.timeout_add(800, self._on_changed)

    def toggle_pause(self):
        st = read_status() or {}
        if not daemon_running():
            self.turn_on()
            return
        daemon_action("resume" if st.get("manual_pause") else "pause")

    def turn_on(self):
        if not start_daemon():
            self.toasts.add_toast(Adw.Toast(title="Mural couldn't start on this desktop", timeout=4))
        GLib.timeout_add(800, self._on_changed)

    def pick_files(self):
        dialog = Gtk.FileDialog(title="Add videos or images")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        f = Gtk.FileFilter(name="Videos and images")
        for ext in sorted(const.VIDEO_EXTS | const.IMAGE_EXTS):
            f.add_suffix(ext.lstrip("."))
        filters.append(f)
        dialog.set_filters(filters)
        dialog.open_multiple(self, None, self._on_files_picked)

    def _on_files_picked(self, dialog, result):
        try:
            files = dialog.open_multiple_finish(result)
        except GLib.Error:
            return
        self.add_files([files.get_item(i).get_path() for i in range(files.get_n_items())])

    def _on_drop(self, _target, value, _x, _y):
        self.add_files([f.get_path() for f in value.get_files() if f.get_path()])
        return True

    def add_files(self, paths):
        added = [p for p in paths if add_to_library(p)]
        skipped = len(paths) - len(added)
        if added:
            self.build_grid()
            if len(added) == 1:
                wp = library.resolve(library.ref_for_path(const.USER_WALLPAPERS / Path(added[0]).name)) or \
                    library.resolve(library.ref_for_path(added[0]))
                if wp:
                    self.choose(wp)
        if skipped:
            self.toasts.add_toast(Adw.Toast(title=f"{skipped} file(s) skipped: not a video or image", timeout=3))

    # -- screens & status --------------------------------------------------------------------
    def refresh_screens(self):
        monitors = Gdk.Display.get_default().get_monitors()
        names = [monitors.get_item(i).get_connector() or f"Screen {i + 1}" for i in range(monitors.get_n_items())]
        self._screens = [ALL_SCREENS, *names]
        self.screen_dd.set_model(Gtk.StringList.new(["All screens", *names]))
        self.screen_dd.set_visible(len(names) > 1)
        if not monitors.get_n_items() or not hasattr(self, "_mon_sig"):
            self._mon_sig = monitors.connect("items-changed", lambda *_: self.refresh_screens())

    def _on_screen_changed(self, dd, _p):
        i = dd.get_selected()
        self.screen = self._screens[i] if 0 <= i < len(self._screens) else ALL_SCREENS
        self.mark_current()

    def refresh_status(self):
        running = daemon_running()
        st = read_status() if running else None
        cls, text = status_text(st)
        self.banner.set_revealed(not running)
        self.status_label.set_label(text)
        for c in ("paused", "off"):
            self.dot.remove_css_class(c)
        if cls:
            self.dot.add_css_class(cls)
        manual = bool(st and st.get("manual_pause"))
        self.pause_btn.set_icon_name("media-playback-start-symbolic" if manual else "media-playback-pause-symbolic")
        self.pause_btn.set_tooltip_text("Play" if manual else "Pause")
        self.pause_btn.set_sensitive(running)

    def _on_changed(self):
        new = palette.load(config.load().get("colors", "auto"))
        if new != self.palette:
            self.palette = new
            for card in self.cards:
                if card.preview is not None:
                    card.preview.set_palette(new)
        self.refresh_status()
        self.mark_current()
        return False

    def _poll(self):
        self.refresh_status()
        return True


# -- preferences ---------------------------------------------------------------------------------
class Preferences(Adw.PreferencesDialog):
    def __init__(self):
        super().__init__(title="Preferences")
        self.cfg = config.load()
        page = Adw.PreferencesPage()

        look = Adw.PreferencesGroup(title="Look")
        colors = Adw.SwitchRow(title="Match my theme's colours",
                               subtitle="Animated wallpapers use your pywal or rice colours")
        colors.set_active(self.cfg["colors"] == "auto")
        colors.connect("notify::active", lambda r, _p: self.save(colors="auto" if r.get_active() else "default"))
        look.add(colors)
        fps_choices = [15, 30, 60]
        fps = Adw.ComboRow(title="Smoothness", subtitle="Higher is smoother but uses more power",
                           model=Gtk.StringList.new(["Low (15 fps)", "Normal (30 fps)", "High (60 fps)"]))
        cur = min(range(3), key=lambda i: abs(fps_choices[i] - self.cfg["fps"]))
        fps.set_selected(cur)
        fps.connect("notify::selected", lambda r, _p: self.save(fps=fps_choices[r.get_selected()]))
        look.add(fps)
        page.add(look)

        pause = Adw.PreferencesGroup(title="Pause automatically",
                                     description="A paused wallpaper uses no CPU or GPU at all.")
        for key, title, sub in (
                ("pause_on_fullscreen", "When an app is fullscreen", "Only on the screen it covers"),
                ("pause_on_game", "When I'm in a game", "Steam, Proton, Minecraft, emulators and more"),
                ("pause_on_gamemode", "When GameMode is on", None),
                ("pause_on_battery", "On battery", "Saves power on laptops")):
            row = Adw.SwitchRow(title=title, subtitle=sub or "")
            row.set_active(bool(self.cfg[key]))
            row.connect("notify::active", lambda r, _p, k=key: self.save(**{k: r.get_active()}))
            pause.add(row)
        page.add(pause)

        start = Adw.PreferencesGroup(title="Startup")
        auto = Adw.SwitchRow(title="Start with my desktop", subtitle="Your wallpaper comes back when you log in")
        auto.set_active(autostart_enabled())
        auto.connect("notify::active", lambda r, _p: set_autostart(r.get_active()))
        start.add(auto)
        page.add(start)
        self.add(page)

    def save(self, **changes):
        self.cfg = config.load()
        self.cfg.update(changes)
        config.save(self.cfg)
        if daemon_running():
            daemon_action("reload")


# -- application -----------------------------------------------------------------------------
class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.window = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.PREFER_DARK)
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        for name, cb, ptype in (("preferences", lambda *_: Preferences().present(self.window), None),
                                ("about", lambda *_: self.about(), None),
                                ("turn-off", lambda *_: self.turn_off(), None),
                                ("remove", self.remove, GLib.VariantType.new("s"))):
            a = Gio.SimpleAction.new(name, ptype)
            a.connect("activate", cb)
            self.add_action(a)
        self.set_accels_for_action("app.preferences", ["<Control>comma"])
        self.set_accels_for_action("window.close", ["<Control>w", "<Control>q"])

    def do_activate(self):
        if self.window is None:
            self.window = Window(self)
            self._gnome_setup()
        self.window.present()

    def _gnome_setup(self):
        """On GNOME the wallpaper needs Mural's Shell extension switched on."""
        from .surface import GNOME_EXTENSION, gnome_extension_enabled, is_gnome
        if not is_gnome() or gnome_extension_enabled():
            return
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call_sync("org.gnome.Shell.Extensions", "/org/gnome/Shell/Extensions",
                          "org.gnome.Shell.Extensions", "EnableExtension",
                          GLib.Variant("(s)", (GNOME_EXTENSION,)), None, Gio.DBusCallFlags.NONE, 3000, None)
        except GLib.Error:
            pass
        if not gnome_extension_enabled():
            self.window.toasts.add_toast(Adw.Toast(
                title="Log out and back in once to finish setting Mural up on GNOME", timeout=0))

    def turn_off(self):
        if daemon_running():
            daemon_action("quit")
        GLib.timeout_add(600, lambda: self.window.refresh_status() or False)

    def remove(self, _action, param):
        p = Path(param.get_string())
        if p.parent == const.USER_WALLPAPERS and (p.is_symlink() or p.exists()):
            p.unlink()          # only our link (or copy) in Mural's folder; the original file stays
        self.window.build_grid()

    def about(self):
        Adw.AboutDialog(application_name="Mural", application_icon=APP_ID, version=const.VERSION,
                        developer_name="dork40", website="https://github.com/dork40/mural",
                        issue_url="https://github.com/dork40/mural/issues",
                        license_type=Gtk.License.GPL_3_0,
                        comments="Live wallpapers for Linux").present(self.window)


def main():
    logging.basicConfig(level=os.environ.get("MURAL_LOG", "WARNING"))
    os.environ.setdefault("GSK_RENDERER", "gl")
    return App().run(sys.argv[:1])


if __name__ == "__main__":
    sys.exit(main())
