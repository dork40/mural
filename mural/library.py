"""What can be shown, and how a wallpaper reference ("shader:aurora", "file:/x.mp4") resolves."""
import re
from dataclasses import dataclass
from pathlib import Path

from . import const


@dataclass(frozen=True)
class Wallpaper:
    ref: str       # what the config stores
    kind: str      # "shader" | "video" | "image"
    name: str      # shown to people
    path: Path
    builtin: bool = False


def kind_of(path):
    ext = Path(path).suffix.lower()
    if ext in const.SHADER_EXTS:
        return "shader"
    if ext in const.VIDEO_EXTS:
        return "video"
    if ext in const.IMAGE_EXTS:
        return "image"
    return None


def shader_name(path):
    """`// Name: Aurora` on one of the first lines, else the file name."""
    try:
        with open(path, encoding="utf-8") as f:
            for _ in range(5):
                m = re.match(r"\s*//\s*name\s*:\s*(.+)", f.readline(), re.I)
                if m:
                    return m.group(1).strip()
    except OSError:
        pass
    return pretty(path)


def pretty(path):
    return re.sub(r"[-_]+", " ", Path(path).stem).strip().title() or Path(path).name


def _shader_dirs(user_dir=None, builtin_dir=None):
    return [(Path(user_dir or const.USER_SHADERS), False), (Path(builtin_dir or const.BUILTIN_SHADERS), True)]


def resolve(ref, user_dir=None, builtin_dir=None):
    """Turn a reference into a Wallpaper, or None if it doesn't exist (any more)."""
    if not ref:
        return None
    if ref.startswith("shader:"):
        sid = ref[len("shader:"):]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", sid):
            return None
        for d, builtin in _shader_dirs(user_dir, builtin_dir):   # a user shader may override a built-in one
            for ext in sorted(const.SHADER_EXTS):
                p = d / f"{sid}{ext}"
                if p.is_file():
                    return Wallpaper(ref, "shader", shader_name(p), p, builtin)
        return None
    path = Path(ref[len("file:"):] if ref.startswith("file:") else ref).expanduser()
    kind = kind_of(path)
    if kind is None or not path.is_file():
        return None
    return Wallpaper(f"file:{path}", kind, pretty(path), path)


def ref_for_path(path):
    path = Path(path).expanduser().resolve()
    if kind_of(path) == "shader":
        return None   # shaders are referenced by id from a shader folder
    return f"file:{path}"


def list_all(user_dir=None, builtin_dir=None, wallpapers_dir=None):
    seen, out = set(), []
    for d, builtin in _shader_dirs(user_dir, builtin_dir):
        if d.is_dir():
            for p in sorted(d.iterdir()):
                if p.suffix.lower() in const.SHADER_EXTS and p.stem not in seen:
                    seen.add(p.stem)
                    out.append(Wallpaper(f"shader:{p.stem}", "shader", shader_name(p), p, builtin))
    wd = Path(wallpapers_dir or const.USER_WALLPAPERS)
    if wd.is_dir():
        for p in sorted(wd.rglob("*")):
            k = kind_of(p)
            if p.is_file() and k in ("video", "image"):
                out.append(Wallpaper(f"file:{p}", k, pretty(p), p))
    return out
