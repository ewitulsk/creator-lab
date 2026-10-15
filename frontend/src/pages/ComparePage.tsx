import * as Plot from "@observablehq/plot";
import { Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { useChannels } from "@/api/channels";
import type { Schemas } from "@/api/client";
import { useCompare } from "@/api/data";
import { PlotFigure } from "@/components/PlotFigure";
import {
  Alert,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  PageHeader,
  Select,
  Spinner,
  Table,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Tabs,
} from "@/components/ui/primitives";
import { fmtCount, fmtDuration } from "@/lib/format";

type Profile = Schemas["ChannelProfile"];
type Fmt = "long" | "short";

const MAX = 8;

function pct(v: number | null | undefined, digits = 1) {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(digits)}%`;
}

const ROWS: { label: string; hint?: string; get: (p: Profile, f: Fmt) => string }[] = [
  { label: "Uploads (all time)", get: (p, f) => fmtCount(p[f].count) },
  { label: "Uploads per month", hint: "inside the window", get: (p, f) => p[f].uploads_per_month.toFixed(1) },
  { label: "Median views", get: (p, f) => fmtCount(p[f].median_views) },
  { label: "Median length", get: (p, f) => fmtDuration(p[f].median_duration_s) },
  { label: "Hit rate", hint: "outlier score ≥ 2", get: (p, f) => pct(p[f].hit_rate) },
  { label: "Flop rate", hint: "outlier score ≤ 0.5", get: (p, f) => pct(p[f].flop_rate) },
  { label: "Like rate", hint: "median likes / views", get: (p, f) => pct(p[f].like_rate, 2) },
  { label: "Comment rate", hint: "median comments / views", get: (p, f) => pct(p[f].comment_rate, 3) },
  { label: "Shorts share", hint: "of all uploads", get: (p) => pct(p.shorts_share) },
];

function MonthlyChart({ profiles, fmt, metric }: { profiles: Profile[]; fmt: Fmt; metric: "uploads" | "views" }) {
  const options = useMemo(() => {
    const data = profiles.flatMap((p) =>
      p.monthly.map((m) => ({
        channel: p.title,
        date: new Date(`${m.month}-01T00:00:00Z`),
        value: metric === "uploads" ? m[fmt] : m.median_long_views,
      })),
    );
    const rows = data.filter((d) => d.value !== null && (metric === "uploads" || (d.value ?? 0) > 0));
    return {
      height: 260,
      marginLeft: 56,
      color: { legend: true },
      x: { type: "utc", label: null },
      y:
        metric === "uploads"
          ? { label: `${fmt === "long" ? "Long-form" : "Shorts"} uploads per month`, grid: true }
          : { type: "log", label: "Median long-form views (uploads that month)", grid: true, tickFormat: "~s" },
      marks: [
        Plot.lineY(rows, { x: "date", y: "value", stroke: "channel", curve: "monotone-x" }),
        Plot.dot(rows, { x: "date", y: "value", fill: "channel", r: 2, tip: true }),
      ],
    } as Plot.PlotOptions;
  }, [profiles, fmt, metric]);
  return <PlotFigure options={options} />;
}

function OutlierDistribution({ profiles, fmt }: { profiles: Profile[]; fmt: Fmt }) {
  const options = useMemo(() => {
    const data = profiles.flatMap((p) =>
      p.videos.filter((v) => v.format === fmt && v.outlier_score > 0).map((v) => ({ ...v, channel: p.title })),
    );
    return {
      height: 70 + 46 * profiles.length,
      marginLeft: 140,
      x: { type: "log", label: "Outlier score (views ÷ channel baseline)", grid: true },
      y: { label: null },
      color: { legend: false },
      marks: [
        Plot.ruleX([0.5, 1, 2], { stroke: "currentColor", strokeOpacity: 0.25, strokeDasharray: "3,3" }),
        Plot.boxX(data, { x: "outlier_score", y: "channel", fill: "channel", fillOpacity: 0.25 }),
        Plot.dot(data, {
          x: "outlier_score",
          y: "channel",
          r: 2.2,
          fill: "channel",
          fillOpacity: 0.6,
          tip: true,
          title: (d: (typeof data)[number]) => `${d.title}\n${fmtCount(d.views)} views · ${d.outlier_score.toFixed(2)}×`,
        }),
      ],
    } as Plot.PlotOptions;
  }, [profiles, fmt]);
  return <PlotFigure options={options} />;
}

function SubscriberChart({ profiles }: { profiles: Profile[] }) {
  const options = useMemo(() => {
    const data = profiles.flatMap((p) =>
      p.subscribers.map((s) => ({ channel: p.title, date: new Date(s.captured_at), subscribers: s.subscribers })),
    );
    return {
      height: 240,
      marginLeft: 56,
      color: { legend: true },
      x: { type: "utc", label: null },
      y: { type: "log", label: "Subscribers", grid: true, tickFormat: "~s" },
      marks: [
        Plot.lineY(data, { x: "date", y: "subscribers", stroke: "channel" }),
        Plot.dot(data, { x: "date", y: "subscribers", fill: "channel", r: 2, tip: true }),
      ],
    } as Plot.PlotOptions;
  }, [profiles]);
  return <PlotFigure options={options} />;
}

export function ComparePage() {
  const channels = useChannels();
  const list = useMemo(
    () => [...(channels.data?.channels ?? [])].sort((a, b) => (b.subscribers ?? 0) - (a.subscribers ?? 0)),
    [channels.data],
  );
  const [picked, setPicked] = useState<number[] | null>(null);
  // Default: up to three followed channels, biggest first.
  const selected = picked ?? list.filter((c) => c.followed).slice(0, 3).map((c) => c.id);
  const [months, setMonths] = useState(12);
  const [fmt, setFmt] = useState<Fmt>("long");
  const compare = useCompare(selected, months);
  const profiles = compare.data?.channels ?? [];

  function toggle(id: number) {
    const next = selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id].slice(-MAX);
    setPicked(next);
  }

  if (channels.isLoading) return <Spinner />;
  return (
    <div className="space-y-4">
      <PageHeader
        title="Compare creators"
        description="Long-form and Shorts are compared separately; rates use the window you pick, scores use each channel's own baseline."
        actions={
          <>
            <Select value={months} onChange={(e) => setMonths(Number(e.target.value))} aria-label="Window">
              {[3, 6, 12, 24, 60].map((m) => (
                <option key={m} value={m}>
                  Last {m} months
                </option>
              ))}
            </Select>
            <Tabs
              value={fmt}
              onChange={setFmt}
              tabs={[
                { value: "long", label: "Long-form" },
                { value: "short", label: "Shorts" },
              ]}
            />
          </>
        }
      />
      {list.length === 0 ? (
        <EmptyState title="No channels yet">
          Add creators on <Link to="/discover" className="underline">Discover</Link> first.
        </EmptyState>
      ) : (
        <div className="flex flex-wrap gap-2" role="group" aria-label="Channels">
          {list.map((c) => {
            const on = selected.includes(c.id);
            return (
              <button
                key={c.id}
                type="button"
                aria-pressed={on}
                onClick={() => toggle(c.id)}
                className={`flex cursor-pointer items-center gap-2 rounded-full border px-3 py-1 text-sm transition-colors ${
                  on ? "border-primary bg-primary/10" : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {c.thumbnail_url ? (
                  <img src={c.thumbnail_url} alt="" className="size-5 rounded-full" referrerPolicy="no-referrer" />
                ) : null}
                {c.title}
              </button>
            );
          })}
        </div>
      )}
      {compare.error ? <Alert tone="destructive">{compare.error.message}</Alert> : null}
      {selected.length === 0 ? null : compare.isLoading ? (
        <Spinner />
      ) : profiles.length ? (
        <>
          <Card>
            <CardContent className="overflow-x-auto p-0">
              <Table>
                <THead>
                  <TR>
                    <TH>{fmt === "long" ? "Long-form" : "Shorts"}</TH>
                    {profiles.map((p) => (
                      <TH key={p.id} className="text-right">
                        <Link to="/channels/$channelId" params={{ channelId: String(p.id) }} className="hover:underline">
                          {p.title}
                        </Link>
                      </TH>
                    ))}
                  </TR>
                </THead>
                <TBody>
                  {ROWS.map((r) => (
                    <TR key={r.label}>
                      <TD>
                        {r.label}
                        {r.hint ? <span className="block text-xs text-muted-foreground">{r.hint}</span> : null}
                      </TD>
                      {profiles.map((p) => (
                        <TD key={p.id} className="text-right tabular">
                          {r.get(p, fmt)}
                        </TD>
                      ))}
                    </TR>
                  ))}
                </TBody>
              </Table>
            </CardContent>
          </Card>
          <div className="grid gap-4 xl:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Upload cadence</CardTitle>
              </CardHeader>
              <CardContent>
                <MonthlyChart profiles={profiles} fmt={fmt} metric="uploads" />
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Median long-form views by month</CardTitle>
              </CardHeader>
              <CardContent>
                <MonthlyChart profiles={profiles} fmt="long" metric="views" />
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Outlier scores ({fmt === "long" ? "long-form" : "Shorts"})</CardTitle>
              </CardHeader>
              <CardContent>
                <OutlierDistribution profiles={profiles} fmt={fmt} />
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Subscribers</CardTitle>
              </CardHeader>
              <CardContent>
                <SubscriberChart profiles={profiles} />
              </CardContent>
            </Card>
          </div>
        </>
      ) : null}
    </div>
  );
}
