"""`mural` command line: start/stop the wallpaper daemon and choose what plays."""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import config, const, library

USAGE_EXAMPLES = """examples:
  mural start                       start the wallpaper (also in your autostart)
  mural set aurora                  a built-in shader wallpaper
  mural set ~/Videos/rain.mp4       any video, GIF or image
  mural set nebula --screen HDMI-A-1   only on one screen
  mural list                        everything you can choose
  mural pause / resume / toggle     freeze or unfreeze the animation
  mural status                      what is playing, and whether it is paused (and why)
"""


def _bus():
    from gi.repository import Gio
    return Gio.bus_get_sync(Gio.BusType.SESSION, None)


def daemon_running():
    try:
        from gi.repository import GLib
        reply = _bus().call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                 "NameHasOwner", GLib.Variant("(s)", (const.DAEMON_ID,)),
                                 GLib.VariantType("(b)"), 0, 2000, None)
        return reply.unpack()[0]
    except Exception:  # noqa: BLE001
        return False


def daemon_action(name):
    from gi.repository import GLib
    _bus().call_sync(const.DAEMON_ID, const.DAEMON_PATH, "org.gtk.Actions", "Activate",
                     GLib.Variant("(sava{sv})", (name, [], {})), None, 0, 3000, None)


def start_daemon(wait=6.0):
    if daemon_running():
        return True
    log_path = const.STATE_DIR / "daemon.log"
    const.STATE_DIR.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    pkg_root = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (pkg_root, env.get("PYTHONPATH")) if p)
    with open(log_path, "ab") as log:
        subprocess.Popen([sys.executable, "-m", "mural.daemon"], stdin=subprocess.DEVNULL, stdout=log,
                         stderr=log, env=env, start_new_session=True, close_fds=True)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if daemon_running():
            return True
        time.sleep(0.1)
    return False


def resolve_choice(text):
    """What the user typed -> a wallpaper reference, or None."""
    if text.startswith(("shader:", "file:")):
        return text if library.resolve(text) else None
    p = Path(text).expanduser()
    if p.exists():
        if library.kind_of(p) == "shader":
            return None
        return library.ref_for_path(p) if library.kind_of(p) else None
    shader = library.resolve(f"shader:{text}")
    if shader:
        return shader.ref
    wanted = text.lower().replace(" ", "")
    for wp in library.list_all():                     # also accept the display name: "liquid glass"
        if wp.name.lower().replace(" ", "") == wanted:
            return wp.ref
    return None


def read_status():
    try:
        return json.loads(const.STATUS_PATH.read_text())
    except (OSError, ValueError):
        return None


def cmd_start(args):
    if daemon_running():
        print("Mural is already running.")
        return 0
    if start_daemon():
        print("Mural started.")
        return 0
    print(f"Mural didn't start. See {const.STATE_DIR / 'daemon.log'}", file=sys.stderr)
    return 1


def cmd_stop(args):
    if not daemon_running():
        print("Mural isn't running.")
        return 0
    daemon_action("quit")
    for _ in range(50):
        if not daemon_running():
            print("Mural stopped.")
            return 0
        time.sleep(0.1)
    print("Mural didn't stop in time.", file=sys.stderr)
    return 1


def cmd_set(args):
    ref = resolve_choice(args.wallpaper)
    if ref is None:
        print(f"Can't find \"{args.wallpaper}\". Try `mural list`, or give a path to a video, GIF or image.",
              file=sys.stderr)
        return 1
    cfg = config.load()
    if args.screen:
        cfg["monitors"][args.screen] = ref
    else:
        cfg["wallpaper"] = ref
        cfg["monitors"] = {}
    config.save(cfg)
    if daemon_running():
        daemon_action("reload")
    elif not start_daemon():
        print(f"Saved, but Mural didn't start. See {const.STATE_DIR / 'daemon.log'}", file=sys.stderr)
        return 1
    name = library.resolve(ref).name
    print(f"Now showing {name}" + (f" on {args.screen}." if args.screen else "."))
    return 0


def cmd_list(args):
    cfg = config.load()
    current = set([cfg["wallpaper"], *cfg["monitors"].values()])
    for wp in library.list_all():
        short = wp.ref.split(":", 1)[1] if wp.kind == "shader" else str(wp.path)
        mark = "*" if wp.ref in current else " "
        print(f"{mark} {wp.name:<16} {wp.kind:<7} {short}")
    return 0


def cmd_simple(action):
    def run(args):
        if not daemon_running():
            print("Mural isn't running (start it with `mural start`).", file=sys.stderr)
            return 1
        daemon_action(action)
        return 0
    return run


def cmd_status(args):
    if not daemon_running():
        print("Mural isn't running.")
        return 1
    st = None
    for _ in range(20):
        st = read_status()
        if st:
            break
        time.sleep(0.1)
    if not st:
        print("Mural is running (no status yet).")
        return 0
    print(f"Mural {st['version']} running ({st['backend']}), colours from {st['colors']}")
    for conn, s in st["screens"].items():
        wp = library.resolve(s["wallpaper"]) if s["wallpaper"] else None
        state = f"paused ({s['reason']})" if s["paused"] else "playing"
        err = f"  ERROR: {s['error']}" if s.get("error") else ""
        print(f"  {conn}: {wp.name if wp else s['wallpaper']} - {state}{err}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mural", description="Live wallpapers for every Linux desktop.",
                                 epilog=USAGE_EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"Mural {const.VERSION}")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("start", help="start the wallpaper").set_defaults(func=cmd_start)
    sub.add_parser("stop", help="stop it").set_defaults(func=cmd_stop)
    p = sub.add_parser("set", help="choose a wallpaper")
    p.add_argument("wallpaper", help="a built-in name (see `mural list`) or a file")
    p.add_argument("--screen", "--monitor", help="only this screen, e.g. eDP-1 or HDMI-A-1")
    p.set_defaults(func=cmd_set)
    sub.add_parser("list", help="show everything you can choose").set_defaults(func=cmd_list)
    for name in ("pause", "resume", "toggle"):
        sub.add_parser(name, help=f"{name} the animation").set_defaults(func=cmd_simple(name))
    sub.add_parser("status", help="what is playing and whether it is paused").set_defaults(func=cmd_status)
    sub.add_parser("snapshot", help="save each screen's current frame to ~/.cache/mural").set_defaults(
        func=cmd_simple("snapshot"))
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
