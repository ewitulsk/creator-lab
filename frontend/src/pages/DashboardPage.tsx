import { HardDrive } from "lucide-react";
import { useDashboard } from "@/api/queries";
import {
  Alert,
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  PageHeader,
  Progress,
  Spinner,
  Stat,
} from "@/components/ui/primitives";
import { fmtAgo, fmtBytes, fmtCount, fmtDateTime } from "@/lib/format";

export function DashboardPage() {
  const { data, isLoading, error } = useDashboard();
  if (isLoading) return <Spinner />;
  if (error || !data) return <Alert tone="destructive">{error?.message ?? "Failed to load"}</Alert>;

  const { counts, disk, quota } = data;
  const usedPct = disk.drive_total ? (disk.drive_used / disk.drive_total) * 100 : 0;
  const jobs = data.jobs_by_status;
  const active = (jobs.queued ?? 0) + (jobs.claimed ?? 0) + (jobs.running ?? 0);

  return (
    <div>
      <PageHeader title="Dashboard" description="Everything the platform tracks, at a glance." />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Channels" value={fmtCount(counts.channels)} />
        <Stat
          label="Videos"
          value={fmtCount(counts.videos)}
          hint={
            Object.entries(data.videos_by_format)
              .map(([k, v]) => `${fmtCount(v)} ${k}`)
              .join(" · ") || undefined
          }
        />
        <Stat label="Stat snapshots" value={fmtCount(counts.video_snapshots + counts.channel_snapshots)} />
        <Stat label="Analyzer results" value={fmtCount(counts.results_done)} hint={`${fmtCount(counts.assets)} media files`} />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <HardDrive className="size-4" /> Disk
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <Progress value={usedPct} />
            <div className="flex justify-between text-muted-foreground">
              <span>{fmtBytes(disk.drive_free)} free</span>
              <span>of {fmtBytes(disk.drive_total)}</span>
            </div>
            <div className="flex justify-between">
              <span>creator-lab data</span>
              <span className="tabular">{fmtBytes(disk.data_bytes)}</span>
            </div>
            {Object.entries(disk.folders)
              .filter(([, b]) => b > 0)
              .map(([name, bytes]) => (
                <div key={name} className="flex justify-between text-muted-foreground">
                  <span>{name}/</span>
                  <span className="tabular">{fmtBytes(bytes)}</span>
                </div>
              ))}
            {disk.drive_free < 20 * 1024 ** 3 ? (
              <Alert tone="warning">Less than 20 GB free on the data drive.</Alert>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Jobs and runners</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div className="flex flex-wrap gap-2">
              <Badge tone="primary">{active} active</Badge>
              <Badge tone="success">{jobs.done ?? 0} done</Badge>
              <Badge tone="destructive">{jobs.failed ?? 0} failed</Badge>
            </div>
            {data.runners.length === 0 ? (
              <p className="text-muted-foreground">No runners have registered yet.</p>
            ) : (
              data.runners.map((r) => (
                <div key={r.id} className="flex items-center justify-between">
                  <span>
                    {r.name} <span className="text-muted-foreground">({r.type})</span>
                  </span>
                  <Badge tone={r.online ? "success" : "outline"}>{r.online ? "online" : fmtAgo(r.last_heartbeat)}</Badge>
                </div>
              ))
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">YouTube API quota today</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <Progress value={(quota.units / 10_000) * 100} />
            <div className="flex justify-between text-muted-foreground">
              <span className="tabular">
                {fmtCount(quota.units)} of 10,000 units ({quota.calls} calls)
              </span>
              <span>budget {fmtCount(quota.budget)}</span>
            </div>
            <p className="text-xs text-muted-foreground">Resets at midnight Pacific ({quota.day}).</p>
          </CardContent>
        </Card>
      </div>

      <h2 className="mt-8 mb-3 text-lg font-semibold">Recent errors</h2>
      {data.errors.length === 0 ? (
        <EmptyState title="No errors" />
      ) : (
        <div className="space-y-2">
          {data.errors.map((e) => (
            <Alert key={`${e.kind}-${e.id}`} tone="destructive">
              <div className="flex justify-between gap-4">
                <span className="font-medium">{e.title}</span>
                <span className="text-xs">{fmtDateTime(e.at)}</span>
              </div>
              <div className="mt-1 text-xs break-words opacity-80">{e.detail}</div>
            </Alert>
          ))}
        </div>
      )}
    </div>
  );
}
