// Name: Aurora
// Scale: 0.5
// Folding curtains of light with fine drifting rays, over a starry sky, in the desktop's colours.

// hash: "Hash without Sine" by Dave Hoskins (MIT)
float hash(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

float noise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    vec2 u = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);   // quintic: no visible grid
    return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x),
               mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y);
}

float fbm(vec2 p) {
    float v = 0.0, a = 0.5;
    mat2 r = mat2(0.8, -0.6, 0.6, 0.8);                    // rotate octaves so they don't line up
    for (int i = 0; i < 5; i++) {
        v += a * noise(p);
        p = r * p * 2.02;
        a *= 0.5;
    }
    return v;
}

void main() {
    vec2 uv = gl_FragCoord.xy / iResolution.xy;
    float x = uv.x * iResolution.x / iResolution.y;
    float t = iTime * 0.04;

    // night sky, a little lighter near the horizon
    vec3 col = mix(uBackground * 1.8 + 0.006, uBackground * 0.8, smoothstep(0.0, 1.0, uv.y));

    // stars: tiny, sparse, gently twinkling, fading toward the horizon
    vec2 cell = floor(gl_FragCoord.xy / 3.0);
    float s = hash(cell);
    if (s > 0.9965) {
        vec2 c = (cell + 0.5) * 3.0;
        float d = length(gl_FragCoord.xy - c);
        float tw = 0.55 + 0.45 * sin(iTime * (0.6 + s * 2.0) + s * 80.0);
        col += vec3(0.9, 0.92, 1.0) * smoothstep(1.6, 0.0, d) * tw * smoothstep(0.25, 0.9, uv.y) * 0.8;
    }

    vec3 glow = vec3(0.0);
    for (int i = 0; i < 3; i++) {
        float fi = float(i);
        float sx = x * (0.9 + 0.35 * fi) + fi * 7.3;
        // the curtain's lower edge, folding slowly
        float base = 0.30 + 0.09 * fi + 0.36 * (fbm(vec2(sx * 0.45 + t * (0.8 + 0.3 * fi), fi * 3.0 + t * 0.5)) - 0.5);
        float d = uv.y - base;
        float height = 0.13 + 0.10 * fbm(vec2(sx * 0.7, t + fi * 5.0)) - 0.02 * fi;
        float body = smoothstep(-0.012, 0.018, d) * (exp(-max(d, 0.0) / height * 2.4) + 0.6 * exp(-max(d, 0.0) * 45.0));
        // fine vertical rays that drift sideways
        float r = fbm(vec2(sx * 16.0 + t * 5.0 * (fi + 1.0), d * 1.2 + t * 2.0));
        float rays = 0.3 + 0.7 * smoothstep(0.3, 0.75, r);
        float k = clamp(d / height, 0.0, 1.0);
        vec3 low = fi < 0.5 ? uAccent1 : (fi < 1.5 ? uAccent2 : uAccent3);
        vec3 high = fi < 0.5 ? uAccent2 : (fi < 1.5 ? uAccent3 : uAccent1);
        glow += mix(low, high, k) * body * rays * (0.95 - 0.22 * fi);
    }
    col += glow * 0.8;
    col += uAccent1 * 0.035 * exp(-uv.y * 7.0);     // faint glow along the horizon

    // a dark mountain ridge in front, lit faintly at its edge by the aurora
    float ridge = 0.09 + 0.16 * fbm(vec2(x * 0.9 + 3.0, 1.0)) + 0.025 * fbm(vec2(x * 6.0, 7.0));
    float ground = smoothstep(ridge + 0.002, ridge - 0.002, uv.y);
    vec3 land = uBackground * 0.45 + glow * 0.04;
    col = mix(col, land, ground);
    col += uAccent1 * 0.10 * exp(-abs(uv.y - ridge) * 260.0) * (1.0 - ground * 0.5);
    fragColor = vec4(col, 1.0);
}
