// Name: Starfield
// A slow drift through stars of different sizes and colours, with a faint band of galaxy behind.

// hash: "Hash without Sine" by Dave Hoskins (MIT)
float hash(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

float noise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    vec2 u = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);
    return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x),
               mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y);
}

float fbm(vec2 p) {
    float v = 0.0, a = 0.5;
    mat2 r = mat2(0.8, -0.6, 0.6, 0.8);
    for (int i = 0; i < 5; i++) { v += a * noise(p); p = r * p * 2.03; a *= 0.5; }
    return v;
}

vec3 stars(vec2 uv, float scale, float speed, float bright, float seed) {
    uv = uv * scale + vec2(iTime * speed, iTime * speed * 0.35) + seed;
    vec2 id = floor(uv);
    vec2 f = fract(uv) - 0.5;
    float h = hash(id + seed);
    if (h < 0.72) return vec3(0.0);
    vec2 off = (vec2(hash(id + 3.1), hash(id + 7.7)) - 0.5) * 0.7;
    float d = length(f - off);
    float mag = pow(hash(id + 1.7), 6.0);                       // most stars small, a few bright
    float core = smoothstep(0.03 + mag * 0.05, 0.0, d);
    float halo = exp(-d * (28.0 - mag * 18.0)) * mag * 0.6;
    float twinkle = 0.75 + 0.25 * sin(iTime * (0.8 + h * 2.5) + h * 60.0);
    float pick = hash(id + 9.1);
    vec3 tint = pick < 0.6 ? vec3(0.92, 0.95, 1.0) : (pick < 0.8 ? mix(vec3(1.0), uAccent1, 0.5) : mix(vec3(1.0), uAccent3, 0.5));
    return tint * (core + halo) * twinkle * bright;
}

void main() {
    vec2 uv = (gl_FragCoord.xy - 0.5 * iResolution.xy) / iResolution.y;
    float t = iTime * 0.01;

    // a faint diagonal band of galaxy
    vec2 q = mat2(0.86, -0.5, 0.5, 0.86) * uv;
    float band = exp(-q.y * q.y * 9.0);
    float dust = fbm(q * 2.2 + vec2(t, 0.0));
    float gas = band * smoothstep(0.35, 0.85, dust);
    vec3 col = uBackground;
    col += mix(uAccent2, uAccent1, dust) * gas * 0.16;
    col += uAccent3 * band * 0.025;

    col += stars(uv, 7.0, 0.003, 0.9, 0.0);
    col += stars(uv, 14.0, 0.006, 0.7, 17.0);
    col += stars(uv, 28.0, 0.012, 0.55, 41.0);
    col += stars(uv, 55.0, 0.02, 0.4, 73.0) * (0.4 + band);     // tiny ones, thicker in the band
    fragColor = vec4(col, 1.0);
}
