"""Videos Mural offers to download: short, seamlessly looping clips cut from NASA's public-domain
4K footage of Earth from the Space Station. Hosted on Mural's GitHub releases."""
import hashlib
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import const

BASE = "https://github.com/dork40/mural/releases/download/videos-1"
FOLDER = "NASA"            # inside ~/.local/share/mural/wallpapers
CREDIT = "NASA, Earth in 4K: Expedition 65 (public domain)"


@dataclass(frozen=True)
class Clip:
    file: str
    name: str
    about: str
    size: int          # bytes, for the progress bar
    sha256: str

    @property
    def url(self):
        return f"{BASE}/{self.file}"

    @property
    def thumb_url(self):
        return f"{BASE}/{Path(self.file).stem}.jpg"

    @property
    def path(self):
        return const.USER_WALLPAPERS / FOLDER / self.file


def _load():
    """The list ships as videos.json next to this file (written when the clips are released)."""
    import json
    p = Path(__file__).resolve().parent / "videos.json"
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return []
    return [Clip(**c) for c in data]


CLIPS = _load()


def installed(clip):
    return clip.path.exists()


def download(clip, progress=None):
    """Fetch a clip into the wallpapers folder. progress(done, total) is called as it goes.
    The file only appears under its real name once it's complete and its checksum matches."""
    clip.path.parent.mkdir(parents=True, exist_ok=True)
    tmp = clip.path.with_suffix(clip.path.suffix + ".part")
    h = hashlib.sha256()
    done = 0
    req = urllib.request.Request(clip.url, headers={"User-Agent": f"Mural/{const.VERSION}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or clip.size or 0)
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        if clip.sha256 and h.hexdigest() != clip.sha256:
            raise IOError("the download was damaged, try again")
        os.replace(tmp, clip.path)
    finally:
        tmp.unlink(missing_ok=True)
    return clip.path


def thumbnail(clip):
    """A small preview picture for a clip that isn't downloaded yet (cached)."""
    out = const.CACHE_DIR / "catalog" / f"{Path(clip.file).stem}.jpg"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(clip.thumb_url, headers={"User-Agent": f"Mural/{const.VERSION}"})
        with urllib.request.urlopen(req, timeout=20) as r:
            out.write_bytes(r.read())
        return out
    except OSError:
        return None
