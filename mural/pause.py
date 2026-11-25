"""When should a screen's wallpaper stop moving?

Reasons, per screen:
  manual      - `mural pause`
  fullscreen  - a fullscreen window covers this screen          (Hyprland, Sway/i3, X11, GNOME)
  game        - a game window is focused (pauses every screen)  (Hyprland, Sway/i3, X11)
  gamemode    - Feral GameMode is active                         (any desktop)
  battery     - running on battery, if enabled                   (any desktop)

The decision logic is plain functions (tested); the watchers just feed them.
"""
import json
import logging
import os
import re
import socket
import threading

from gi.repository import Gio, GLib, GObject

log = logging.getLogger(__name__)

GAME_CLASS = re.compile(
    r"^(steam_app_\d+|gamescope|steam_proton|.+\.exe|minecraft.*|org\.vinegarhq\.sober|sober|"
    r"cs2|dota2|hl2_linux|retroarch|dolphin-emu|org\.ppsspp\.ppsspp|pcsx2.*|rpcs3|yuzu|ryujinx.*)$",
    re.I)


def is_game_class(cls):
    return bool(cls) and bool(GAME_CLASS.match(cls))


def decide(connector, cfg, *, manual=False, fullscreen=(), game=False, gamemode=False, on_battery=False):
    """(paused, reason) for one screen. Order = what the user most likely wants to see as the reason."""
    if manual:
        return True, "manual"
    if cfg.get("pause_on_game") and game:
        return True, "game"
    if cfg.get("pause_on_fullscreen") and connector in fullscreen:
        return True, "fullscreen"
    if cfg.get("pause_on_gamemode") and gamemode:
        return True, "gamemode"
    if cfg.get("pause_on_battery") and on_battery:
        return True, "battery"
    return False, ""


# -- Hyprland ---------------------------------------------------------------------------
def hypr_fullscreen_monitors(monitors, workspaces):
    """Monitors whose visible (or special) workspace has a fullscreen window."""
    full = {w["id"] for w in workspaces if w.get("hasfullscreen")}
    out = set()
    for m in monitors:
        visible = {(m.get("activeWorkspace") or {}).get("id"), (m.get("specialWorkspace") or {}).get("id")}
        if visible & full:
            out.add(m["name"])
    return out


def hypr_socket_dir():
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not sig:
        return None
    for base in (os.path.join(os.environ.get("XDG_RUNTIME_DIR", ""), "hypr"), "/tmp/hypr"):
        d = os.path.join(base, sig)
        if os.path.exists(os.path.join(d, ".socket.sock")):
            return d
    return None


def hypr_query(sock_dir, what):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        s.connect(os.path.join(sock_dir, ".socket.sock"))
        s.sendall(f"j/{what}".encode())
        chunks = []
        while True:
            c = s.recv(65536)
            if not c:
                break
            chunks.append(c)
    finally:
        s.close()
    return json.loads(b"".join(chunks) or b"null")


HYPR_EVENTS = {"fullscreen", "workspace", "workspacev2", "focusedmon", "focusedmonv2", "activewindow",
               "activewindowv2", "openwindow", "closewindow", "movewindow", "movewindowv2",
               "activespecial", "activespecialv2", "monitoradded", "monitoraddedv2", "monitorremoved",
               "monitorremovedv2", "changefloatingmode"}


# -- Sway / i3 --------------------------------------------------------------------------
def sway_state(tree, outputs):
    """(fullscreen outputs, focused class) from i3ipc-style dicts."""
    visible = {o["current_workspace"]: o["name"] for o in outputs if o.get("active") and o.get("current_workspace")}
    full, focused_cls = set(), None

    def walk(node, output):
        nonlocal focused_cls
        if node.get("type") == "workspace":
            output = visible.get(node.get("name"))
        if node.get("fullscreen_mode") and output and node.get("type") == "con":
            full.add(output)
        if node.get("fullscreen_mode") == 2:              # global fullscreen covers every screen
            full.update(visible.values())
        if node.get("focused"):
            focused_cls = node.get("app_id") or (node.get("window_properties") or {}).get("class")
        for child in node.get("nodes", []) + node.get("floating_nodes", []):
            walk(child, output)

    walk(tree, None)
    return full, focused_cls


def x11_fullscreen_screens(windows, screens):
    """windows: [(x, y, w, h)] of visible fullscreen windows; screens: {connector: (x, y, w, h)}.
    A screen counts as covered when a fullscreen window's centre is on it."""
    out = set()
    for x, y, w, h in windows:
        cx, cy = x + w / 2, y + h / 2
        for conn, (sx, sy, sw, sh) in screens.items():
            if sx <= cx < sx + sw and sy <= cy < sy + sh:
                out.add(conn)
    return out


class PauseManager(GObject.Object):
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self):
        super().__init__()
        self.manual = False
        self.fullscreen = set()
        self.all_screens = ()        # connectors, kept current by the daemon
        self.screen_geometry = {}    # connector -> (x, y, w, h), for X11
        self.game = False
        self.gamemode = False
        self.on_battery = False
        self.source = "none"
        self._debounce = 0
        self._hypr_conn = None
        self._gamemode_proxy = None
        self._upower_proxy = None

    def start(self):
        if hypr_socket_dir():
            self._start_hyprland()
        elif os.environ.get("SWAYSOCK") or os.environ.get("I3SOCK"):
            self._start_sway()
        elif os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            self._start_x11()
        self._start_gamemode()
        self._start_upower()

    def state(self, connector, cfg):
        return decide(connector, cfg, manual=self.manual, fullscreen=self.fullscreen, game=self.game,
                      gamemode=self.gamemode, on_battery=self.on_battery)

    def set_external_fullscreen(self, on):
        """The GNOME extension reports fullscreen apps (GNOME has no other way to ask)."""
        self._update(fullscreen=set(self.all_screens) if on else set())

    def set_manual(self, on):
        if self.manual != on:
            self.manual = on
            self.emit("changed")

    def _update(self, **values):
        changed = False
        for k, v in values.items():
            if getattr(self, k) != v:
                setattr(self, k, v)
                changed = True
        if changed:
            log.info("pause state: fullscreen=%s game=%s gamemode=%s battery=%s",
                     sorted(self.fullscreen), self.game, self.gamemode, self.on_battery)
            self.emit("changed")

    # -- Hyprland --
    def _start_hyprland(self):
        self._hypr_dir = hypr_socket_dir()
        self.source = "hyprland"
        self._hypr_refresh()
        try:
            client = Gio.SocketClient()
            self._hypr_conn = client.connect(Gio.UnixSocketAddress.new(os.path.join(self._hypr_dir, ".socket2.sock")), None)
            self._hypr_lines = Gio.DataInputStream.new(self._hypr_conn.get_input_stream())
            self._read_hypr_event()
        except GLib.Error as e:
            log.warning("Hyprland event socket unavailable (%s): fullscreen pausing off", e.message)

    def _read_hypr_event(self):
        self._hypr_lines.read_line_async(GLib.PRIORITY_DEFAULT, None, self._on_hypr_line)

    def _on_hypr_line(self, stream, result):
        try:
            line, _len = stream.read_line_finish_utf8(result)
        except GLib.Error as e:
            log.warning("Hyprland event socket closed: %s", e.message)
            return
        if line is None:
            log.warning("Hyprland event socket closed")
            return
        if line.split(">>", 1)[0] in HYPR_EVENTS:
            self._schedule(self._hypr_refresh)
        self._read_hypr_event()

    def _hypr_refresh(self):
        self._debounce = 0
        try:
            monitors = hypr_query(self._hypr_dir, "monitors")
            workspaces = hypr_query(self._hypr_dir, "workspaces")
            active = hypr_query(self._hypr_dir, "activewindow") or {}
        except (OSError, ValueError) as e:
            log.debug("Hyprland query failed: %s", e)
            return GLib.SOURCE_REMOVE
        self._update(fullscreen=hypr_fullscreen_monitors(monitors or [], workspaces or []),
                     game=is_game_class(active.get("class") if isinstance(active, dict) else None))
        return GLib.SOURCE_REMOVE

    def _schedule(self, fn):
        if not self._debounce:
            self._debounce = GLib.timeout_add(60, fn)

    # -- Sway / i3 --
    def _start_sway(self):
        try:
            import i3ipc
        except ImportError:
            log.warning("python-i3ipc not installed: fullscreen pausing off on Sway/i3")
            return
        self.source = "sway"
        self._i3ipc = i3ipc
        self._sway_refresh()

        def listen():
            try:
                conn = i3ipc.Connection(auto_reconnect=True)
                handler = lambda *_: GLib.idle_add(lambda: self._schedule(self._sway_refresh) and False)  # noqa: E731
                conn.on(i3ipc.Event.WINDOW, handler)
                conn.on(i3ipc.Event.WORKSPACE, handler)
                conn.on(i3ipc.Event.OUTPUT, handler)
                conn.main()
            except Exception as e:  # noqa: BLE001
                log.warning("Sway/i3 event listener stopped: %s", e)
        threading.Thread(target=listen, name="mural-sway", daemon=True).start()

    def _sway_refresh(self):
        self._debounce = 0
        try:
            conn = self._i3ipc.Connection()
            tree = conn.get_tree().ipc_data
            outputs = [o.ipc_data for o in conn.get_outputs()]
        except Exception as e:  # noqa: BLE001
            log.debug("Sway query failed: %s", e)
            return GLib.SOURCE_REMOVE
        full, cls = sway_state(tree, outputs)
        self._update(fullscreen=full, game=is_game_class(cls))
        return GLib.SOURCE_REMOVE

    # -- X11 (EWMH: XFCE, KDE on X11, Openbox, i3, ...) --
    def _start_x11(self):
        try:
            from Xlib import X, display as xdisplay
        except ImportError:
            log.warning("python-xlib not installed: fullscreen pausing off on X11")
            return
        self.source = "x11"

        def listen():
            try:
                d = xdisplay.Display()
                root = d.screen().root
                atoms = {n: d.intern_atom(n) for n in ("_NET_ACTIVE_WINDOW", "_NET_CLIENT_LIST_STACKING",
                                                      "_NET_WM_STATE", "_NET_WM_STATE_FULLSCREEN",
                                                      "_NET_WM_STATE_HIDDEN", "_NET_CURRENT_DESKTOP")}
                root.change_attributes(event_mask=X.PropertyChangeMask)
                watched = set()
                while True:
                    full, cls = self._x11_scan(d, root, atoms, watched, X)
                    GLib.idle_add(self._x11_apply, full, cls)
                    d.next_event()
                    while d.pending_events():             # coalesce bursts
                        d.next_event()
            except Exception as e:  # noqa: BLE001
                log.warning("X11 watcher stopped: %s", e)
        threading.Thread(target=listen, name="mural-x11", daemon=True).start()

    @staticmethod
    def _x11_scan(d, root, atoms, watched, X):
        def prop(win, name):
            try:
                p = win.get_full_property(atoms[name], X.AnyPropertyType)
                return list(p.value) if p else []
            except Exception:  # noqa: BLE001 - window went away
                return []
        full = []
        for wid in prop(root, "_NET_CLIENT_LIST_STACKING"):
            win = d.create_resource_object("window", wid)
            if wid not in watched:                          # hear about its state changes too
                try:
                    win.change_attributes(event_mask=X.PropertyChangeMask)
                    watched.add(wid)
                except Exception:  # noqa: BLE001
                    continue
            state = prop(win, "_NET_WM_STATE")
            if atoms["_NET_WM_STATE_FULLSCREEN"] in state and atoms["_NET_WM_STATE_HIDDEN"] not in state:
                try:
                    g = win.get_geometry()
                    t = win.translate_coords(root, 0, 0)
                    full.append((-t.x, -t.y, g.width, g.height))
                except Exception:  # noqa: BLE001
                    pass
        cls = None
        active = prop(root, "_NET_ACTIVE_WINDOW")
        if active and active[0]:
            try:
                wm = d.create_resource_object("window", active[0]).get_wm_class()
                cls = wm[1] if wm else None
            except Exception:  # noqa: BLE001
                pass
        return full, cls

    def _x11_apply(self, full, cls):
        self._update(fullscreen=x11_fullscreen_screens(full, self.screen_geometry), game=is_game_class(cls))
        return GLib.SOURCE_REMOVE

    # -- GameMode (session bus) --
    def _start_gamemode(self):
        Gio.DBusProxy.new_for_bus(Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None,
                                  "com.feralinteractive.GameMode", "/com/feralinteractive/GameMode",
                                  "com.feralinteractive.GameMode", None, self._on_gamemode_proxy)

    def _on_gamemode_proxy(self, _src, result):
        try:
            self._gamemode_proxy = Gio.DBusProxy.new_for_bus_finish(result)
        except GLib.Error as e:
            log.debug("GameMode unavailable: %s", e.message)
            return
        self._gamemode_proxy.connect("g-properties-changed", lambda *_: self._read_gamemode())
        self._read_gamemode()

    def _read_gamemode(self):
        v = self._gamemode_proxy.get_cached_property("ClientCount")
        self._update(gamemode=bool(v is not None and v.unpack() > 0))

    # -- UPower (system bus) --
    def _start_upower(self):
        Gio.DBusProxy.new_for_bus(Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None,
                                  "org.freedesktop.UPower", "/org/freedesktop/UPower",
                                  "org.freedesktop.UPower", None, self._on_upower_proxy)

    def _on_upower_proxy(self, _src, result):
        try:
            self._upower_proxy = Gio.DBusProxy.new_for_bus_finish(result)
        except GLib.Error as e:
            log.debug("UPower unavailable: %s", e.message)
            return
        self._upower_proxy.connect("g-properties-changed", lambda *_: self._read_upower())
        self._read_upower()

    def _read_upower(self):
        v = self._upower_proxy.get_cached_property("OnBattery")
        self._update(on_battery=bool(v is not None and v.unpack()))
