"""Building complete GLSL programs from a wallpaper's fragment shader (no GL calls here)."""

ES_HEADER = "#version 300 es\nprecision highp float;\nprecision highp int;\n"
CORE_HEADER = "#version 330 core\n"

# Everything a wallpaper shader can use. Shadertoy-style names, so ported shaders feel familiar.
UNIFORMS = """uniform float iTime;        // seconds of animation (stops while paused)
uniform vec3 iResolution;   // pixels: width, height, 1
uniform vec3 uAccent1;      // desktop theme colours, 0..1
uniform vec3 uAccent2;
uniform vec3 uAccent3;
uniform vec3 uBackground;
out vec4 fragColor;
"""

VERTEX_BODY = """const vec2 P[3] = vec2[3](vec2(-1.0, -1.0), vec2(3.0, -1.0), vec2(-1.0, 3.0));
void main() { gl_Position = vec4(P[gl_VertexID], 0.0, 1.0); }
"""

UNIFORM_NAMES = ("iTime", "iResolution", "uAccent1", "uAccent2", "uAccent3", "uBackground")


def header(use_es):
    return ES_HEADER if use_es else CORE_HEADER


def render_scale(src):
    """`// Scale: 0.5` near the top lets a soft shader draw at reduced resolution (clamped 0.25..1)."""
    import re
    for line in src.splitlines()[:8]:
        m = re.match(r"\s*//\s*scale\s*:\s*([0-9.]+)", line, re.I)
        if m:
            try:
                return min(1.0, max(0.25, float(m.group(1))))
            except ValueError:
                return 1.0
    return 1.0


def strip_version(src):
    """Wallpaper shaders don't need a #version line; drop one if present (we add the right one)."""
    return "\n".join("" if line.strip().startswith("#version") else line for line in src.splitlines())


def vertex_source(use_es):
    return header(use_es) + VERTEX_BODY


def fragment_source(body, use_es):
    return header(use_es) + UNIFORMS + "#line 1\n" + strip_version(body) + "\n"
