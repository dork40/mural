import json

from mural import config, library, palette


def test_defaults_when_missing(tmp_path):
    cfg = config.load(tmp_path / "nope.json")
    assert cfg == config.clean({})
    assert cfg["wallpaper"] == "shader:aurora" and cfg["monitors"] == {}


def test_bad_values_are_dropped(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"fps": "fast", "pause_on_game": "yes", "monitors": {"eDP-1": 3, "HDMI-A-1": "shader:nebula"},
                             "unknown": 1, "wallpaper": ""}))
    cfg = config.load(p)
    assert cfg["fps"] == 30 and cfg["pause_on_game"] is True
    assert cfg["monitors"] == {"HDMI-A-1": "shader:nebula"}
    assert cfg["wallpaper"] == "shader:aurora" and "unknown" not in cfg


def test_fps_is_clamped():
    assert config.clean({"fps": 1000})["fps"] == 144
    assert config.clean({"fps": 0})["fps"] == 5
    assert config.clean({"fps": True})["fps"] == 30      # bool is not a number here


def test_corrupt_file_falls_back(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("{not json")
    assert config.load(p) == config.clean({})


def test_save_roundtrip_is_atomic(tmp_path):
    p = tmp_path / "sub" / "c.json"
    cfg = config.clean({"wallpaper": "file:/x.mp4", "monitors": {"DP-1": "shader:liquid"}, "fps": 60})
    config.save(cfg, p)
    assert config.load(p) == cfg
    assert [f.name for f in p.parent.iterdir()] == ["c.json"]      # no temp files left behind


def test_wallpaper_for_screen():
    cfg = config.clean({"wallpaper": "shader:aurora", "monitors": {"DP-1": "shader:nebula"}})
    assert config.wallpaper_for(cfg, "DP-1") == "shader:nebula"
    assert config.wallpaper_for(cfg, "eDP-1") == "shader:aurora"


# -- palette ---------------------------------------------------------------------------
def test_hex_rgb():
    assert palette.hex_rgb("#ff0000") == (1.0, 0.0, 0.0)
    assert palette.hex_rgb("0f0") == (0.0, 1.0, 0.0)


def test_palette_prefers_rice_then_pywal(tmp_path):
    (tmp_path / ".cache/wal").mkdir(parents=True)
    (tmp_path / ".cache/wal/colors.json").write_text(json.dumps(
        {"special": {"background": "#000000"}, "colors": {f"color{i}": "#101010" for i in range(16)}}))
    assert palette.load(home=tmp_path).source == "pywal"
    (tmp_path / ".cache/rice-palette.json").write_text(json.dumps(
        {"pal": {"mauve": "cba6f7", "blue": "89b4fa", "pink": "f5c2e7", "crust": "11111b"}}))
    p = palette.load(home=tmp_path)
    assert p.source == "rice" and p.accent1 == palette.hex_rgb("cba6f7")


def test_palette_broken_file_is_skipped(tmp_path):
    (tmp_path / ".cache").mkdir()
    (tmp_path / ".cache/rice-palette.json").write_text("garbage")
    assert palette.load(home=tmp_path) == palette.DEFAULT
    assert palette.load("default", home=tmp_path) == palette.DEFAULT


# -- library ---------------------------------------------------------------------------
def test_builtin_shaders_are_listed():
    names = {w.ref: w.name for w in library.list_all(user_dir="/nonexistent", wallpapers_dir="/nonexistent")}
    assert {"shader:aurora", "shader:starfield", "shader:gradient", "shader:liquid", "shader:nebula"} <= set(names)
    assert names["shader:liquid"] == "Liquid Glass"


def test_user_shader_overrides_builtin(tmp_path):
    (tmp_path / "aurora.frag").write_text("// Name: My Aurora\nvoid main(){fragColor=vec4(1);}")
    wp = library.resolve("shader:aurora", user_dir=tmp_path)
    assert wp.name == "My Aurora" and not wp.builtin


def test_resolve_files(tmp_path):
    v = tmp_path / "rain_on-glass.MP4"
    v.write_bytes(b"x")
    i = tmp_path / "sky.webp"
    i.write_bytes(b"x")
    assert library.resolve(f"file:{v}").kind == "video"
    assert library.resolve(str(i)).kind == "image"
    assert library.resolve(f"file:{v}").name == "Rain On Glass"
    assert library.resolve(f"file:{tmp_path}/missing.mp4") is None
    assert library.resolve(f"file:{tmp_path}/notes.txt") is None


def test_shader_ids_cannot_escape(tmp_path):
    assert library.resolve("shader:../../etc/passwd") is None
    assert library.resolve("shader:") is None
    assert library.resolve("") is None


def test_user_wallpaper_folder_is_scanned(tmp_path):
    (tmp_path / "space").mkdir()
    (tmp_path / "space/earth.mp4").write_bytes(b"x")
    (tmp_path / "readme.txt").write_text("x")
    refs = [w.ref for w in library.list_all(user_dir="/nonexistent", wallpapers_dir=tmp_path)]
    assert f"file:{tmp_path}/space/earth.mp4" in refs
    assert not any(r.endswith("readme.txt") for r in refs)
