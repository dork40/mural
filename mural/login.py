"""Mural on the login screen (SDDM).

Mural writes its own SDDM theme, "mural": the same clock-and-password layout as the rice's login
screen, with one of Mural's animated wallpapers drawn live behind it by the GPU (so it runs at the
screen's own refresh rate). Changing the system needs root, so the app hands a finished theme folder
to a small helper (mural-login-helper) through pkexec, which asks for your password.

The parts here are plain functions so they can be tested without a GPU or root."""
import configparser
import glob
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import const

THEME_NAME = "mural"
THEMES_DIR = Path("/usr/share/sddm/themes")
THEME_DIR = THEMES_DIR / THEME_NAME
CONF_DIR = Path("/etc/sddm.conf.d")
THEME_CONF = CONF_DIR / "zz-mural-theme.conf"         # "zz" so SDDM reads it last and it wins over other Current= lines
DISPLAY_CONF = CONF_DIR / "zz-mural-display.conf"
STATE = Path("/var/lib/mural/login-previous-theme")
HELPER_NAME = "mural-login-helper"
QSB_CANDIDATES = ["/usr/lib/qt6/bin/qsb", "/usr/lib64/qt6/bin/qsb", "/usr/lib/x86_64-linux-gnu/qt6/bin/qsb",
                  "/usr/libexec/qt6/qsb", "qsb6", "qsb"]
TEMPLATE_DIR = Path(__file__).resolve().parent / "login"

QUALITY = {"sharp": 1.0, "balanced": 0.5, "light": 0.35}


# -- what's on this machine ------------------------------------------------------------------
def sddm_installed():
    return shutil.which("sddm") is not None or Path("/usr/bin/sddm").exists()


def sddm_active():
    """True if SDDM is the display manager that starts at boot."""
    try:
        target = os.path.realpath("/etc/systemd/system/display-manager.service")
        return os.path.basename(target) == "sddm.service"
    except OSError:
        return False


def qsb_path():
    for c in QSB_CANDIDATES:
        p = c if os.path.isabs(c) else shutil.which(c)
        if p and os.access(p, os.X_OK):
            return p
    return None


def helper_path():
    """The root helper (installed by the package or install.sh)."""
    # only ever a root-owned copy: pkexec runs it as root
    for p in (Path("/usr/lib/mural") / HELPER_NAME, Path("/usr/local/lib/mural") / HELPER_NAME):
        if p.exists():
            return p
    return None


def _read_sddm_conf():
    """All SDDM settings, merged the way SDDM merges them (later files win)."""
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = lambda o: o  # keep key case (Current, DisplayServer...)
    files = sorted(glob.glob("/usr/lib/sddm/sddm.conf.d/*.conf")) + ["/etc/sddm.conf"] + \
        sorted(glob.glob(str(CONF_DIR / "*.conf")))
    for f in files:
        try:
            cp.read(f)
        except (configparser.Error, OSError, UnicodeDecodeError):
            continue
    return cp


def current_state():
    """What the login screen is set to now:
    {"theme", "mural", "wallpaper", "full_refresh", "mode"}.

    "mode" is None when the login screen is left on its default 60 Hz, "auto" when it uses each
    screen's best mode, or a "WIDTHxHEIGHT@RATE" string when a specific mode was chosen."""
    cp = _read_sddm_conf()
    theme = cp.get("Theme", "Current", fallback="")
    wallpaper = None
    if theme == THEME_NAME:
        tc = configparser.ConfigParser(interpolation=None)
        try:
            tc.read(THEME_DIR / "theme.conf")
            wallpaper = tc.get("General", "wallpaper", fallback=None)
        except (configparser.Error, OSError):
            pass
    return {"theme": theme or "default", "mural": theme == THEME_NAME, "wallpaper": wallpaper,
            "full_refresh": DISPLAY_CONF.exists(), "mode": _configured_mode(cp)}


def _configured_mode(cp):
    """Read the login-screen mode back from SDDM's DisplayCommand, or None if it's the default."""
    if not DISPLAY_CONF.exists():
        return None
    cmd = cp.get("X11", "DisplayCommand", fallback="")
    parts = cmd.split()
    if len(parts) >= 3 and "x" in parts[1]:        # /usr/lib/mural/sddm-xsetup 1920x1200 165
        return f"{parts[1]}@{parts[2]}"
    return "auto"


# -- enumerating the screen's real modes ----------------------------------------------------
def _parse_rate(text):
    try:
        return round(float(text.strip().rstrip("Hz*+ ")), 2)
    except ValueError:
        return None


def _modes_from_hyprctl():
    """availableModes from Hyprland, e.g. '1920x1200@165.00Hz'."""
    import json
    try:
        out = subprocess.run(["hyprctl", "-j", "monitors"], capture_output=True, text=True,
                             timeout=5).stdout
        mons = json.loads(out)
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    modes = []
    for m in mons:
        for s in m.get("availableModes", []):
            try:
                res, rate = s.split("@")
                w, h = res.split("x")
                r = _parse_rate(rate)
                if r:
                    modes.append((int(w), int(h), r))
            except (ValueError, TypeError):
                continue
    return modes


def _modes_from_xrandr():
    """Every mode xrandr lists for connected outputs (a real X session only)."""
    try:
        out = subprocess.run(["xrandr", "--query"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    modes, on = [], False
    for line in out.splitlines():
        if " connected" in line:
            on = True
            continue
        if line and not line[0].isspace():
            on = False
            continue
        if not on:
            continue
        parts = line.split()
        if not parts or "x" not in parts[0]:
            continue
        try:
            w, h = parts[0].split("x")
            w, h = int(w), int(h)
        except ValueError:
            continue
        for tok in parts[1:]:
            r = _parse_rate(tok)
            if r:
                modes.append((w, h, r))
    return modes


def available_modes():
    """A sorted, de-duplicated list of (width, height, rate) the login screen could use.

    Tries Hyprland, then a real X session (xrandr); returns [] when neither can be asked (the
    app still offers the automatic best-mode option in that case)."""
    modes = _modes_from_hyprctl() or _modes_from_xrandr()
    seen, uniq = set(), []
    for w, h, r in modes:
        key = (w, h, r)
        if key not in seen:
            seen.add(key)
            uniq.append(key)
    # biggest screen first, then fastest refresh
    uniq.sort(key=lambda m: (m[0] * m[1], m[2]), reverse=True)
    return uniq


# -- shaders: Mural's GLSL -> Qt's ShaderEffect GLSL ---------------------------------------------
_QT_HEADER = """#version 440
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    float iTime;
    vec2 muralRes;
    vec4 muralAccent1;
    vec4 muralAccent2;
    vec4 muralAccent3;
    vec4 muralBackground;
};
#define iResolution vec3(muralRes, 1.0)
#define uAccent1 muralAccent1.rgb
#define uAccent2 muralAccent2.rgb
#define uAccent3 muralAccent3.rgb
#define uBackground muralBackground.rgb
vec4 muralFragCoord;
"""

_QT_MAIN = """
void main() {
    // Mural shaders count pixels from the bottom-left, like Shadertoy; Qt's texture runs top-down
    muralFragCoord = vec4(qt_TexCoord0.x * muralRes.x, (1.0 - qt_TexCoord0.y) * muralRes.y, 0.0, 1.0);
    mural_main();
    fragColor = vec4(fragColor.rgb, 1.0) * qt_Opacity;
}
"""


def to_qt_shader(src):
    """Turn a Mural wallpaper shader into one Qt's ShaderEffect can run."""
    lines = []
    for line in src.splitlines():
        s = line.strip()
        if s.startswith("#version") or re.match(r"precision\s+\w+\s+\w+\s*;", s):
            continue
        lines.append(line)
    body = "\n".join(lines)
    body = re.sub(r"\bgl_FragCoord\b", "muralFragCoord", body)
    body = re.sub(r"\bvoid\s+main\s*\(\s*(void)?\s*\)", "void mural_main()", body, count=1)
    return _QT_HEADER + body + "\n" + _QT_MAIN


def compile_qt_shader(src, out_path, qsb=None):
    """Write <out_path> (a .qsb) from a Mural shader. Returns None, or the compiler's complaint."""
    qsb = qsb or qsb_path()
    if not qsb:
        return "Qt's shader tool (qsb, in qt6-shadertools) isn't installed"
    out_path = Path(out_path)
    frag = out_path.with_suffix("")          # aurora.frag.qsb -> aurora.frag
    frag.write_text(to_qt_shader(src))
    r = subprocess.run([qsb, "--glsl", "100 es,120,150", "-o", str(out_path), str(frag)],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return (r.stderr or r.stdout).strip() or "the shader didn't compile"
    return None


# -- building the theme folder ----------------------------------------------------------------
def _hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c * 255))) for c in rgb)


def build_theme(wallpaper, palette, quality="balanced", dest=None):
    """Put a complete SDDM theme folder together (as your user, in the cache) and return its path.
    `wallpaper` is a library.Wallpaper: animated (shader), video or image."""
    dest = Path(dest or (const.CACHE_DIR / "login-theme"))
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(TEMPLATE_DIR, dest)
    extra = ""
    if wallpaper.kind == "shader":
        err = compile_qt_shader(wallpaper.path.read_text(), dest / "background.frag.qsb")
        if err:
            raise RuntimeError(err)
    elif wallpaper.kind == "video":
        name = "background" + wallpaper.path.suffix.lower()
        shutil.copyfile(wallpaper.path, dest / name)
        extra = f"video={name}\n"
    else:
        name = "wallpaper" + wallpaper.path.suffix.lower()
        shutil.copyfile(wallpaper.path, dest / name)
        extra = f"backgroundImage={name}\n"
    scale = QUALITY.get(quality, 0.5)
    (dest / "theme.conf").write_text(
        "[General]\n"
        f"kind={wallpaper.kind}\nwallpaper={wallpaper.name}\n{extra}"
        f"accent={_hex(palette.accent1)}\naccent2={_hex(palette.accent2)}\n"
        f"accent3={_hex(palette.accent3)}\nbase={_hex(palette.background)}\n"
        "font=JetBrainsMono Nerd Font\n"
        f"renderScale={scale}\n")
    return dest


def apply_command(theme_dir, mode="auto"):
    """mode: "off", "auto", or "1920x1200@165" / "1920x1200@max"."""
    helper = helper_path()
    if helper is None:
        raise RuntimeError("Mural's login-screen helper is missing; reinstall Mural")
    if mode is True:
        mode = "auto"
    elif mode is False:
        mode = "off"
    return ["pkexec", str(helper), "apply", str(theme_dir), mode]


# -- screen modes the login screen can use -----------------------------------------------------
LOGIN_DISPLAY = Path("/etc/mural/login-display")


def _modes_hyprland():
    import json
    out = subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True, timeout=3).stdout
    modes = {}
    for m in json.loads(out)[:1]:
        for s in m.get("availableModes", []):
            res, _, hz = s.partition("@")
            w, h = res.split("x")
            modes.setdefault((int(w), int(h)), set()).add(round(float(hz.rstrip("Hz"))))
    return modes


def _modes_xrandr():
    out = subprocess.run(["xrandr", "--query"], capture_output=True, text=True, timeout=3).stdout
    modes, on = {}, False
    for line in out.splitlines():
        if " connected" in line:
            if modes:
                break
            on = True
            continue
        if not line.startswith(" ") or not on:
            on = on and line.startswith(" ")
            continue
        parts = line.split()
        if "x" not in parts[0]:
            continue
        w, h = parts[0].split("x")[:2]
        rates = {round(float(r.strip("*+"))) for r in parts[1:] if r.strip("*+").replace(".", "").isdigit()}
        modes.setdefault((int(w), int(h.rstrip("i"))), set()).update(rates)
    return modes


def _modes_kscreen():
    import json
    out = subprocess.run(["kscreen-doctor", "-j"], capture_output=True, text=True, timeout=3).stdout
    modes = {}
    for o in json.loads(out).get("outputs", []):
        if o.get("connected") and o.get("enabled"):
            for m in o.get("modes", []):
                modes.setdefault((m["size"]["width"], m["size"]["height"]), set()).add(round(m["refreshRate"]))
            break
    return modes


def screen_modes():
    """The first screen's modes, as [((w, h), [rates high to low]), ...], biggest first."""
    import shutil
    for tool, fn in (("hyprctl", _modes_hyprland), ("kscreen-doctor", _modes_kscreen), ("xrandr", _modes_xrandr)):
        if shutil.which(tool):
            try:
                modes = fn()
            except (OSError, ValueError, KeyError, subprocess.SubprocessError):
                continue
            if modes:
                return sorted(((k, sorted(v, reverse=True)) for k, v in modes.items()),
                              key=lambda kv: (-kv[0][0] * kv[0][1], -kv[0][0]))
    return []


def login_display():
    """What the login screen's mode is set to: "off", "auto", or (w, h, rate|"max")."""
    if not DISPLAY_CONF.exists():
        return "off"
    try:
        words = LOGIN_DISPLAY.read_text().split()
    except OSError:
        return "auto"
    if len(words) == 2 and "x" in words[0]:
        w, h = words[0].split("x")
        return (int(w), int(h), words[1] if words[1] == "max" else round(float(words[1])))
    return "auto"


def restore_command():
    helper = helper_path()
    if helper is None:
        raise RuntimeError("Mural's login-screen helper is missing; reinstall Mural")
    return ["pkexec", str(helper), "restore"]
