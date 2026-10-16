import { describe, expect, it } from "vitest";
import { fmtBytes, fmtCount, fmtDuration, fmtElapsed, fmtRatio } from "./format";

describe("format", () => {
  it("formats counts", () => {
    expect(fmtCount(null)).toBe("–");
    expect(fmtCount(950)).toBe("950");
    expect(fmtCount(1_250_000)).toBe("1.3M");
  });
  it("formats bytes", () => {
    expect(fmtBytes(512)).toBe("512 B");
    expect(fmtBytes(1536)).toBe("1.5 KB");
    expect(fmtBytes(76 * 1024 ** 3)).toBe("76.0 GB");
  });
  it("formats durations", () => {
    expect(fmtDuration(59)).toBe("0:59");
    expect(fmtDuration(3725)).toBe("1:02:05");
    expect(fmtElapsed(7800)).toBe("2h 10m");
  });
  it("formats ratios", () => {
    expect(fmtRatio(2.345)).toBe("2.35×");
    expect(fmtRatio(12.4)).toBe("12×");
  });
});
