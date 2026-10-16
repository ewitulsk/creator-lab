import { describe, expect, it } from "vitest";
import { fmtUsd, leverMarkers, type VideoLevers } from "./levers";

describe("leverMarkers", () => {
  it("takes one run's moments and labels them", () => {
    const data = {
      scorings: [],
      probe: null,
      moments: [
        { kind: "elicited", lever: "emotion", t_start: 12, t_end: null, intensity: 4, label: "joy", run_idx: 1 },
        { kind: "evidence", lever: "attention", t_start: 1, t_end: 4, intensity: null, label: "hook", run_idx: 1 },
        { kind: "elicited", lever: "emotion", t_start: 30, t_end: null, intensity: 2, label: "calm", run_idx: 2 },
      ],
    } as VideoLevers;
    const m = leverMarkers(data);
    expect(m).toHaveLength(2);
    expect(m[0]).toMatchObject({ t: 12, kind: "elicited", label: "joy (elicited, 4/5)" });
    expect(m[1]).toMatchObject({ t: 1, t_end: 4, label: "attention: hook" });
    expect(leverMarkers(undefined)).toEqual([]);
  });

  it("formats costs", () => {
    expect(fmtUsd(1.234)).toBe("$1.23");
    expect(fmtUsd(0.001)).toBe("<$0.01");
    expect(fmtUsd(null)).toBe("—");
  });
});
