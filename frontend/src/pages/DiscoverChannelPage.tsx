import { Link, useParams } from "@tanstack/react-router";
import { Download, RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";
import { useChannel, useRefreshChannel } from "@/api/channels";
import type { Schemas } from "@/api/client";
import { DEFAULT_FILTERS, type DiscoverFilters, estimateBytes, useDiscoverVideos, usePull } from "@/api/discover";
import { useSettings } from "@/api/queries";
import { useStudies } from "@/api/studies";
import { FormatBadge, OutlierBadge } from "@/components/badges";
import { Button } from "@/components/ui/button";
import {
  Alert,
  Badge,
  Card,
  CardContent,
  EmptyState,
  Input,
  Label,
  Select,
  Spinner,
  Table,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui/primitives";
import { fmtAgo, fmtBytes, fmtCount, fmtDate, fmtDuration } from "@/lib/format";
import { cn } from "@/lib/utils";

type Video = Schemas["DiscoverVideo"];

function Filters({ value, onChange }: { value: DiscoverFilters; onChange: (f: DiscoverFilters) => void }) {
  const set = (patch: Partial<DiscoverFilters>) => onChange({ ...value, ...patch });
  const studies = useStudies();
  return (
    <div className="flex flex-wrap items-end gap-4">
      <div className="flex flex-col gap-1">
        <Label className="text-xs text-muted-foreground">Videos needed for</Label>
        <Select
          value={value.study_id ?? ""}
          onChange={(e) => set({ study_id: e.target.value ? Number(e.target.value) : undefined })}
        >
          <option value="">Any (use the filters)</option>
          {studies.data?.studies.map((s) => (
            <option key={s.id} value={s.id}>
              {s.title} (v{s.version})
            </option>
          ))}
        </Select>
      </div>
      <div className="flex flex-col gap-1">
        <Label className="text-xs text-muted-foreground">Uploaded</Label>
        <Select value={value.timeframe} onChange={(e) => set({ timeframe: e.target.value as DiscoverFilters["timeframe"] })}>
          <option value="7d">Last 7 days</option>
          <option value="30d">Last 30 days</option>
          <option value="90d">Last 90 days</option>
          <option value="1y">Last year</option>
          <option value="all">All time</option>
          <option value="custom">Custom range</option>
        </Select>
      </div>
      {value.timeframe === "custom" ? (
        <>
          <div className="flex flex-col gap-1">
            <Label className="text-xs text-muted-foreground">From</Label>
            <Input type="date" value={value.since ?? ""} onChange={(e) => set({ since: e.target.value || undefined })} />
          </div>
          <div className="flex flex-col gap-1">
            <Label className="text-xs text-muted-foreground">To</Label>
            <Input type="date" value={value.until ?? ""} onChange={(e) => set({ until: e.target.value || undefined })} />
          </div>
        </>
      ) : null}
      <div className="flex flex-col gap-1">
        <Label className="text-xs text-muted-foreground">Show</Label>
        <Select value={value.pick} onChange={(e) => set({ pick: e.target.value as DiscoverFilters["pick"] })}>
          <option value="all">All uploads</option>
          <option value="top">Most popular</option>
          <option value="bottom">Least popular</option>
        </Select>
      </div>
      {value.pick !== "all" ? (
        <>
          <div className="flex flex-col gap-1">
            <Label className="text-xs text-muted-foreground">How many</Label>
            <Input
              type="number"
              min={1}
              className="w-20"
              value={value.n}
              onChange={(e) => set({ n: Math.max(1, Number(e.target.value) || 1) })}
            />
          </div>
          <div className="flex flex-col gap-1">
            <Label className="text-xs text-muted-foreground">Ranked by</Label>
            <Select value={value.rank} onChange={(e) => set({ rank: e.target.value as DiscoverFilters["rank"] })}>
              <option value="outlier">Outlier score (vs. channel at same age)</option>
              <option value="views">Raw views</option>
            </Select>
          </div>
        </>
      ) : null}
      <div className="flex flex-col gap-1">
        <Label className="text-xs text-muted-foreground">Format</Label>
        <Select value={value.formats} onChange={(e) => set({ formats: e.target.value })}>
          <option value="long,short">Long-form and Shorts</option>
          <option value="long">Long-form</option>
          <option value="short">Shorts</option>
        </Select>
      </div>
      <div className="flex flex-col gap-1">
        <Label className="text-xs text-muted-foreground">Min length (s)</Label>
        <Input
          type="number"
          min={0}
          className="w-24"
          value={value.min_duration ?? ""}
          onChange={(e) => set({ min_duration: e.target.value ? Number(e.target.value) : undefined })}
        />
      </div>
      <label className="flex h-9 items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={value.include_live === "1"}
          onChange={(e) => set({ include_live: e.target.checked ? "1" : undefined })}
        />
        include live streams / premieres
      </label>
    </div>
  );
}

function VideoRow({ v, checked, onToggle }: { v: Video; checked: boolean; onToggle: () => void }) {
  const tracked = v.selection === "tracked";
  return (
    <TR className={cn(checked && "bg-accent/50")}>
      <TD className="w-8">
        <input type="checkbox" checked={checked} onChange={onToggle} aria-label={`select ${v.title}`} />
      </TD>
      <TD className="w-32">
        <img src={v.thumbnail} alt="" loading="lazy" className="aspect-video w-28 rounded object-cover" referrerPolicy="no-referrer" />
      </TD>
      <TD className="max-w-md">
        <Link to="/videos/$videoId" params={{ videoId: String(v.id) }} className="line-clamp-2 font-medium hover:underline">
          {v.title}
        </Link>
        <div className="mt-1 flex flex-wrap gap-1">
          <FormatBadge format={v.format} />
          {tracked ? <Badge tone="success">pulled</Badge> : null}
          {v.role ? (
            <Badge tone="primary">
              {v.group ? `${v.group} ` : ""}
              {v.role}
            </Badge>
          ) : null}
        </div>
      </TD>
      <TD className="whitespace-nowrap text-muted-foreground">{fmtDate(v.published_at)}</TD>
      <TD className="text-right tabular">{fmtDuration(v.duration_s)}</TD>
      <TD className="text-right tabular">{fmtCount(v.views)}</TD>
      <TD className="text-right">
        <OutlierBadge score={v.outlier_score} />
      </TD>
    </TR>
  );
}

/** Step 2 of Discover: browse and filter one channel's uploads, then pull a selection. */
export function DiscoverChannelPage() {
  const { channelId } = useParams({ from: "/discover/$channelId" });
  const id = Number(channelId);
  const channel = useChannel(id);
  const [filters, setFilters] = useState<DiscoverFilters>(DEFAULT_FILTERS);
  const videos = useDiscoverVideos(id, filters);
  const refresh = useRefreshChannel(id);
  const pull = usePull();
  const settings = useSettings();
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [addToStudy, setAddToStudy] = useState(false);

  const list = useMemo(() => videos.data?.videos ?? [], [videos.data]);
  const byId = useMemo(() => new Map(list.map((v) => [v.id, v])), [list]);
  const chosen = [...selected].map((i) => byId.get(i)).filter((v): v is Video => !!v);
  const toPull = chosen.filter((v) => v.selection !== "tracked");
  const seconds = toPull.reduce((s, v) => s + (v.duration_s ?? 0), 0);
  const maxHeight = settings.data?.max_video_height ?? 1080;
  const allSelected = list.length > 0 && list.every((v) => selected.has(v.id));

  const toggle = (vid: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(vid)) next.delete(vid);
      else next.add(vid);
      return next;
    });

  if (channel.isLoading) return <Spinner />;
  if (channel.error || !channel.data) return <Alert tone="destructive">{channel.error?.message ?? "Not found"}</Alert>;
  const ch = channel.data.channel;
  const listing = videos.data?.listing;

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-4">
        {ch.thumbnail_url ? <img src={ch.thumbnail_url} alt="" className="size-14 rounded-full" referrerPolicy="no-referrer" /> : null}
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-semibold tracking-tight">{ch.title}</h1>
          <div className="text-sm text-muted-foreground">
            {ch.handle} · {fmtCount(ch.subscribers)} subscribers · {fmtCount(ch.uploads)} uploads ·{" "}
            <Link to="/channels/$channelId" params={{ channelId: String(ch.id) }} className="text-primary hover:underline">
              channel page
            </Link>
          </div>
        </div>
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          {listing?.syncing ? (
            <>
              <Spinner className="size-3" /> listing uploads… {fmtCount(ch.video_count)} so far
            </>
          ) : (
            <>listed {fmtAgo(listing?.listed_at)}</>
          )}
          <Button variant="outline" size="sm" disabled={listing?.syncing || refresh.isPending} onClick={() => refresh.mutate(true)}>
            <RefreshCw /> Refresh
          </Button>
        </div>
      </div>
      {listing?.error ? <Alert tone="destructive" className="mb-4">Listing failed: {listing.error}</Alert> : null}
      {videos.data?.study_rules ? <Alert className="mb-4">{videos.data.study_rules}</Alert> : null}

      <Card className="mb-4">
        <CardContent className="p-4">
          <Filters value={filters} onChange={setFilters} />
        </CardContent>
      </Card>

      <div className="sticky top-0 z-10 mb-3 flex flex-wrap items-center gap-3 rounded-lg border bg-background/95 p-3 backdrop-blur">
        <span className="text-sm">
          <b className="tabular">{fmtCount(videos.data?.matching)}</b> match
          {filters.pick !== "all" ? ` · showing ${list.length}` : ""}
        </span>
        <Button
          variant="outline"
          size="sm"
          disabled={list.length === 0}
          onClick={() => setSelected(allSelected ? new Set() : new Set(list.map((v) => v.id)))}
        >
          {allSelected ? "Clear selection" : `Select all ${list.length} shown`}
        </Button>
        <div className="flex-1" />
        {chosen.length > 0 ? (
          <span className="text-sm text-muted-foreground">
            {toPull.length} to pull{chosen.length > toPull.length ? ` (${chosen.length - toPull.length} already pulled)` : ""} · ~
            {fmtBytes(estimateBytes(seconds, maxHeight))} at {maxHeight}p · ~{Math.ceil(toPull.length / 50)} quota unit
            {Math.ceil(toPull.length / 50) === 1 ? "" : "s"}
          </span>
        ) : null}
        {filters.study_id ? (
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={addToStudy} onChange={(e) => setAddToStudy(e.target.checked)} /> also add to the study
          </label>
        ) : null}
        <Button
          disabled={toPull.length === 0 || pull.isPending}
          onClick={() =>
            pull.mutate(
              { video_ids: toPull.map((v) => v.id), study_id: addToStudy && filters.study_id ? filters.study_id : null },
              { onSuccess: () => setSelected(new Set()) },
            )
          }
        >
          <Download /> {pull.isPending ? "Pulling…" : `Pull ${toPull.length || ""} selected`}
        </Button>
      </div>
      {pull.data ? (
        <Alert className="mb-3">
          Pulled {pull.data.pulled} video{pull.data.pulled === 1 ? "" : "s"}
          {pull.data.jobs.length ? ` and queued ${pull.data.jobs.length} jobs` : ""}
          {pull.data.study_added ? `, added ${pull.data.study_added} to the study` : ""}. They now get stats snapshots on schedule.
        </Alert>
      ) : null}
      {pull.error ? <Alert tone="destructive" className="mb-3">{pull.error.message}</Alert> : null}

      {videos.isLoading ? (
        <Spinner />
      ) : list.length === 0 ? (
        <EmptyState title={listing?.syncing ? "Listing uploads…" : "No uploads match these filters"} />
      ) : (
        <Card>
          <Table>
            <THead>
              <TR className="hover:bg-transparent">
                <TH />
                <TH />
                <TH>Title</TH>
                <TH>Published</TH>
                <TH className="text-right">Length</TH>
                <TH className="text-right">Views</TH>
                <TH className="text-right" title="Views ÷ median of the 10 nearest uploads at the same age">
                  Outlier
                </TH>
              </TR>
            </THead>
            <TBody>
              {list.map((v) => (
                <VideoRow key={v.id} v={v} checked={selected.has(v.id)} onToggle={() => toggle(v.id)} />
              ))}
            </TBody>
          </Table>
        </Card>
      )}
    </div>
  );
}
