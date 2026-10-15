import { Link } from "@tanstack/react-router";
import { Cpu, MonitorCog, Radio } from "lucide-react";
import { useState } from "react";
import { isActive, type JobSummary, useJobs, useLiveJobs, useRunners } from "@/api/jobs";
import { StatusBadge } from "@/components/badges";
import {
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  PageHeader,
  Progress,
  Select,
  Spinner,
  Table,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui/primitives";
import { fmtAgo, fmtDateTime, fmtElapsed } from "@/lib/format";

export function JobRow({ j }: { j: JobSummary }) {
  const p = j.progress;
  return (
    <TR>
      <TD className="w-14 text-muted-foreground">
        <Link to="/jobs/$jobId" params={{ jobId: String(j.id) }} className="hover:underline">
          #{j.id}
        </Link>
      </TD>
      <TD>
        <Link to="/jobs/$jobId" params={{ jobId: String(j.id) }} className="font-medium hover:underline">
          {j.title}
        </Link>
        <div className="text-xs text-muted-foreground">
          {j.items_total} video{j.items_total === 1 ? "" : "s"} · {j.created_by}
        </div>
      </TD>
      <TD>
        <StatusBadge status={j.status} />
      </TD>
      <TD className="min-w-64">
        <div className="text-sm">{j.progress_text}</div>
        {isActive(j.status) && p.overall_percent !== undefined ? (
          <div className="mt-1 flex items-center gap-2">
            <Progress value={p.overall_percent} className="h-1.5" />
            <span className="w-10 text-right text-xs text-muted-foreground tabular">{p.overall_percent.toFixed(0)}%</span>
          </div>
        ) : null}
        {p.message && j.status === "running" ? <div className="mt-0.5 truncate text-xs text-muted-foreground">{p.message}</div> : null}
        {j.error ? <div className="mt-0.5 line-clamp-2 text-xs text-destructive">{j.error}</div> : null}
      </TD>
      <TD className="text-sm text-muted-foreground">{j.runner?.name ?? "–"}</TD>
      <TD className="whitespace-nowrap text-right text-sm text-muted-foreground tabular">
        {j.elapsed_seconds !== null ? fmtElapsed(j.elapsed_seconds) : fmtAgo(j.created_at)}
      </TD>
    </TR>
  );
}

function JobTable({ jobs }: { jobs: JobSummary[] }) {
  return (
    <Table>
      <THead>
        <TR className="hover:bg-transparent">
          <TH>Job</TH>
          <TH>Analyzer</TH>
          <TH>Status</TH>
          <TH>Live state</TH>
          <TH>Runner</TH>
          <TH className="text-right">Time</TH>
        </TR>
      </THead>
      <TBody>
        {jobs.map((j) => (
          <JobRow key={j.id} j={j} />
        ))}
      </TBody>
    </Table>
  );
}

function Runners() {
  const { data } = useRunners();
  if (!data) return <Spinner />;
  if (data.runners.length === 0)
    return (
      <p className="text-sm text-muted-foreground">
        No runner has registered yet. Start one from the repo root: <code>uv run --project runners python -m runners.cpu</code>{" "}
        (downloads, captions) and <code>D:\VideoAnalysis\BrainThing\.venv\Scripts\python.exe -m runners.tribe</code> (TRIBE).
      </p>
    );
  return (
    <div className="grid gap-3 md:grid-cols-2">
      {data.runners.map((r) => (
        <div key={r.id} className="flex items-start gap-3 rounded-lg border p-3">
          {r.type === "gpu" ? <MonitorCog className="mt-0.5 size-5 text-primary" /> : <Cpu className="mt-0.5 size-5 text-primary" />}
          <div className="min-w-0 flex-1 text-sm">
            <div className="flex items-center gap-2 font-medium">
              {r.name}
              <Badge tone={r.online ? "success" : "outline"}>{r.online ? "online" : `seen ${fmtAgo(r.last_heartbeat)}`}</Badge>
            </div>
            <div className="text-xs text-muted-foreground">
              {r.gpu ?? r.type} · {r.capabilities.join(", ")}
              {typeof r.info.vram_free_gb === "number" ? ` · ${r.info.vram_free_gb} GB VRAM free` : ""}
            </div>
            {r.current_job_id ? (
              <Link to="/jobs/$jobId" params={{ jobId: String(r.current_job_id) }} className="text-xs text-primary hover:underline">
                working on job #{r.current_job_id}
              </Link>
            ) : (
              <div className="text-xs text-muted-foreground">idle</div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

export function JobsPage() {
  const live = useLiveJobs();
  const [status, setStatus] = useState("");
  const history = useJobs({ status: status || undefined, limit: 100 });
  const active = (live.jobs ?? []).filter((j) => isActive(j.status));

  return (
    <div className="space-y-6">
      <PageHeader
        title="Jobs"
        description="Every job the platform issued, which runner holds it and its exact live state."
        actions={
          <Badge tone={live.live ? "success" : "outline"} className="h-7">
            <Radio className="size-3" /> {live.live ? "live" : "polling"}
          </Badge>
        }
      />
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Runners</CardTitle>
        </CardHeader>
        <CardContent>
          <Runners />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Active ({active.length})</CardTitle>
        </CardHeader>
        <CardContent>
          {live.jobs === undefined ? <Spinner /> : active.length === 0 ? <EmptyState title="Nothing running" /> : <JobTable jobs={active} />}
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle className="text-base">History</CardTitle>
          <Select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            <option value="queued,claimed,running">Active</option>
            <option value="done">Done</option>
            <option value="failed">Failed</option>
            <option value="cancelled">Cancelled</option>
          </Select>
        </CardHeader>
        <CardContent>
          {history.data ? (
            history.data.jobs.length ? (
              <>
                <JobTable jobs={history.data.jobs} />
                <p className="mt-2 text-xs text-muted-foreground">
                  Showing {history.data.jobs.length} of {history.data.total}. Updated {fmtDateTime(new Date().toISOString())}.
                </p>
              </>
            ) : (
              <EmptyState title="No jobs yet">Pull videos in Discover, or select cells in Coverage to issue jobs.</EmptyState>
            )
          ) : (
            <Spinner />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
