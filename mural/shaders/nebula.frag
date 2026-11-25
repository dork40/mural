// Name: Nebula
// Scale: 0.5
// Glowing clouds of gas folding slowly in on themselves, with dark dust lanes and stars behind.

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
    for (int i = 0; i < 5; i++) { v += a * noise(p); p = r * p * 2.02; a *= 0.48; }
    return v;
}

void main() {
    vec2 uv = (gl_FragCoord.xy - 0.5 * iResolution.xy) / iResolution.y;
    float t = iTime * 0.015;

    vec2 q = vec2(fbm(uv * 1.4 + vec2(t, 0.0)), fbm(uv * 1.4 + vec2(5.2, 1.3) - t));
    vec2 r = vec2(fbm(uv * 1.2 + q * 1.1 + vec2(1.7, 9.2) + t * 0.6), fbm(uv * 1.2 + q * 1.1 + vec2(8.3, 2.8)));
    float gas = fbm(uv * 1.3 + r * 1.2);
    float dust = smoothstep(0.58, 0.78, fbm(uv * 2.2 + r * 1.2 + 11.0));

    float shape = smoothstep(1.25, 0.1, length(uv * vec2(0.8, 1.15) - vec2(0.1, 0.0)));   // brighter middle
    float dens = smoothstep(0.3, 0.85, gas) * shape;

    vec3 col = uBackground;
    col = mix(col, uAccent2 * 0.6, dens);
    col = mix(col, uAccent1 * 0.75, smoothstep(0.55, 0.95, gas) * shape * q.x);
    col += uAccent3 * pow(dens, 2.5) * 0.45;                             // hot cores
    col *= 1.0 - dust * 0.55 * dens;                                     // dark lanes

    vec2 cell = floor(gl_FragCoord.xy / 3.0);
    float s = hash(cell);
    if (s > 0.996) {
        float d = length(gl_FragCoord.xy - (cell + 0.5) * 3.0);
        col += vec3(0.9, 0.93, 1.0) * smoothstep(1.5, 0.0, d) * (1.0 - dens * 0.8);
    }
    fragColor = vec4(col, 1.0);
}
