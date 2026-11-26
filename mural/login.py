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
    """What the login screen is set to now: {"theme", "mural", "wallpaper", "full_refresh"}."""
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
            "full_refresh": DISPLAY_CONF.exists()}


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


def apply_command(theme_dir, full_refresh=True):
    helper = helper_path()
    if helper is None:
        raise RuntimeError("Mural's login-screen helper is missing; reinstall Mural")
    return ["pkexec", str(helper), "apply", str(theme_dir), "1" if full_refresh else "0"]


def restore_command():
    helper = helper_path()
    if helper is None:
        raise RuntimeError("Mural's login-screen helper is missing; reinstall Mural")
    return ["pkexec", str(helper), "restore"]
