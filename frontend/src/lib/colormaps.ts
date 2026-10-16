/** 256-entry RGB lookup tables (0..1 floats) built from color stops. */

type Stop = [number, [number, number, number]];

const STOPS: Record<string, Stop[]> = {
  // Diverging blue-white-red, for signed values centred on 0.
  coolwarm: [
    [0, [0.23, 0.3, 0.75]],
    [0.25, [0.55, 0.69, 1.0]],
    [0.5, [0.87, 0.87, 0.87]],
    [0.75, [0.96, 0.6, 0.48]],
    [1, [0.71, 0.02, 0.15]],
  ],
  // Sequential, for magnitude.
  hot: [
    [0, [0.04, 0, 0]],
    [0.35, [0.9, 0.05, 0]],
    [0.7, [1, 0.8, 0]],
    [1, [1, 1, 1]],
  ],
  viridis: [
    [0, [0.27, 0.0, 0.33]],
    [0.25, [0.23, 0.32, 0.55]],
    [0.5, [0.13, 0.57, 0.55]],
    [0.75, [0.37, 0.79, 0.38]],
    [1, [0.99, 0.91, 0.14]],
  ],
};

export const COLORMAPS = Object.keys(STOPS);
/** Diverging maps colour the signed value; the others colour |value|. */
export const DIVERGING = new Set(["coolwarm"]);

const cache = new Map<string, Float32Array>();

export function lut(name: string): Float32Array {
  const hit = cache.get(name);
  if (hit) return hit;
  const stops = STOPS[name] ?? STOPS.coolwarm;
  const out = new Float32Array(256 * 3);
  for (let i = 0; i < 256; i++) {
    const t = i / 255;
    let k = 0;
    while (k < stops.length - 2 && t > stops[k + 1][0]) k++;
    const [t0, c0] = stops[k];
    const [t1, c1] = stops[k + 1];
    const f = t1 > t0 ? Math.min(1, Math.max(0, (t - t0) / (t1 - t0))) : 0;
    for (let c = 0; c < 3; c++) out[i * 3 + c] = c0[c] + (c1[c] - c0[c]) * f;
  }
  cache.set(name, out);
  return out;
}

/**
 * Colour one quantized value. code: 0 = no data, 1..255 = lo..hi with 128 = 0.
 * Returns the LUT index, or -1 when the vertex should show the plain cortex.
 */
export function lutIndex(code: number, diverging: boolean, thresholdCodes: number): number {
  if (code === 0) return -1;
  const signed = code - 128; // -127..127
  if (Math.abs(signed) < thresholdCodes) return -1;
  if (diverging) return Math.round(((code - 1) / 254) * 255);
  return Math.min(255, Math.round((Math.abs(signed) / 127) * 255));
}
