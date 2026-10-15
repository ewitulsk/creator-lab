import { describe, expect, it } from "vitest";
import { lut, lutIndex } from "./colormaps";

describe("colormaps", () => {
  it("builds 256-entry tables", () => {
    const t = lut("coolwarm");
    expect(t.length).toBe(768);
    // Middle of a diverging map is near-neutral grey.
    expect(Math.abs(t[127 * 3] - t[127 * 3 + 2])).toBeLessThan(0.1);
  });
  it("maps codes", () => {
    expect(lutIndex(0, true, 0)).toBe(-1); // no data
    expect(lutIndex(128, true, 5)).toBe(-1); // below threshold
    expect(lutIndex(255, true, 0)).toBe(255);
    expect(lutIndex(1, true, 0)).toBe(0);
    expect(lutIndex(1, false, 0)).toBe(255); // magnitude maps use |value|
  });
});
