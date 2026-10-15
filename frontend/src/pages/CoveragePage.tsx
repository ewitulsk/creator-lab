import { Link } from "@tanstack/react-router";
import { Play } from "lucide-react";
import { useMemo, useState } from "react";
import { useChannels } from "@/api/channels";
import { type CoverageQuery, useCoverage, useIssueJobs } from "@/api/jobs";
import { fmtUsd, useLeversEstimate, useScoreLevers } from "@/api/levers";
import { FormatBadge } from "@/components/badges";
import { Button } from "@/components/ui/button";
import { Alert, Card, EmptyState, PageHeader, Select, Spinner } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";

const CELL: Record<string, string> = {
  done: "bg-success/70",
  queued: "bg-muted-foreground/30",
  running: "bg-primary animate-pulse",
  failed: "bg-destructive/70",
  missing: "bg-transparent border border-dashed border-muted-foreground/40",
  "n/a": "bg-transparent",
};

type Key = string; // `${videoId}:${analyzer}`

export function CoveragePage() {
  const [query, setQuery] = useState<CoverageQuery>({ selection: "tracked", limit: 300 });
  const { data, isLoading, error } = useCoverage(query);
  const channels = useChannels();
  const issue = useIssueJobs();
  const leverEstimate = useLeversEstimate();
  const scoreLevers = useScoreLevers();
  const [selected, setSelected] = useState<Set<Key>>(new Set());

  const issuable = useMemo(
    // Pairwise lever jobs are started from a study or the Compare page, not per cell.
    () =>
      new Set(
        (data?.analyzers ?? [])
          .filter((a) => a.runner_type !== "builtin" && a.name !== "levers_gemini_pairwise")
          .map((a) => a.name),
      ),
    [data],
  );
  const canSelect = (analyzer: string, status: string) =>
    issuable.has(analyzer) && (status === "missing" || status === "failed");

  const toggle = (k: Key) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });

  const selectColumn = (analyzer: string) => {
    if (!data) return;
    const keys = data.videos.filter((v) => canSelect(analyzer, v.cells[analyzer]?.status)).map((v) => `${v.video_id}:${analyzer}`);
    setSelected((prev) => {
      const next = new Set(prev);
      const all = keys.every((k) => next.has(k));
      keys.forEach((k) => (all ? next.delete(k) : next.add(k)));
      return next;
    });
  };

  const requests = useMemo(() => {
    const by: Record<string, number[]> = {};
    selected.forEach((k) => {
      const [vid, analyzer] = k.split(":");
      (by[analyzer] ??= []).push(Number(vid));
    });
    return Object.entries(by).map(([analyzer, video_ids]) => ({ analyzer, video_ids }));
  }, [selected]);

  const counts = useMemo(() => {
    const out: Record<string, Record<string, number>> = {};
    for (const v of data?.videos ?? [])
      for (const [a, c] of Object.entries(v.cells)) {
        out[a] ??= {};
        out[a][c.status] = (out[a][c.status] ?? 0) + 1;
      }
    return out;
  }, [data]);

  return (
    <div>
      <PageHeader
        title="Data coverage"
        description="Which analyzers have run on which videos. Click empty or failed cells (or a column header) to select them, then issue jobs."
      />
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <Select
          value={query.channel_id ?? ""}
          onChange={(e) => setQuery({ ...query, channel_id: e.target.value || undefined })}
        >
          <option value="">All channels</option>
          {channels.data?.channels.map((c) => (
            <option key={c.id} value={String(c.id)}>
              {c.title}
            </option>
          ))}
        </Select>
        <Select
          value={query.selection}
          onChange={(e) => setQuery({ ...query, selection: e.target.value as CoverageQuery["selection"] })}
        >
          <option value="tracked">Pulled videos</option>
          <option value="all">All stored videos</option>
          <option value="discovered">Discovered only</option>
        </Select>
        <Select value={query.format ?? ""} onChange={(e) => setQuery({ ...query, format: e.target.value || undefined })}>
          <option value="">Any format</option>
          <option value="long">Long-form</option>
          <option value="short">Shorts</option>
        </Select>
        <div className="flex-1" />
        <span className="text-sm text-muted-foreground">{selected.size} cells selected</span>
        <Button variant="ghost" disabled={!selected.size} onClick={() => setSelected(new Set())}>
          Clear
        </Button>
        <Button
          disabled={!requests.length || issue.isPending}
          onClick={() => {
            const gemini = requests.find((r) => r.analyzer === "levers_gemini");
            const rest = requests.filter((r) => r.analyzer !== "levers_gemini");
            // Gemini batches cost money: show the estimate and wait for a confirm.
            if (gemini) leverEstimate.mutate({ video_ids: gemini.video_ids });
            if (rest.length) issue.mutate({ requests: rest }, { onSuccess: () => !gemini && setSelected(new Set()) });
          }}
        >
          <Play /> Issue {requests.length} job{requests.length === 1 ? "" : "s"}
        </Button>
      </div>
      {issue.data ? (
        <Alert className="mb-3">
          Issued{" "}
          {issue.data.jobs.map((j, i) => (
            <span key={j.id}>
              {i ? ", " : ""}
              <Link to="/jobs/$jobId" params={{ jobId: String(j.id) }} className="text-primary underline">
                #{j.id} {j.analyzer}
              </Link>
            </span>
          ))}
          {issue.data.skipped.length ? `. Nothing new to do for: ${issue.data.skipped.join(", ")}.` : "."}
        </Alert>
      ) : null}
      {issue.error ? <Alert tone="destructive" className="mb-3">{issue.error.message}</Alert> : null}
      {leverEstimate.data && !scoreLevers.data ? (
        <Alert className="mb-3">
          Gemini levers: {leverEstimate.data.estimate.videos} videos ({leverEstimate.data.estimate.already_scored} already
          scored), {leverEstimate.data.estimate.calls} calls on {leverEstimate.data.estimate.model}, about{" "}
          {fmtUsd(leverEstimate.data.estimate.cost_usd)}.{" "}
          <Button
            size="sm"
            disabled={scoreLevers.isPending || !leverEstimate.data.estimate.videos}
            onClick={() => {
              const ids = requests.find((r) => r.analyzer === "levers_gemini")?.video_ids ?? [];
              scoreLevers.mutate(ids, { onSuccess: () => setSelected(new Set()) });
            }}
          >
            Confirm and queue
          </Button>
        </Alert>
      ) : null}
      {scoreLevers.data?.job ? (
        <Alert className="mb-3">
          Queued{" "}
          <Link to="/jobs/$jobId" params={{ jobId: String(scoreLevers.data.job.id) }} className="text-primary underline">
            #{scoreLevers.data.job.id} levers_gemini
          </Link>
          .
        </Alert>
      ) : null}
      {(leverEstimate.error ?? scoreLevers.error) ? (
        <Alert tone="destructive" className="mb-3">{(leverEstimate.error ?? scoreLevers.error)?.message}</Alert>
      ) : null}

      {isLoading ? (
        <Spinner />
      ) : error ? (
        <Alert tone="destructive">{error.message}</Alert>
      ) : !data || data.videos.length === 0 ? (
        <EmptyState title="No videos">Pull videos in Discover first.</EmptyState>
      ) : (
        <Card className="overflow-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-card">
              <tr className="border-b">
                <th className="p-2 text-left text-xs font-medium text-muted-foreground">
                  {data.videos.length} of {data.total} videos
                </th>
                {data.analyzers.map((a) => (
                  <th key={a.name} className="p-1 align-bottom">
                    <button
                      className={cn(
                        "w-full rounded px-1 py-1 text-xs font-medium [writing-mode:vertical-rl] rotate-180 hover:bg-accent",
                        !issuable.has(a.name) && "cursor-default text-muted-foreground hover:bg-transparent",
                      )}
                      title={`${a.name}@${a.version} (${a.runner_type})${issuable.has(a.name) ? ": click to select every missing cell" : ""}`}
                      onClick={() => issuable.has(a.name) && selectColumn(a.name)}
                    >
                      {a.title}
                    </button>
                    <div className="text-[10px] text-muted-foreground tabular">
                      {counts[a.name]?.done ?? 0}/{data.videos.length - (counts[a.name]?.["n/a"] ?? 0)}
                    </div>
                  </th>
                ))}
                {data.lever_columns.map((c) => (
                  <th key={c.key} className="p-1 align-bottom">
                    <span
                      className="block w-full px-1 py-1 text-xs font-medium [writing-mode:vertical-rl] rotate-180"
                      title={`${c.lever} lever, method ${c.method} (median score 1-5)`}
                    >
                      {c.lever} · {c.method.replace("gemini_agentic", "gemini")}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.videos.map((v) => (
                <tr key={v.video_id} className="border-b hover:bg-muted/40">
                  <td className="max-w-sm p-2">
                    <Link to="/videos/$videoId" params={{ videoId: String(v.video_id) }} className="line-clamp-1 hover:underline">
                      {v.title}
                    </Link>
                    {v.format !== "long" ? <FormatBadge format={v.format} /> : null}
                  </td>
                  {data.analyzers.map((a) => {
                    const c = v.cells[a.name];
                    const k = `${v.video_id}:${a.name}`;
                    const sel = selected.has(k);
                    const clickable = canSelect(a.name, c.status);
                    return (
                      <td key={a.name} className="p-1 text-center">
                        <button
                          className={cn(
                            "inline-block size-5 rounded",
                            CELL[c.status] ?? "bg-muted",
                            sel && "ring-2 ring-primary ring-offset-1 ring-offset-background",
                            clickable ? "cursor-pointer" : "cursor-default",
                          )}
                          title={`${a.title}: ${c.status}${c.job_id ? ` (job #${c.job_id})` : ""}`}
                          onClick={() => clickable && toggle(k)}
                        />
                      </td>
                    );
                  })}
                  {data.lever_columns.map((c) => {
                    const l = v.levers[c.key];
                    return (
                      <td key={c.key} className="p-1 text-center text-xs tabular">
                        {l?.score != null ? (
                          <span
                            className={cn("rounded px-1", l.flags.length && "bg-warning/20")}
                            title={`${c.lever}: ${l.score} (spread ${l.spread ?? "?"})${l.flags.length ? `; ${l.flags.join(", ")}` : ""}`}
                          >
                            {l.score.toFixed(1)}
                          </span>
                        ) : (
                          <span className="text-muted-foreground">·</span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
      <div className="mt-3 flex flex-wrap gap-4 text-xs text-muted-foreground">
        {Object.entries(CELL)
          .filter(([s]) => s !== "n/a")
          .map(([s, cls]) => (
            <span key={s} className="flex items-center gap-1">
              <span className={cn("inline-block size-3 rounded", cls)} /> {s}
            </span>
          ))}
      </div>
    </div>
  );
}
