// Name: Liquid Glass
// Soft glass shapes that drift, merge and split, catching light along their edges.

vec2 dropPos(int i, float t) {
    float fi = float(i);
    return vec2(sin(t * (0.21 + fi * 0.037) + fi * 1.9) * (0.62 + 0.1 * sin(fi)),
                cos(t * (0.17 + fi * 0.031) + fi * 2.7) * 0.38);
}

float field(vec2 p, float t) {
    float v = 0.0;
    for (int i = 0; i < 7; i++) {
        vec2 d = p - dropPos(i, t);
        float r = 0.022 + 0.012 * sin(float(i) * 3.1);
        v += r / dot(d, d);
    }
    return v;
}

vec3 backdrop(vec2 p) {
    vec3 c = mix(uBackground * 1.15, uBackground + uAccent2 * 0.12, smoothstep(-0.7, 0.7, p.y + 0.3 * p.x));
    c += uAccent1 * 0.12 * exp(-dot(p - vec2(-0.55, 0.3), p - vec2(-0.55, 0.3)) * 2.5);
    c += uAccent3 * 0.10 * exp(-dot(p - vec2(0.65, -0.25), p - vec2(0.65, -0.25)) * 2.5);
    return c;
}

void main() {
    vec2 p = (gl_FragCoord.xy - 0.5 * iResolution.xy) / iResolution.y;
    float t = iTime * 0.3;
    float f = field(p, t);
    float e = 1.0 / iResolution.y;
    vec2 g = vec2(field(p + vec2(e, 0.0), t) - field(p - vec2(e, 0.0), t),
                  field(p + vec2(0.0, e), t) - field(p - vec2(0.0, e), t)) / (2.0 * e);

    float edge = 1.0;
    float inside = smoothstep(edge - 0.04, edge + 0.04, f);
    // how close to the rim: 1 at the edge, fading toward the middle
    float rim = exp(-(f - edge) * 2.2) * inside;
    vec2 slope = -normalize(g + 1e-6) * rim;

    vec3 bg = backdrop(p);
    vec3 through = backdrop(p + slope * 0.06) * 1.08 + uAccent2 * 0.035;      // bent, slightly brighter
    float light = max(dot(slope, normalize(vec2(-0.6, 0.8))), 0.0);
    vec3 glass = through
               + mix(uAccent1, uAccent3, 0.5 + 0.5 * slope.x) * pow(rim, 3.0) * 0.30   // coloured rim
               + vec3(1.0) * pow(light, 6.0) * 0.22;                                    // soft highlight

    float shadow = smoothstep(0.6, 1.0, field(p + vec2(-0.012, 0.02), t)) * (1.0 - inside);
    vec3 col = mix(bg * (1.0 - shadow * 0.22), glass, inside);
    fragColor = vec4(col, 1.0);
}
