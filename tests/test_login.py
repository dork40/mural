import shutil

import pytest

from mural import catalog, const, library, login, palette

SHADERS = sorted(const.BUILTIN_SHADERS.glob("*.frag"))


def test_qt_shader_rewrites_the_mural_parts():
    src = "#version 300 es\nprecision highp float;\nvoid main(){ vec2 p = gl_FragCoord.xy / iResolution.xy; fragColor = vec4(uAccent1, 1.0); }"
    out = login.to_qt_shader(src)
    assert out.startswith("#version 440") and out.count("#version") == 1
    assert "gl_FragCoord" not in out and "precision highp" not in out
    assert "void mural_main()" in out and out.rstrip().endswith("}")


@pytest.mark.skipif(not login.qsb_path(), reason="qsb (qt6-shadertools) not installed")
@pytest.mark.parametrize("shader", SHADERS, ids=[s.stem for s in SHADERS])
def test_every_builtin_compiles_for_the_login_screen(tmp_path, shader):
    assert login.compile_qt_shader(shader.read_text(), tmp_path / "bg.frag.qsb") is None
    assert (tmp_path / "bg.frag.qsb").stat().st_size > 1000


@pytest.mark.skipif(not login.qsb_path(), reason="qsb (qt6-shadertools) not installed")
def test_build_theme_for_each_kind(tmp_path):
    pal = palette.DEFAULT
    shader = library.resolve("shader:aurora")
    d = login.build_theme(shader, pal, "light", dest=tmp_path / "s")
    conf = (d / "theme.conf").read_text()
    assert "kind=shader" in conf and "renderScale=0.35" in conf and (d / "background.frag.qsb").exists()
    for name in ("Main.qml", "metadata.desktop", "background.png"):
        assert (d / name).exists()

    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"x")
    d = login.build_theme(library.resolve(f"file:{vid}"), pal, dest=tmp_path / "v")
    assert "kind=video" in (d / "theme.conf").read_text() and (d / "background.mp4").exists()

    img = tmp_path / "pic.webp"
    img.write_bytes(b"x")
    d = login.build_theme(library.resolve(f"file:{img}"), pal, dest=tmp_path / "i")
    assert "backgroundImage=wallpaper.webp" in (d / "theme.conf").read_text()


def test_helper_only_copies_known_files():
    text = (const.HERE.parent / "data" / "mural-login-helper").read_text()
    assert "install -m644" in text and "cp -r" not in text


def test_catalog_entries_are_complete():
    for c in catalog.CLIPS:
        assert c.file.endswith(".mp4") and c.name and c.size > 1_000_000 and len(c.sha256) == 64
        assert c.url.startswith("https://github.com/dork40/mural/releases/download/")
