// Name: Soft Gradient
// Deep colour pools that drift and melt into each other, with fine grain so it never bands.

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

void main() {
    vec2 uv = gl_FragCoord.xy / iResolution.xy;
    vec2 p = vec2(uv.x * iResolution.x / iResolution.y, uv.y);
    float t = iTime * 0.05;

    // warp the plane slowly so the pools are organic, not circles
    vec2 w = p + 0.25 * vec2(noise(p * 1.3 + t), noise(p * 1.3 - t + 4.0)) - 0.125;

    vec2 c1 = vec2(0.35 + 0.30 * sin(t * 0.9), 0.30 + 0.20 * cos(t * 0.7));
    vec2 c2 = vec2(1.20 + 0.30 * cos(t * 0.6), 0.70 + 0.20 * sin(t * 1.1));
    vec2 c3 = vec2(0.80 + 0.40 * sin(t * 0.5 + 2.0), 0.05 + 0.15 * cos(t * 0.8));
    float a = exp(-dot(w - c1, w - c1) * 3.2);
    float b = exp(-dot(w - c2, w - c2) * 2.6);
    float c = exp(-dot(w - c3, w - c3) * 3.6);

    vec3 col = uBackground * 1.3;
    col = mix(col, uAccent1 * 0.62, a * 0.85);
    col = mix(col, uAccent2 * 0.58, b * 0.80);
    col = mix(col, uAccent3 * 0.50, c * 0.75);
    col *= 1.0 - 0.25 * length(uv - 0.5);                       // soft vignette

    col += (hash(gl_FragCoord.xy + fract(iTime) * 97.0) - 0.5) * 0.018;   // fine grain
    fragColor = vec4(col, 1.0);
}
