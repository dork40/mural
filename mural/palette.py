"""Colours for the shader wallpapers, taken from the desktop theme when there is one.

Sources, first match wins: the hyprland-rice palette (~/.cache/rice-palette.json), pywal
(~/.cache/wal/colors.json), then Mural's own default.
"""
import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


def hex_rgb(value):
    s = str(value).strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) < 6:
        raise ValueError(f"not a colour: {value!r}")
    return tuple(int(s[i:i + 2], 16) / 255 for i in (0, 2, 4))


@dataclass(frozen=True)
class Palette:
    accent1: tuple
    accent2: tuple
    accent3: tuple
    background: tuple
    source: str = "default"


DEFAULT = Palette(hex_rgb("6ee7d8"), hex_rgb("8b7cf6"), hex_rgb("4f9cf9"), hex_rgb("05060a"))


def candidates(home=None):
    home = Path(home) if home else Path.home()
    return [("rice", home / ".cache/rice-palette.json"), ("pywal", home / ".cache/wal/colors.json")]


def _from_rice(data):
    p = data["pal"]
    return Palette(hex_rgb(p["mauve"]), hex_rgb(p["blue"]), hex_rgb(p["pink"]), hex_rgb(p["crust"]), "rice")


def _from_pywal(data):
    c, special = data["colors"], data.get("special", {})
    return Palette(hex_rgb(c["color4"]), hex_rgb(c["color5"]), hex_rgb(c["color6"]),
                   hex_rgb(special.get("background", c["color0"])), "pywal")


def load(mode="auto", home=None):
    if mode != "auto":
        return DEFAULT
    for name, path in candidates(home):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return _from_rice(data) if name == "rice" else _from_pywal(data)
        except FileNotFoundError:
            continue
        except (OSError, ValueError, KeyError, TypeError) as e:
            log.info("palette %s ignored: %s", path, e)
    return DEFAULT
