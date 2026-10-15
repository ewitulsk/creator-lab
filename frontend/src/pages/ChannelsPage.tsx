import { Link, useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { Compass } from "lucide-react";
import { useChannels } from "@/api/channels";
import type { Schemas } from "@/api/client";
import { DataTable } from "@/components/DataTable";
import { Button } from "@/components/ui/button";
import { Alert, Badge, Card, EmptyState, PageHeader, Spinner } from "@/components/ui/primitives";
import { fmtAgo, fmtBytes, fmtCount } from "@/lib/format";

type Channel = Schemas["ChannelSummary"];

const columns: ColumnDef<Channel>[] = [
  {
    id: "channel",
    header: "Channel",
    accessorFn: (c) => c.title.toLowerCase(),
    cell: ({ row: { original: c } }) => (
      <div className="flex items-center gap-3">
        {c.thumbnail_url ? (
          <img src={c.thumbnail_url} alt="" className="size-8 rounded-full" referrerPolicy="no-referrer" />
        ) : (
          <div className="size-8 rounded-full bg-muted" />
        )}
        <div className="min-w-0">
          <div className="flex items-center gap-2 font-medium">
            <span className="truncate">{c.title || c.external_id}</span>
            {c.owned ? <Badge tone="primary">owned</Badge> : c.is_mine ? <Badge tone="primary">mine</Badge> : null}
          </div>
          <div className="text-xs text-muted-foreground">{c.handle}</div>
        </div>
      </div>
    ),
  },
  {
    id: "subscribers",
    header: "Subscribers",
    accessorFn: (c) => c.subscribers ?? -1,
    cell: ({ row }) => fmtCount(row.original.subscribers),
    meta: { align: "right" },
  },
  {
    id: "videos",
    header: "Uploads listed",
    accessorFn: (c) => c.video_count ?? 0,
    cell: ({ row: { original: c } }) => (
      <span title={Object.entries(c.videos_by_format).map(([k, v]) => `${v} ${k}`).join(", ")}>
        {fmtCount(c.video_count)}
      </span>
    ),
    meta: { align: "right" },
  },
  {
    id: "tracked",
    header: "Pulled",
    accessorFn: (c) => c.tracked_count,
    cell: ({ row }) => fmtCount(row.original.tracked_count),
    meta: { align: "right" },
  },
  {
    id: "disk",
    header: "Disk",
    accessorFn: (c) => c.disk_bytes,
    cell: ({ row }) => fmtBytes(row.original.disk_bytes),
    meta: { align: "right" },
  },
  {
    id: "flags",
    header: "Following",
    accessorFn: (c) => (c.followed ? 1 : 0),
    cell: ({ row: { original: c } }) => (
      <div className="flex gap-1">
        {c.followed ? <Badge tone="success">followed</Badge> : <Badge tone="outline">not followed</Badge>}
        {c.auto_track_new ? <Badge tone="primary">auto-track</Badge> : null}
      </div>
    ),
  },
  {
    id: "synced",
    header: "Listed",
    accessorFn: (c) => c.backfilled_at ?? "",
    cell: ({ row: { original: c } }) =>
      c.syncing ? (
        <span className="inline-flex items-center gap-2 text-primary">
          <Spinner className="size-3" /> listing
        </span>
      ) : c.sync_error ? (
        <Badge tone="destructive" title={c.sync_error}>
          error
        </Badge>
      ) : (
        <span className="text-muted-foreground">{fmtAgo(c.backfilled_at)}</span>
      ),
  },
];

export function ChannelsPage() {
  const { data, isLoading, error } = useChannels();
  const navigate = useNavigate();
  return (
    <div>
      <PageHeader
        title="Channels"
        description="Every channel you've opened. Followed channels get daily stats and new-upload checks."
        actions={
          <Button asChild>
            <Link to="/discover">
              <Compass /> Discover channels
            </Link>
          </Button>
        }
      />
      {isLoading ? (
        <Spinner />
      ) : error ? (
        <Alert tone="destructive">{error.message}</Alert>
      ) : !data || data.channels.length === 0 ? (
        <EmptyState title="No channels yet">
          <Link to="/discover" className="text-primary underline">
            Open Discover
          </Link>{" "}
          to find one.
        </EmptyState>
      ) : (
        <Card>
          <DataTable
            data={data.channels}
            columns={columns}
            onRowClick={(c) => navigate({ to: "/channels/$channelId", params: { channelId: String(c.id) } })}
          />
        </Card>
      )}
    </div>
  );
}
