import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { useChannels } from "@/api/channels";
import { type ShortsSort, useShorts } from "@/api/data";
import { OutlierBadge } from "@/components/badges";
import { Alert, EmptyState, PageHeader, Select, Spinner, Tabs } from "@/components/ui/primitives";
import { fmtCount, fmtDate, fmtDuration } from "@/lib/format";

export function ShortsPage() {
  const channels = useChannels();
  const [channelId, setChannelId] = useState("");
  const [sort, setSort] = useState<ShortsSort>("published_at");
  const shorts = useShorts({ sort, channel_id: channelId || undefined, limit: 240 });
  const withShorts = (channels.data?.channels ?? []).filter((c) => (c.videos_by_format?.short ?? 0) > 0);

  return (
    <div className="space-y-4">
      <PageHeader
        title="Shorts"
        description="Every stored Short. Outlier scores compare each Short with its own channel's Shorts, never with long-form."
        actions={
          <>
            <Select value={channelId} onChange={(e) => setChannelId(e.target.value)} aria-label="Channel">
              <option value="">All channels</option>
              {withShorts.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title}
                </option>
              ))}
            </Select>
            <Tabs
              value={sort}
              onChange={setSort}
              tabs={[
                { value: "published_at", label: "Newest" },
                { value: "outlier_score", label: "Top outliers" },
                { value: "views", label: "Most viewed" },
              ]}
            />
          </>
        }
      />
      {shorts.error ? <Alert tone="destructive">{shorts.error.message}</Alert> : null}
      {shorts.isLoading ? (
        <Spinner />
      ) : !shorts.data?.videos.length ? (
        <EmptyState title="No Shorts stored yet">Shorts show up once a channel's uploads are listed on Discover.</EmptyState>
      ) : (
        <>
          <p className="text-sm text-muted-foreground">
            Showing {shorts.data.videos.length} of {fmtCount(shorts.data.total)}
          </p>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
            {shorts.data.videos.map((v) => (
              <Link key={v.id} to="/videos/$videoId" params={{ videoId: String(v.id) }} className="group block">
                <div className="relative aspect-[9/16] overflow-hidden rounded-lg bg-muted">
                  <img
                    src={v.thumbnail}
                    alt=""
                    loading="lazy"
                    referrerPolicy="no-referrer"
                    className="absolute inset-0 h-full w-full object-cover transition-opacity group-hover:opacity-90"
                  />
                  <span className="absolute bottom-1 right-1 rounded bg-black/70 px-1 text-xs text-white tabular">
                    {fmtDuration(v.duration_s)}
                  </span>
                </div>
                <div className="mt-2 line-clamp-2 text-sm font-medium">{v.title}</div>
                <div className="mt-1 text-xs text-muted-foreground">{v.channel_title}</div>
                <div className="mt-1 flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
                  <span className="tabular">{fmtCount(v.views)} views</span>·<span>{fmtDate(v.published_at)}</span>
                  <OutlierBadge score={v.outlier_score} />
                </div>
              </Link>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
