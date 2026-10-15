import * as Plot from "@observablehq/plot";
import type { LeverMarker } from "@/api/levers";

const KIND_COLORS: Record<string, string> = { elicited: "#a855f7", displayed: "#ec4899", evidence: "#3b82f6" };

/** Gemini lever markers on a time axis in seconds: emotional moments as ticks, evidence as spans. */
export function leverMarks(markers: LeverMarker[] | undefined, yTop: number): Plot.Markish[] {
  if (!markers?.length) return [];
  const moments = markers.filter((m) => m.kind !== "evidence");
  const spans = markers.filter((m) => m.kind === "evidence" && m.t_end !== null);
  return [
    Plot.rect(spans, {
      x1: "t",
      x2: "t_end",
      y1: 0,
      y2: yTop * 0.06,
      fill: KIND_COLORS.evidence,
      fillOpacity: 0.35,
      title: "label",
    }),
    Plot.ruleX(moments, { x: "t", stroke: (d: LeverMarker) => KIND_COLORS[d.kind], strokeOpacity: 0.5, strokeDasharray: "2,2" }),
    Plot.dot(moments, { x: "t", y: yTop * 0.95, r: 3.5, fill: (d: LeverMarker) => KIND_COLORS[d.kind], title: "label" }),
  ];
}
