import * as Plot from "@observablehq/plot";
import { useMemo, useState } from "react";
import type { LeverMarker } from "@/api/levers";
import { useEngagement, useHeatmap } from "@/api/owner";
import { PlotFigure } from "@/components/PlotFigure";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Select, Spinner, Table, TBody, TD, TH, THead, TR } from "@/components/ui/primitives";
import { fmtDuration, fmtInt } from "@/lib/format";
import { leverMarks } from "@/lib/leverMarks";

const AUDIENCE_COLORS: Record<string, string> = {
  ALL: "var(--color-primary)",
  ORGANIC: "#16a34a",
  AD_INSTREAM: "#f59e0b",
  AD_INDISPLAY: "#a855f7",
};

function zscore(values: (number | null)[]): (number | null)[] {
  const v = values.filter((x): x is number => x !== null && Number.isFinite(x));
  if (v.length < 2) return values.map(() => null);
  const mean = v.reduce((a, b) => a + b, 0) / v.length;
  const sd = Math.sqrt(v.reduce((a, b) => a + (b - mean) ** 2, 0) / (v.length - 1)) || 1;
  return values.map((x) => (x === null ? null : (x - mean) / sd));
}

function minmax(values: (number | null)[]): (number | null)[] {
  const v = values.filter((x): x is number => x !== null && Number.isFinite(x));
  if (!v.length) return values.map(() => null);
  const lo = Math.min(...v);
  const hi = Math.max(...v);
  return values.map((x) => (x === null ? null : hi > lo ? (x - lo) / (hi - lo) : 0.5));
}

type EngagementData = NonNullable<ReturnType<typeof useEngagement>["data"]>;
type HeatmapData = NonNullable<ReturnType<typeof useHeatmap>["data"]>;

/** The public heatmap and the owner's real retention on one time axis, clearly labelled. */
function compareOptions(eng: EngagementData, hm: HeatmapData): Plot.PlotOptions {
  const real = eng.retention.curves.ALL ?? Object.values(eng.retention.curves)[0] ?? [];
  const realY = minmax(real.map((p) => p.watch_ratio));
  const heatY = minmax(hm.points.map((p) => p.value));
  const rows = [
    ...real.map((p, i) => ({ x: p.seconds ?? p.ratio, y: realY[i], series: "Real retention (YouTube Analytics)" })),
    ...hm.points.map((p, i) => ({ x: (p.start_s + p.end_s) / 2, y: heatY[i], series: "Most replayed (public, yt-dlp)" })),
  ].filter((r) => r.y !== null);
  return {
    height: 200,
    marginLeft: 40,
    x: { label: "seconds", grid: true, domain: eng.duration_s ? [0, eng.duration_s] : undefined },
    y: { label: "scaled 0–1", grid: true, domain: [0, 1] },
    color: {
      domain: ["Real retention (YouTube Analytics)", "Most replayed (public, yt-dlp)"],
      range: ["var(--color-primary)", "#f97316"],
      legend: true,
    },
    marks: [Plot.line(rows, { x: "x", y: "y", stroke: "series", curve: "monotone-x" })],
  } as Plot.PlotOptions;
}

const LIFETIME_LABELS: Record<string, string> = {
  views: "Views",
  estimatedMinutesWatched: "Minutes watched",
  averageViewDuration: "Avg view duration (s)",
  averageViewPercentage: "Avg % viewed",
  likes: "Likes",
  dislikes: "Dislikes",
  comments: "Comments",
  shares: "Shares",
  subscribersGained: "Subscribers gained",
  subscribersLost: "Subscribers lost",
  videoThumbnailImpressions: "Impressions",
  videoThumbnailImpressionsClickRate: "Impressions CTR",
};

/** Owner analytics for one video: retention curve(s), TRIBE overlay, lifetime, daily, breakdowns. */
export function RealEngagement({ videoId, markers }: { videoId: number; markers?: LeverMarker[] }) {
  const [syncId, setSyncId] = useState<number | undefined>(undefined);
  const { data, isLoading } = useEngagement(videoId, syncId);
  const heatmap = useHeatmap(videoId);

  const retentionPlot = useMemo(() => {
    if (!data) return null;
    const rows = Object.entries(data.retention.curves).flatMap(([aud, pts]) =>
      pts.map((p) => ({ ...p, audience: aud, x: p.seconds ?? p.ratio })),
    );
    return {
      height: 260,
      marginLeft: 48,
      x: { label: data.duration_s ? "seconds" : "fraction of video", grid: true },
      y: { label: "audience watch ratio", grid: true },
      color: { domain: Object.keys(AUDIENCE_COLORS), range: Object.values(AUDIENCE_COLORS), legend: true },
      marks: [
        Plot.line(rows, { x: "x", y: "watch_ratio", stroke: "audience", curve: "monotone-x" }),
        // Gemini lever markers only fit a seconds axis
        ...(rows.some((r) => r.seconds !== null && r.seconds !== undefined)
          ? leverMarks(markers, Math.max(1, ...rows.map((r) => r.watch_ratio ?? 0)))
          : []),
        Plot.tip(rows, Plot.pointerX({ x: "x", y: "watch_ratio", title: (d) => `${d.audience} ${fmtDuration(d.x)}: ${d.watch_ratio}` })),
      ],
    } as Plot.PlotOptions;
  }, [data, markers]);

  const overlayPlot = useMemo(() => {
    if (!data) return null;
    const all = data.retention.curves.ALL ?? Object.values(data.retention.curves)[0] ?? [];
    const rel = zscore(all.map((p) => p.relative));
    const relRows = all.map((p, i) => ({ x: p.seconds ?? p.ratio, y: rel[i], series: "relative retention (z)" }));
    const tribeRows = data.tribe
      ? data.tribe.t.map((t, i) => ({ x: t, y: data.tribe!.engagement[i], series: "TRIBE engagement (z)" }))
      : [];
    const rows = [...relRows, ...tribeRows].filter((r) => r.y !== null);
    return {
      height: 220,
      marginLeft: 48,
      x: { label: "seconds", grid: true, domain: data.duration_s ? [0, data.duration_s] : undefined },
      y: { label: "z-score", grid: true },
      color: { domain: ["relative retention (z)", "TRIBE engagement (z)"], range: ["#16a34a", "#ef4444"], legend: true },
      marks: [Plot.ruleY([0], { stroke: "currentColor", strokeOpacity: 0.3 }), Plot.line(rows, { x: "x", y: "y", stroke: "series" })],
    } as Plot.PlotOptions;
  }, [data]);

  if (isLoading) return <Spinner />;
  if (!data) return null;
  const hasRetention = Object.keys(data.retention.curves).length > 0;
  const comparison = heatmap.data && heatmap.data.points.length && hasRetention ? compareOptions(data, heatmap.data) : null;

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
        <div>
          <CardTitle className="text-base">Real engagement</CardTitle>
          <CardDescription>Owner analytics from YouTube. Retention points are converted to seconds with the video's length.</CardDescription>
        </div>
        {data.retention.versions.length > 1 ? (
          <Select value={syncId ?? data.retention.sync_id ?? ""} onChange={(e) => setSyncId(Number(e.target.value))}>
            {data.retention.versions.map((v) => (
              <option key={v} value={v}>
                snapshot from sync #{v}
              </option>
            ))}
          </Select>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-6">
        {!hasRetention ? (
          <p className="text-sm text-muted-foreground">
            No retention data yet. Run "Sync data from YT" in Settings (owner sign-in required).
          </p>
        ) : (
          <>
            {retentionPlot ? <PlotFigure options={retentionPlot} /> : null}
            {comparison ? (
              <div>
                <div className="mb-1 text-sm font-medium">
                  Public heatmap vs. real retention{" "}
                  <span className="font-normal text-muted-foreground">
                    (separate sources, each scaled 0 to 1 for shape only; never merged)
                  </span>
                </div>
                <PlotFigure options={comparison} />
              </div>
            ) : null}
            <div>
              <div className="mb-1 text-sm font-medium">
                Dips and bumps versus TRIBE{" "}
                <span className="font-normal text-muted-foreground">
                  {data.tribe ? `(TRIBE: ${data.tribe.definition}, draft)` : "(run TRIBE on this video to overlay its engagement curve)"}
                </span>
              </div>
              {overlayPlot ? <PlotFigure options={overlayPlot} /> : null}
            </div>
          </>
        )}
        {data.lifetime ? (
          <div className="grid grid-cols-2 gap-2 text-sm md:grid-cols-4">
            {Object.entries(data.lifetime).map(([k, v]) => (
              <div key={k} className="rounded-lg border p-2">
                <div className="text-xs text-muted-foreground">{LIFETIME_LABELS[k] ?? k}</div>
                <div className="font-medium tabular">{typeof v === "number" ? fmtInt(Math.round(v * 100) / 100) : String(v)}</div>
              </div>
            ))}
          </div>
        ) : null}
        {data.daily.length ? (
          <PlotFigure
            options={{
              height: 160,
              marginLeft: 48,
              x: { type: "utc", label: null },
              y: { label: "views per day", grid: true },
              marks: [Plot.rectY(data.daily.map((d) => ({ ...d, date: new Date(d.day) })), { x: "date", y: "views", interval: "day", fill: "var(--color-primary)" })],
            }}
          />
        ) : null}
        {Object.keys(data.breakdowns).length ? (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {Object.entries(data.breakdowns).map(([dim, rows]) => (
              <div key={dim}>
                <div className="mb-1 text-sm font-medium">{dim.replace("_", " ")}</div>
                <Table>
                  <THead>
                    <TR className="hover:bg-transparent">
                      <TH>{dim}</TH>
                      {Object.keys(rows[0] ?? {})
                        .filter((k) => k !== "key")
                        .map((k) => (
                          <TH key={k} className="text-right">
                            {k}
                          </TH>
                        ))}
                    </TR>
                  </THead>
                  <TBody>
                    {rows.slice(0, 10).map((r) => (
                      <TR key={String(r.key)}>
                        <TD>{String(r.key)}</TD>
                        {Object.entries(r)
                          .filter(([k]) => k !== "key")
                          .map(([k, v]) => (
                            <TD key={k} className="text-right tabular">
                              {typeof v === "number" ? fmtInt(Math.round(v * 10) / 10) : String(v)}
                            </TD>
                          ))}
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </div>
            ))}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
