import * as Plot from "@observablehq/plot";
import { Flame } from "lucide-react";
import { useMemo, useState } from "react";
import { useIssueJobs } from "@/api/jobs";
import type { LeverMarker } from "@/api/levers";
import { useHeatmap } from "@/api/owner";
import { PlotFigure } from "@/components/PlotFigure";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, Select } from "@/components/ui/primitives";
import { fmtDateTime, fmtDuration } from "@/lib/format";
import { leverMarks } from "@/lib/leverMarks";

/** YouTube's public "Most replayed" heatmap (yt-dlp), on the video's time axis. */
export function MostReplayed({
  videoId,
  durationS,
  onSeek,
  markers,
}: {
  videoId: number;
  durationS: number | null;
  onSeek?: (s: number) => void;
  markers?: LeverMarker[];
}) {
  const [captureId, setCaptureId] = useState<number | undefined>(undefined);
  const { data } = useHeatmap(videoId, captureId);
  const issue = useIssueJobs();

  const options = useMemo(() => {
    if (!data || data.points.length === 0) return null;
    const pts = data.points.map((p) => ({ ...p, mid: (p.start_s + p.end_s) / 2 }));
    return {
      height: 120,
      marginLeft: 40,
      x: { label: "seconds", domain: [0, durationS ?? Math.max(...pts.map((p) => p.end_s))], grid: true },
      y: { label: "replays", domain: [0, 1], ticks: 2 },
      marks: [
        Plot.areaY(pts, { x: "mid", y: "value", fill: "#f97316", fillOpacity: 0.35, curve: "monotone-x" }),
        Plot.lineY(pts, { x: "mid", y: "value", stroke: "#f97316", curve: "monotone-x" }),
        Plot.ruleX(data.chapters, { x: "start_s", stroke: "currentColor", strokeOpacity: 0.3 }),
        Plot.text(data.chapters, { x: "start_s", y: 1, text: "title", dx: 3, textAnchor: "start", fontSize: 9, fillOpacity: 0.7 }),
        ...leverMarks(markers, 1),
        Plot.tip(pts, Plot.pointerX({ x: "mid", y: "value", title: (d) => `${fmtDuration(d.start_s)}–${fmtDuration(d.end_s)}: ${d.value.toFixed(2)}` })),
      ],
    } as Plot.PlotOptions;
  }, [data, durationS, markers]);

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <Flame className="size-4 text-orange-500" /> Most replayed <span className="text-xs font-normal text-muted-foreground">(public, yt-dlp)</span>
        </CardTitle>
        <div className="flex items-center gap-2">
          {data && data.captures.length > 1 ? (
            <Select value={captureId ?? data.capture_id ?? ""} onChange={(e) => setCaptureId(Number(e.target.value))}>
              {data.captures.map((c) => (
                <option key={c.id} value={c.id}>
                  {fmtDateTime(c.captured_at)} ({c.heatmap_points} pts, {c.source})
                </option>
              ))}
            </Select>
          ) : null}
          <Button
            size="sm"
            variant="outline"
            disabled={issue.isPending}
            onClick={() => issue.mutate({ requests: [{ analyzer: "heatmap", video_ids: [videoId] }], force: true })}
            title="Capture metadata now (no download)"
          >
            {data?.captures.length ? "Capture again" : "Capture"}
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {options ? (
          <PlotFigure
            options={options}
            onPick={(d) => {
              const x = d as { start_s?: number; t?: number };
              onSeek?.(x.start_s ?? x.t ?? 0);
            }}
          />
        ) : (
          <p className="text-sm text-muted-foreground">
            {data?.captures.length
              ? "YouTube shows no heatmap for this video (it needs enough views)."
              : "Not captured yet. Capture reads the video's public metadata only, without downloading it."}
            {issue.data ? " Job queued; a CPU runner will pick it up." : ""}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
