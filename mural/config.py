"""~/.config/mural/config.json: what plays where, and when Mural pauses itself."""
import json
import logging
import os
import tempfile

from . import const

log = logging.getLogger(__name__)

DEFAULTS = {
    "wallpaper": "shader:aurora",  # used on every screen without its own choice
    "monitors": {},                # connector name (e.g. "eDP-1") -> wallpaper
    "fps": 30,                     # shader frame rate cap (videos play at their own rate)
    "colors": "auto",              # auto (follow the desktop theme) | default
    "pause_on_fullscreen": True,   # a fullscreen window covers that screen
    "pause_on_game": True,         # a game is focused anywhere
    "pause_on_gamemode": True,     # Feral GameMode is active
    "pause_on_battery": False,     # running on battery
}

FPS_RANGE = (5, 144)


def clean(data):
    """Merge user data over the defaults, dropping unknown keys and wrong types."""
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    if not isinstance(data, dict):
        return out
    for key, default in DEFAULTS.items():
        if key not in data:
            continue
        value = data[key]
        if isinstance(default, bool):
            if isinstance(value, bool):
                out[key] = value
        elif isinstance(default, int):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                out[key] = int(min(max(value, FPS_RANGE[0]), FPS_RANGE[1])) if key == "fps" else int(value)
        elif isinstance(default, dict):
            if isinstance(value, dict):
                out[key] = {str(k): str(v) for k, v in value.items() if isinstance(v, str) and v}
        elif isinstance(default, str):
            if isinstance(value, str) and value:
                out[key] = value
    return out


def load(path=None):
    path = path or const.CONFIG_PATH
    try:
        with open(path, encoding="utf-8") as f:
            return clean(json.load(f))
    except FileNotFoundError:
        return clean({})
    except (OSError, ValueError) as e:
        log.warning("config %s unreadable (%s): using defaults", path, e)
        return clean({})


def save(cfg, path=None):
    """Write atomically, so a crash never leaves half a config."""
    path = path or const.CONFIG_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".config-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(clean(cfg), f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def wallpaper_for(cfg, connector):
    return cfg["monitors"].get(connector) or cfg["wallpaper"]
