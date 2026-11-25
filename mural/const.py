import os
from pathlib import Path

APP_ID = "io.github.dork40.Mural"
DAEMON_ID = "io.github.dork40.Mural.Daemon"
DAEMON_PATH = "/io/github/dork40/Mural/Daemon"
APP_NAME = "Mural"
VERSION = "0.1.1"

HERE = Path(__file__).resolve().parent
BUILTIN_SHADERS = HERE / "shaders"


def _xdg(var, fallback):
    return Path(os.environ.get(var) or Path.home() / fallback)


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config") / "mural"
DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share") / "mural"
STATE_DIR = _xdg("XDG_STATE_HOME", ".local/state") / "mural"
CACHE_DIR = _xdg("XDG_CACHE_HOME", ".cache") / "mural"
CONFIG_PATH = CONFIG_DIR / "config.json"
STATUS_PATH = STATE_DIR / "status.json"
USER_SHADERS = DATA_DIR / "shaders"
USER_WALLPAPERS = DATA_DIR / "wallpapers"

VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".m4v", ".avi"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".bmp", ".gif", ".heic", ".jxl", ".tif", ".tiff"}
SHADER_EXTS = {".frag", ".glsl"}


def ensure_dirs():
    for d in (CONFIG_DIR, DATA_DIR, STATE_DIR, CACHE_DIR, USER_SHADERS, USER_WALLPAPERS):
        d.mkdir(parents=True, exist_ok=True)
