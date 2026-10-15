import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { RotateCcw, Square } from "lucide-react";
import { useEffect, useRef } from "react";
import { isActive, useCancelJob, useJob, useJobLog, useRetryJob } from "@/api/jobs";
import { StatusBadge } from "@/components/badges";
import { Button } from "@/components/ui/button";
import {
  Alert,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Progress,
  Spinner,
  Stat,
  Table,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui/primitives";
import { fmtDateTime, fmtElapsed } from "@/lib/format";

function LogView({ jobId, active }: { jobId: number; active: boolean }) {
  const { data } = useJobLog(jobId, active);
  const ref = useRef<HTMLPreElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 80) el.scrollTop = el.scrollHeight;
  }, [data]);
  return (
    <pre ref={ref} className="max-h-96 overflow-auto rounded-lg bg-muted p-3 text-xs leading-relaxed whitespace-pre-wrap">
      {data?.log || "No log lines yet."}
    </pre>
  );
}

export function JobDetailPage() {
  const { jobId } = useParams({ from: "/jobs/$jobId" });
  const id = Number(jobId);
  const { data, isLoading, error } = useJob(id);
  const cancel = useCancelJob();
  const retry = useRetryJob();
  const navigate = useNavigate();

  if (isLoading) return <Spinner />;
  if (error || !data) return <Alert tone="destructive">{error?.message ?? "Not found"}</Alert>;
  const j = data.job;
  const active = isActive(j.status);
  const p = j.progress;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-1">
          <h1 className="flex items-center gap-3 text-2xl font-semibold tracking-tight">
            Job #{j.id}: {j.title} <StatusBadge status={j.status} />
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {j.analyzer}@{j.version} · {j.items_total} videos · issued by {j.created_by} {fmtDateTime(j.created_at)} · attempt{" "}
            {j.attempts}
            {j.runner ? ` · runner ${j.runner.name}` : ""}
            {j.depends_on.length ? ` · waits for ${j.depends_on.map((d) => `#${d}`).join(", ")}` : ""}
          </p>
        </div>
        <div className="flex gap-2">
          {active ? (
            <Button variant="destructive" disabled={cancel.isPending} onClick={() => cancel.mutate(j.id)}>
              <Square /> Cancel
            </Button>
          ) : null}
          {!active && j.items_by_status.done !== j.items_total ? (
            <Button
              variant="outline"
              disabled={retry.isPending}
              onClick={() => retry.mutate(j.id, { onSuccess: (r) => navigate({ to: "/jobs/$jobId", params: { jobId: String(r.job.id) } }) })}
            >
              <RotateCcw /> Retry unfinished
            </Button>
          ) : null}
        </div>
      </div>
      {retry.error ? <Alert tone="destructive">{retry.error.message}</Alert> : null}
      {j.error ? <Alert tone="destructive">{j.error}</Alert> : null}

      <Card>
        <CardContent className="space-y-3 p-4">
          <div className="text-lg font-medium">{j.progress_text}</div>
          <Progress value={p.overall_percent ?? (j.status === "done" ? 100 : 0)} />
          <div className="flex flex-wrap justify-between gap-2 text-sm text-muted-foreground">
            <span>{p.message}</span>
            <span className="tabular">
              {p.stage_index && p.stage_count ? `stage ${p.stage_index} of ${p.stage_count} · ` : ""}
              {p.overall_percent !== undefined ? `${p.overall_percent.toFixed(1)}% overall` : ""}
              {p.updated_at ? ` · updated ${fmtDateTime(p.updated_at)}` : ""}
            </span>
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Done" value={j.items_by_status.done ?? 0} />
        <Stat label="Running" value={j.items_by_status.running ?? 0} />
        <Stat label="Failed" value={j.items_by_status.failed ?? 0} />
        <Stat label="Elapsed" value={fmtElapsed(j.elapsed_seconds)} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Stage timings</CardTitle>
          </CardHeader>
          <CardContent>
            {j.stage_timings.length === 0 ? (
              <p className="text-sm text-muted-foreground">No stage has started.</p>
            ) : (
              <Table>
                <THead>
                  <TR className="hover:bg-transparent">
                    <TH>Stage</TH>
                    <TH>Started</TH>
                    <TH className="text-right">Duration</TH>
                  </TR>
                </THead>
                <TBody>
                  {j.stage_timings.map((s, i) => (
                    <TR key={`${s.stage}-${i}`}>
                      <TD>{s.stage}</TD>
                      <TD className="text-muted-foreground">{fmtDateTime(s.started_at)}</TD>
                      <TD className="text-right tabular">{s.seconds !== null ? fmtElapsed(s.seconds) : "running"}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Videos</CardTitle>
          </CardHeader>
          <CardContent className="max-h-96 overflow-auto">
            <Table>
              <TBody>
                {j.items.map((it) => (
                  <TR key={it.video_id}>
                    <TD className="max-w-xs">
                      <Link to="/videos/$videoId" params={{ videoId: String(it.video_id) }} className="line-clamp-1 hover:underline">
                        {it.title}
                      </Link>
                      {it.error ? <div className="line-clamp-2 text-xs text-destructive">{it.error}</div> : null}
                    </TD>
                    <TD className="text-xs text-muted-foreground">{it.stage}</TD>
                    <TD className="text-right">
                      <StatusBadge status={it.status} />
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Log</CardTitle>
        </CardHeader>
        <CardContent>
          <LogView jobId={j.id} active={active} />
        </CardContent>
      </Card>
    </div>
  );
}
