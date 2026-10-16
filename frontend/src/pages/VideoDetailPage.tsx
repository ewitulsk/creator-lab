import * as Plot from "@observablehq/plot";
import { Link, useParams } from "@tanstack/react-router";
import { Download, ExternalLink, Play, RefreshCw } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { useRefreshVideo, useVideo } from "@/api/channels";
import type { Schemas } from "@/api/client";
import { usePull } from "@/api/discover";
import { useIssueJobs } from "@/api/jobs";
import { FormatBadge, OutlierBadge, StatusBadge } from "@/components/badges";
import { PlotFigure } from "@/components/PlotFigure";
import { useBrainMeta } from "@/api/brain";
import { leverMarkers, useVideoLevers } from "@/api/levers";
import { BrainPlayer } from "@/components/brain/BrainPlayer";
import { LeversPanel } from "@/components/LeversPanel";
import { MostReplayed } from "@/components/MostReplayed";
import { RealEngagement } from "@/components/RealEngagement";
import { Button } from "@/components/ui/button";
import { Alert, Badge, Card, CardContent, CardHeader, CardTitle, Spinner, Stat, Tabs } from "@/components/ui/primitives";
import { fmtBytes, fmtCount, fmtDate, fmtDateTime, fmtDuration, fmtRatio } from "@/lib/format";

type Detail = Schemas["VideoDetail"];

function StatsOverTime({ snapshots, publishedAt }: { snapshots: Detail["snapshots"]; publishedAt: string | null }) {
  const [metric, setMetric] = useState<"views" | "likes" | "comments">("views");
  const options = useMemo(() => {
    const pub = publishedAt ? new Date(publishedAt) : null;
    const data = snapshots
      .filter((s) => s[metric] !== null)
      .map((s) => ({ date: new Date(s.captured_at), value: s[metric] as number }));
    if (pub && data.length && data[0].date > pub) data.unshift({ date: pub, value: 0 });
    return {
      height: 240,
      marginLeft: 56,
      x: { type: "utc", label: null },
      y: { label: metric, grid: true, tickFormat: "~s" },
      marks: [
        Plot.lineY(data, { x: "date", y: "value", stroke: "var(--color-primary)", curve: "monotone-x" }),
        Plot.dot(data, { x: "date", y: "value", r: 2.5, fill: "var(--color-primary)", tip: true }),
      ],
    } as Plot.PlotOptions;
  }, [snapshots, metric, publishedAt]);
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle className="text-base">Stats over time</CardTitle>
        <Tabs
          value={metric}
          onChange={setMetric}
          tabs={[
            { value: "views", label: "Views" },
            { value: "likes", label: "Likes" },
            { value: "comments", label: "Comments" },
          ]}
        />
      </CardHeader>
      <CardContent>
        {snapshots.length < 2 ? (
          <p className="mb-2 text-sm text-muted-foreground">
            {snapshots.length} snapshot so far. Pulled videos are snapshotted daily for their first 30 days, then weekly.
          </p>
        ) : null}
        <PlotFigure options={options} />
      </CardContent>
    </Card>
  );
}

function AnalyzerPanel({ a, videoId }: { a: Detail["analyzers"][number]; videoId: number }) {
  const issue = useIssueJobs();
  return (
    <div className="space-y-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={a.status} />
        <span className="text-muted-foreground">
          {a.analyzer}@{a.version} · {a.runner_type}
          {a.computed_at ? ` · computed ${fmtDateTime(a.computed_at)}` : ""}
        </span>
        {a.job_id ? (
          <Link to="/jobs/$jobId" params={{ jobId: String(a.job_id) }} className="text-primary hover:underline">
            job #{a.job_id}
          </Link>
        ) : null}
        {a.runner_type !== "builtin" && a.status !== "queued" && a.status !== "running" ? (
          <Button
            size="sm"
            variant="outline"
            disabled={issue.isPending}
            onClick={() => issue.mutate({ requests: [{ analyzer: a.analyzer, video_ids: [videoId] }], force: a.status === "done" })}
          >
            <Play /> {a.status === "done" ? "Run again" : "Run"}
          </Button>
        ) : null}
      </div>
      {a.error ? <Alert tone="destructive">{a.error}</Alert> : null}
      {Object.keys(a.summary).length ? (
        <pre className="max-h-80 overflow-auto rounded-lg bg-muted p-3 text-xs">{JSON.stringify(a.summary, null, 2)}</pre>
      ) : a.status === "missing" ? (
        <p className="text-muted-foreground">Not computed for this video yet.</p>
      ) : null}
    </div>
  );
}

export function VideoDetailPage() {
  const { videoId } = useParams({ from: "/videos/$videoId" });
  const id = Number(videoId);
  const q = useVideo(id);
  const refresh = useRefreshVideo(id);
  const pull = usePull();
  const [tab, setTab] = useState<string>("");
  const player = useRef<HTMLVideoElement>(null);
  const brainMeta = useBrainMeta(id);
  const levers = useVideoLevers(id);
  const markers = useMemo(() => leverMarkers(levers.data), [levers.data]);
  const seek = (s: number) => {
    if (player.current) player.current.currentTime = s;
  };

  if (q.isLoading) return <Spinner />;
  if (q.error || !q.data) return <Alert tone="destructive">{q.error?.message ?? "Not found"}</Alert>;
  const { video: v, snapshots, assets, analyzers } = q.data;
  const activeTab = tab || analyzers[0]?.analyzer || "";
  const active = analyzers.find((a) => a.analyzer === activeTab);
  const media = assets.find((a) => a.kind === "video");

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold tracking-tight">{v.title}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            <Link to="/channels/$channelId" params={{ channelId: String(v.channel.id) }} className="hover:text-foreground">
              {v.channel.title}
            </Link>
            <span>· {fmtDate(v.published_at)}</span>
            <span>· {fmtDuration(v.duration_s)}</span>
            <FormatBadge format={v.format} />
            {v.selection === "tracked" ? <Badge tone="success">pulled</Badge> : <Badge tone="outline">discovered</Badge>}
            {v.channel.owned ? <Badge tone="primary">owned</Badge> : null}
            {v.gone ? <Badge tone="destructive">removed from YouTube</Badge> : null}
            <a href={v.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 hover:text-foreground">
              YouTube <ExternalLink className="size-3" />
            </a>
          </div>
        </div>
        <div className="flex gap-2">
          {v.selection !== "tracked" ? (
            <Button disabled={pull.isPending} onClick={() => pull.mutate({ video_ids: [v.id] })}>
              <Download /> Pull this video
            </Button>
          ) : null}
          <Button variant="outline" disabled={refresh.isPending} onClick={() => refresh.mutate()} title="Fetch fresh stats (1 quota unit)">
            <RefreshCw /> Refresh stats
          </Button>
        </div>
      </div>
      {refresh.error ? <Alert tone="destructive" className="mb-4">{refresh.error.message}</Alert> : null}

      {brainMeta.data ? (
        <div className="mb-4">
          <BrainPlayer meta={brainMeta.data} videoRef={player} videoUrl={media?.url} poster={v.thumbnail} />
        </div>
      ) : null}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="space-y-4">
          {brainMeta.data ? null : media ? (
            <video ref={player} src={media.url} controls className="w-full rounded-xl bg-black" poster={v.thumbnail} />
          ) : (
            <img src={v.thumbnail} alt="" className="w-full rounded-xl" referrerPolicy="no-referrer" />
          )}
          <MostReplayed videoId={v.id} durationS={v.duration_s} onSeek={seek} markers={markers} />
          <StatsOverTime snapshots={snapshots} publishedAt={v.published_at} />
        </div>
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <Stat label="Views" value={fmtCount(v.views)} />
            <Stat
              label="Outlier score"
              value={<OutlierBadge score={v.outlier_score} />}
              hint={v.outlier_baseline ? `baseline ${fmtCount(Math.round(v.outlier_baseline))} views` : "no baseline"}
            />
            <Stat label="Likes" value={fmtCount(v.likes)} hint={v.views && v.likes ? `${((v.likes / v.views) * 100).toFixed(2)}% of views` : undefined} />
            <Stat label="Comments" value={fmtCount(v.comments)} />
          </div>
          <LeversPanel videoId={v.id} onSeek={seek} />
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Analyzers</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <Tabs
                value={activeTab}
                onChange={setTab}
                tabs={analyzers.map((a) => ({ value: a.analyzer, label: a.title }))}
              />
              {active ? <AnalyzerPanel a={active} videoId={v.id} /> : null}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Files</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1 text-sm">
              {assets.length === 0 ? <p className="text-muted-foreground">Nothing on disk (pull the video to download it).</p> : null}
              {assets.map((a) => (
                <div key={a.id} className="flex justify-between gap-2">
                  <a href={a.url} target="_blank" rel="noreferrer" className="truncate text-primary hover:underline">
                    {a.kind}
                    {a.variant ? ` (${a.variant})` : ""}
                  </a>
                  <span className="whitespace-nowrap text-muted-foreground tabular">
                    {a.width && a.height ? `${a.width}×${a.height} · ` : ""}
                    {fmtBytes(a.bytes)}
                  </span>
                </div>
              ))}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Details</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <div className="text-muted-foreground">
                Language {v.language ?? "–"} · category {v.category_id ?? "–"} · {v.privacy ?? "–"} · aspect{" "}
                {v.aspect ? v.aspect.toFixed(2) : "–"} · first seen {fmtDate(v.discovered_at)}
                {v.outlier_score !== null ? ` · ${fmtRatio(v.outlier_score)} of baseline` : ""}
              </div>
              {v.tags.length ? (
                <div className="flex flex-wrap gap-1">
                  {v.tags.slice(0, 30).map((t) => (
                    <Badge key={t} tone="outline">
                      {t}
                    </Badge>
                  ))}
                </div>
              ) : null}
              <p className="max-h-60 overflow-auto whitespace-pre-wrap text-muted-foreground">{v.description}</p>
            </CardContent>
          </Card>
        </div>
      </div>
      {v.channel.owned ? (
        <div className="mt-4">
          <RealEngagement videoId={v.id} markers={markers} />
        </div>
      ) : null}
    </div>
  );
}
