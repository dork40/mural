"""Every built-in shader must compile as both desktop GL 3.3 and GLES 3.0 (checked with the
Khronos reference compiler, so no GPU is needed)."""
import shutil
import subprocess

import pytest

from mural import const, shaderlib

GLSLANG = shutil.which("glslangValidator")
SHADERS = sorted(const.BUILTIN_SHADERS.glob("*.frag"))


def _check(tmp_path, name, src):
    p = tmp_path / name
    p.write_text(src)
    r = subprocess.run([GLSLANG, str(p)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.skipif(not GLSLANG, reason="glslangValidator not installed")
@pytest.mark.parametrize("use_es", [True, False], ids=["gles", "gl"])
def test_vertex_shader(tmp_path, use_es):
    _check(tmp_path, "v.vert", shaderlib.vertex_source(use_es))


@pytest.mark.skipif(not GLSLANG, reason="glslangValidator not installed")
@pytest.mark.parametrize("use_es", [True, False], ids=["gles", "gl"])
@pytest.mark.parametrize("shader", SHADERS, ids=[s.stem for s in SHADERS])
def test_builtin_shader_compiles(tmp_path, shader, use_es):
    _check(tmp_path, "f.frag", shaderlib.fragment_source(shader.read_text(), use_es))


def test_there_are_builtin_shaders():
    assert len(SHADERS) >= 5


def test_version_lines_are_replaced():
    src = shaderlib.fragment_source("#version 450\nvoid main(){fragColor=vec4(1.0);}", True)
    assert src.count("#version") == 1 and src.startswith("#version 300 es")
