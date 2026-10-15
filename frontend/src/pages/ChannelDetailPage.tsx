import * as Plot from "@observablehq/plot";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { Compass, ExternalLink, RefreshCw, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import {
  useChannel,
  useChannelSnapshots,
  useChannelVideos,
  useDeleteChannel,
  useRefreshChannel,
  useUpdateChannel,
} from "@/api/channels";
import type { Schemas } from "@/api/client";
import { FormatBadge, OutlierBadge } from "@/components/badges";
import { PlotFigure } from "@/components/PlotFigure";
import { Button } from "@/components/ui/button";
import {
  Alert,
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  Input,
  Spinner,
  Stat,
  Tabs,
  Textarea,
} from "@/components/ui/primitives";
import { fmtAgo, fmtBytes, fmtCount, fmtDate, fmtRatio } from "@/lib/format";

type Video = Schemas["VideoSummary"];

const HIT = "#16a34a";
const FLOP = "#dc2626";
const NEUTRAL = "#64748b";

function outlierColor(score: number | null) {
  if (score === null) return NEUTRAL;
  return score >= 2 ? HIT : score <= 0.5 ? FLOP : NEUTRAL;
}

export function ViewsScatter({ videos, onPick }: { videos: Video[]; onPick: (v: Video) => void }) {
  const options = useMemo(() => {
    const data = videos
      .filter((v) => v.published_at && (v.views ?? 0) > 0)
      .map((v) => ({ ...v, date: new Date(v.published_at!) }));
    const baseline = data
      .filter((v) => v.outlier_baseline)
      .sort((a, b) => a.date.getTime() - b.date.getTime());
    return {
      height: 380,
      marginLeft: 56,
      x: { type: "utc", label: null },
      y: { type: "log", label: "Views", grid: true, tickFormat: "~s" },
      marks: [
        Plot.line(baseline, {
          x: "date",
          y: "outlier_baseline",
          stroke: "currentColor",
          strokeOpacity: 0.45,
          strokeWidth: 1.5,
          curve: "monotone-x",
        }),
        Plot.dot(data, {
          x: "date",
          y: "views",
          r: (d: Video) => (d.selection === "tracked" ? 4.5 : 3.2),
          fill: (d: Video) => outlierColor(d.outlier_score),
          fillOpacity: 0.85,
          stroke: (d: Video) => (d.selection === "tracked" ? "currentColor" : "none"),
          tip: true,
          title: (d: Video) =>
            `${d.title}\n${fmtCount(d.views)} views · ${fmtDate(d.published_at)}\noutlier ${fmtRatio(d.outlier_score)}${
              d.selection === "tracked" ? " · pulled" : ""
            }`,
        }),
      ],
    } as Plot.PlotOptions;
  }, [videos]);
  return <PlotFigure options={options} onPick={(d) => onPick(d as Video)} />;
}

function SubscriberChart({ channelId }: { channelId: number }) {
  const snaps = useChannelSnapshots(channelId);
  const options = useMemo(() => {
    const data = (snaps.data?.snapshots ?? [])
      .filter((s) => s.subscribers !== null)
      .map((s) => ({ ...s, date: new Date(s.captured_at) }));
    return {
      height: 220,
      marginLeft: 56,
      x: { type: "utc", label: null },
      y: { label: "Subscribers", grid: true, tickFormat: "~s", zero: false },
      marks: [
        Plot.lineY(data, { x: "date", y: "subscribers", stroke: "var(--color-primary)" }),
        Plot.dot(data, { x: "date", y: "subscribers", r: 2.5, fill: "var(--color-primary)", tip: true }),
      ],
    } as Plot.PlotOptions;
  }, [snaps.data]);
  if (snaps.isLoading) return <Spinner />;
  if ((snaps.data?.snapshots.length ?? 0) < 2)
    return <p className="text-sm text-muted-foreground">History builds up from daily snapshots once the channel is followed.</p>;
  return <PlotFigure options={options} />;
}

export function VideoCard({ v }: { v: Video }) {
  return (
    <Link to="/videos/$videoId" params={{ videoId: String(v.id) }} className="group block">
      <img
        src={v.thumbnail}
        alt=""
        loading="lazy"
        className="aspect-video w-full rounded-lg object-cover transition-opacity group-hover:opacity-90"
        referrerPolicy="no-referrer"
      />
      <div className="mt-2 line-clamp-2 text-sm font-medium">{v.title}</div>
      <div className="mt-1 flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
        <span className="tabular">{fmtCount(v.views)} views</span>·<span>{fmtDate(v.published_at)}</span>
        <OutlierBadge score={v.outlier_score} />
        {v.format !== "long" ? <FormatBadge format={v.format} /> : null}
      </div>
    </Link>
  );
}

function Toggle({ label, hint, checked, onChange }: { label: string; hint: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex cursor-pointer items-start gap-2 text-sm" title={hint}>
      <input type="checkbox" className="mt-0.5" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>
        {label}
        <span className="block text-xs text-muted-foreground">{hint}</span>
      </span>
    </label>
  );
}

export function ChannelDetailPage() {
  const { channelId } = useParams({ from: "/channels/$channelId" });
  const id = Number(channelId);
  const channel = useChannel(id);
  const [format, setFormat] = useState<"long" | "short">("long");
  const [tab, setTab] = useState<"performance" | "pulled" | "notes">("performance");
  const all = useChannelVideos(id, { limit: "all", format }, { poll: channel.data?.channel.syncing });
  const pulled = useChannelVideos(id, { limit: "all", selection: "tracked", sort: "published_at" });
  const update = useUpdateChannel(id);
  const refresh = useRefreshChannel(id);
  const del = useDeleteChannel();
  const navigate = useNavigate();
  const [tags, setTags] = useState<string | null>(null);
  const [notes, setNotes] = useState<string | null>(null);

  if (channel.isLoading) return <Spinner />;
  if (channel.error || !channel.data) return <Alert tone="destructive">{channel.error?.message ?? "Not found"}</Alert>;
  const ch = channel.data.channel;

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center gap-4">
        {ch.thumbnail_url ? <img src={ch.thumbnail_url} alt="" className="size-16 rounded-full" referrerPolicy="no-referrer" /> : null}
        <div className="min-w-0 flex-1">
          <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
            {ch.title} {ch.is_mine ? <Badge tone="primary">mine</Badge> : null}
          </h1>
          <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            <span>{ch.handle}</span>
            {ch.country ? <span>· {ch.country}</span> : null}
            <a href={ch.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 hover:text-foreground">
              · YouTube <ExternalLink className="size-3" />
            </a>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button asChild>
            <Link to="/discover/$channelId" params={{ channelId: String(ch.id) }}>
              <Compass /> Browse & pull videos
            </Link>
          </Button>
          <Button variant="outline" disabled={ch.syncing || refresh.isPending} onClick={() => refresh.mutate(true)}>
            {ch.syncing ? <Spinner className="size-3" /> : <RefreshCw />} Re-list uploads
          </Button>
          <Button
            variant="ghost"
            size="icon"
            title="Delete channel"
            onClick={() => {
              if (!window.confirm(`Delete ${ch.title} and all its stored videos and history?`)) return;
              const files = window.confirm("Also delete its downloaded files from disk?");
              del.mutate({ id: ch.id, deleteFiles: files }, { onSuccess: () => navigate({ to: "/channels" }) });
            }}
          >
            <Trash2 />
          </Button>
        </div>
      </div>
      {ch.sync_error ? <Alert tone="destructive" className="mb-4">Last sync failed: {ch.sync_error}</Alert> : null}

      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Subscribers" value={fmtCount(ch.subscribers)} />
        <Stat label="Channel views" value={fmtCount(ch.total_views)} />
        <Stat label="Uploads listed" value={fmtCount(ch.video_count)} hint={`YouTube reports ${fmtCount(ch.uploads)}`} />
        <Stat label="Pulled" value={fmtCount(ch.tracked_count)} hint={`listed ${fmtAgo(ch.backfilled_at)}`} />
        <Stat label="Disk" value={fmtBytes(ch.disk_bytes)} />
      </div>

      <Card className="mb-4">
        <CardContent className="flex flex-wrap gap-6 p-4">
          <Toggle
            label="Follow"
            hint="Daily channel stats and new-upload checks"
            checked={ch.followed}
            onChange={(v) => update.mutate({ followed: v })}
          />
          <Toggle
            label="Auto-track new uploads"
            hint="Pull every new upload automatically (download + captions)"
            checked={ch.auto_track_new}
            onChange={(v) => update.mutate({ auto_track_new: v, ...(v ? { followed: true } : {}) })}
          />
          <Toggle label="My channel" hint="Used by studies of your own channels" checked={ch.is_mine} onChange={(v) => update.mutate({ is_mine: v })} />
        </CardContent>
      </Card>

      <Tabs
        className="mb-4"
        value={tab}
        onChange={setTab}
        tabs={[
          { value: "performance", label: "Performance" },
          { value: "pulled", label: `Pulled videos (${ch.tracked_count})` },
          { value: "notes", label: "Tags & notes" },
        ]}
      />

      {tab === "performance" ? (
        <div className="grid gap-4">
          <Card>
            <CardHeader className="flex-row items-center justify-between">
              <CardTitle className="text-base">Views by upload date</CardTitle>
              <Tabs
                value={format}
                onChange={setFormat}
                tabs={[
                  { value: "long", label: "Long-form" },
                  { value: "short", label: "Shorts" },
                ]}
              />
            </CardHeader>
            <CardContent>
              <p className="mb-2 text-xs text-muted-foreground">
                Line: the rolling baseline (median of the 10 nearest uploads at equal age). <span style={{ color: HIT }}>●</span> ≥2×,{" "}
                <span style={{ color: FLOP }}>●</span> ≤0.5×. Ringed dots are pulled. Click a dot to open it.
              </p>
              {all.isLoading ? (
                <Spinner />
              ) : (all.data?.videos.length ?? 0) === 0 ? (
                <EmptyState title={ch.syncing ? "Listing uploads…" : "No uploads in this format"} />
              ) : (
                <ViewsScatter
                  videos={all.data!.videos}
                  onPick={(v) => navigate({ to: "/videos/$videoId", params: { videoId: String(v.id) } })}
                />
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Subscribers</CardTitle>
            </CardHeader>
            <CardContent>
              <SubscriberChart channelId={id} />
            </CardContent>
          </Card>
        </div>
      ) : null}

      {tab === "pulled" ? (
        pulled.data && pulled.data.videos.length > 0 ? (
          <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-5">
            {pulled.data.videos.map((v) => (
              <VideoCard key={v.id} v={v} />
            ))}
          </div>
        ) : (
          <EmptyState title="No videos pulled yet">
            <Link to="/discover/$channelId" params={{ channelId: String(ch.id) }} className="text-primary underline">
              Browse this channel's uploads
            </Link>{" "}
            and pull the ones to analyze.
          </EmptyState>
        )
      ) : null}

      {tab === "notes" ? (
        <Card>
          <CardContent className="space-y-3 p-4">
            <Input
              placeholder="Tags, comma-separated"
              value={tags ?? ch.tags.join(", ")}
              onChange={(e) => setTags(e.target.value)}
            />
            <Textarea placeholder="Notes" value={notes ?? ch.notes ?? ""} onChange={(e) => setNotes(e.target.value)} />
            <Button
              disabled={update.isPending}
              onClick={() =>
                update.mutate({
                  tags: (tags ?? ch.tags.join(",")).split(",").map((t) => t.trim()).filter(Boolean),
                  notes: notes ?? ch.notes,
                })
              }
            >
              Save
            </Button>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
