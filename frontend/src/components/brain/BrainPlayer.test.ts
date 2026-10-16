import { describe, expect, it } from "vitest";
import { frameAt } from "./BrainPlayer";

describe("frameAt", () => {
  const grid = [0, 1, 2, 3, 4];
  it("maps seconds to the row showing then", () => {
    expect(frameAt(0, grid, 1)).toBe(0);
    expect(frameAt(2.99, grid, 1)).toBe(2);
    expect(frameAt(3, grid, 1)).toBe(3);
    expect(frameAt(99, grid, 1)).toBe(4);
  });
  it("handles irregular starts", () => {
    expect(frameAt(2.5, [0, 0.5, 2, 3.5], 1)).toBe(2);
    expect(frameAt(0.7, [0, 0.5, 2, 3.5], 1)).toBe(1);
  });
});
