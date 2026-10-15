import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { BarChart3, Download, Hammer, Lock, RefreshCw, Users } from "lucide-react";
import Markdown from "react-markdown";
import type { Schemas } from "@/api/client";
import { useAnalyze, useFillGaps, useLockStudy, useNewVersion, useSelectMembers, useStudy } from "@/api/studies";
import { StatusBadge } from "@/components/badges";
import { Button } from "@/components/ui/button";
import {
  Alert,
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Spinner,
  Table,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui/primitives";
import { fmtDateTime, fmtDuration, fmtRatio } from "@/lib/format";
import { STUDY_STATUS_TONE } from "./StudiesPage";

type Result = Schemas["StudyResultInfo"];

function p(v: unknown) {
  return typeof v === "number" ? (v < 0.001 ? v.toExponential(1) : v.toFixed(3)) : "–";
}

function ViralityResults({ r }: { r: Result["results"] }) {
  const tests = (r.tests ?? {}) as Record<string, { role: string; wilcoxon: Record<string, number | null>; sign_flip: Record<string, number | null> }>;
  const models = (r.models ?? {}) as Record<string, { auc: number | null; accuracy: number | null; n_pairs: number }>;
  const cmp = (r.comparison ?? {}) as Record<string, { diff: number | null; ci95: number[] | null }>;
  return (
    <div className="space-y-4">
      <div className="text-sm">
        {String(r.pairs_complete)} complete pairs of {String(r.pairs_total)}. Brain beats both controls:{" "}
        <b>{r.brain_beats_controls ? "yes" : "no"}</b>
      </div>
      <Table>
        <THead>
          <TR className="hover:bg-transparent">
            <TH>Measure</TH>
            <TH>Role</TH>
            <TH className="text-right">n</TH>
            <TH className="text-right">Median hit − flop</TH>
            <TH className="text-right">Wilcoxon p</TH>
            <TH className="text-right">Holm p</TH>
            <TH className="text-right">Sign-flip p</TH>
          </TR>
        </THead>
        <TBody>
          {Object.entries(tests).map(([name, t]) => (
            <TR key={name} className={t.wilcoxon.p_holm !== undefined && (t.wilcoxon.p_holm ?? 1) < 0.05 ? "bg-success/10" : undefined}>
              <TD className="font-mono text-xs">{name}</TD>
              <TD>{t.role}</TD>
              <TD className="text-right tabular">{t.wilcoxon.n}</TD>
              <TD className="text-right tabular">{t.wilcoxon.median_diff?.toFixed(4) ?? "–"}</TD>
              <TD className="text-right tabular">{p(t.wilcoxon.p)}</TD>
              <TD className="text-right tabular">{p(t.wilcoxon.p_holm)}</TD>
              <TD className="text-right tabular">{p(t.sign_flip.p)}</TD>
            </TR>
          ))}
        </TBody>
      </Table>
      <div className="grid gap-3 md:grid-cols-3">
        {Object.entries(models).map(([name, m]) => (
          <div key={name} className="rounded-lg border p-3 text-sm">
            <div className="font-medium">{name} model</div>
            <div className="text-muted-foreground">
              AUC {m.auc?.toFixed(3) ?? "–"} · accuracy {m.accuracy?.toFixed(3) ?? "–"} · {m.n_pairs} pairs
            </div>
          </div>
        ))}
      </div>
      <div className="text-sm text-muted-foreground">
        {Object.entries(cmp).map(([k, c]) => (
          <div key={k}>
            {k.replace(/_/g, " ")}: ΔAUC {c.diff?.toFixed(3) ?? "–"}
            {c.ci95 ? ` (95% CI ${c.ci95[0].toFixed(3)} to ${c.ci95[1].toFixed(3)})` : ""}
          </div>
        ))}
      </div>
    </div>
  );
}

function RetentionResults({ r }: { r: Result["results"] }) {
  type Agg = { n_videos: number; mean_rho: number | null; p_perm: number | null; p_sign: number | null };
  const owned = (r.owned ?? {}) as { brain?: Record<string, Agg>; controls?: Record<string, Agg>; ridge_brain?: { mean_heldout_rho: number | null }; ridge_controls?: { mean_heldout_rho: number | null } };
  const pub = (r.public_heatmap ?? {}) as { brain?: Record<string, Agg> };
  const proxy = (r.heatmap_vs_real_retention ?? {}) as Record<string, Agg>;
  const top = (rows: Record<string, Agg> | undefined) =>
    Object.entries(rows ?? {})
      .filter(([, a]) => a.mean_rho !== null)
      .sort((a, b) => Math.abs(b[1].mean_rho!) - Math.abs(a[1].mean_rho!))
      .slice(0, 12);
  const table = (rows: [string, Agg][]) => (
    <Table>
      <THead>
        <TR className="hover:bg-transparent">
          <TH>Predictor | target | lag</TH>
          <TH className="text-right">videos</TH>
          <TH className="text-right">mean ρ</TH>
          <TH className="text-right">perm p</TH>
          <TH className="text-right">sign p</TH>
        </TR>
      </THead>
      <TBody>
        {rows.map(([k, a]) => (
          <TR key={k}>
            <TD className="font-mono text-xs">{k}</TD>
            <TD className="text-right tabular">{a.n_videos}</TD>
            <TD className="text-right tabular">{a.mean_rho?.toFixed(3)}</TD>
            <TD className="text-right tabular">{p(a.p_perm)}</TD>
            <TD className="text-right tabular">{p(a.p_sign)}</TD>
          </TR>
        ))}
      </TBody>
    </Table>
  );
  return (
    <div className="space-y-4 text-sm">
      <div>
        {String(r.n_owned)} owned videos, {String(r.n_public)} public. Held-out ridge ρ: brain{" "}
        {owned.ridge_brain?.mean_heldout_rho?.toFixed(3) ?? "–"} vs controls {owned.ridge_controls?.mean_heldout_rho?.toFixed(3) ?? "–"}.
        Brain beats controls: <b>{r.brain_beats_controls === null ? "–" : r.brain_beats_controls ? "yes" : "no"}</b>
      </div>
      <div className="font-medium">Owned: brain curves vs real retention (strongest first)</div>
      {table(top(owned.brain))}
      <div className="font-medium">Owned: cheap controls</div>
      {table(top(owned.controls))}
      <div className="font-medium">Public heatmap vs real retention (proxy check)</div>
      {table(Object.entries(proxy))}
      <div className="font-medium">Public: brain curves vs Most replayed</div>
      {table(top(pub.brain))}
    </div>
  );
}

export function StudyDetailPage() {
  const { studyId } = useParams({ from: "/studies/$studyId" });
  const id = Number(studyId);
  const { data, isLoading, error } = useStudy(id);
  const select = useSelectMembers(id);
  const lock = useLockStudy(id);
  const fill = useFillGaps(id);
  const analyze = useAnalyze(id);
  const newVersion = useNewVersion(id);
  const navigate = useNavigate();

  if (isLoading) return <Spinner />;
  if (error || !data) return <Alert tone="destructive">{error?.message ?? "Not found"}</Alert>;
  const { study: s, completeness: c, results } = data;
  const latest = results[0];
  const actionError = select.error ?? lock.error ?? fill.error ?? analyze.error ?? newVersion.error;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={STUDY_STATUS_TONE[s.status]}>
              {s.status !== "draft" ? <Lock className="size-3" /> : null}
              {s.status}
            </Badge>
            <span className="text-xs text-muted-foreground">
              {s.slug} v{s.version} · {s.module}
              {s.locked_at ? ` · locked ${fmtDateTime(s.locked_at)} by ${s.locked_by}` : ""}
            </span>
          </div>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">{s.title}</h1>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">{s.summary}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" disabled={select.isPending} onClick={() => select.mutate()}>
            <Users /> Select members
          </Button>
          {s.status === "draft" ? (
            <Button
              variant="outline"
              disabled={lock.isPending}
              onClick={() =>
                window.confirm(
                  "Lock this study? Its criteria, measures and code are frozen (hashed). Changing the code later needs a new version.",
                ) && lock.mutate()
              }
            >
              <Lock /> Lock
            </Button>
          ) : null}
          <Button variant="outline" disabled={fill.isPending || !s.members} onClick={() => fill.mutate()}>
            <Hammer /> Fill gaps
          </Button>
          <Button disabled={analyze.isPending || s.analyzing || !s.members} onClick={() => analyze.mutate()}>
            {s.analyzing ? <Spinner className="size-3" /> : <BarChart3 />} Analyze
          </Button>
        </div>
      </div>
      {actionError ? <Alert tone="destructive">{actionError.message}</Alert> : null}
      {s.drift.changed ? (
        <Alert tone="destructive">
          This study's code changed after it was locked, so its results would no longer match the pre-registration.{" "}
          <Button
            size="sm"
            className="ml-2"
            onClick={() =>
              newVersion.mutate(undefined, {
                onSuccess: (r) => navigate({ to: "/studies/$studyId", params: { studyId: String(r.study.id) } }),
              })
            }
          >
            <RefreshCw /> Create version {s.version + 1}
          </Button>
        </Alert>
      ) : null}
      {s.status === "draft" ? (
        <Alert tone="warning">
          Draft: the measures and curve definitions below are proposals and are <b>not approved</b>. Lock the study to
          pre-register them before the data comes in.
        </Alert>
      ) : null}
      {select.data ? <Alert>{select.data.rules}</Alert> : null}
      {fill.data ? (
        <Alert>
          Pulled {fill.data.pulled} videos and issued {fill.data.jobs.length} jobs
          {fill.data.nothing_to_do.length ? `; nothing to do for ${fill.data.nothing_to_do.join(", ")}` : ""}.{" "}
          <Link to="/jobs" className="text-primary underline">
            Watch them on Jobs
          </Link>
        </Alert>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Criteria</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>{s.selection?.rules ?? "Not selected yet. Use Select members to run the criteria."}</p>
            <pre className="overflow-auto rounded bg-muted p-2 text-xs">{JSON.stringify(s.params, null, 2)}</pre>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Measures {s.status === "draft" ? "(draft)" : "(locked)"}</CardTitle>
          </CardHeader>
          <CardContent className="max-h-72 overflow-auto">
            <Table>
              <TBody>
                {s.measures.map((m, i) => (
                  <TR key={i}>
                    <TD className="font-mono text-xs">{String(m.name)}</TD>
                    <TD className="text-xs text-muted-foreground">{String(m.role ?? m.kind ?? "")}</TD>
                    <TD className="text-xs">{String(m.definition ?? "")}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Methodology</CardTitle>
        </CardHeader>
        <CardContent className="prose-sm max-w-none space-y-2 text-sm [&_h2]:mt-3 [&_h2]:font-semibold [&_li]:ml-5 [&_li]:list-disc">
          <Markdown>{s.methodology_md}</Markdown>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
          <CardTitle className="text-base">
            Members ({c.members.length}) · {c.complete} complete · {c.missing_cells} missing results
          </CardTitle>
          <div className="flex gap-2">
            <Button asChild variant="outline" size="sm">
              <a href={`/api/studies/${s.id}/members.csv`}>
                <Download /> CSV
              </a>
            </Button>
            <Button asChild variant="outline" size="sm">
              <a href={`/api/studies/${s.id}/members.parquet`}>
                <Download /> Parquet
              </a>
            </Button>
          </div>
        </CardHeader>
        <CardContent className="max-h-[32rem] overflow-auto">
          <Table>
            <THead>
              <TR className="hover:bg-transparent">
                <TH>Video</TH>
                <TH>Role</TH>
                <TH>Group</TH>
                <TH className="text-right">Outlier</TH>
                <TH className="text-right">Length</TH>
                {c.requirements.map((r) => (
                  <TH key={r} className="text-center text-[10px]">
                    {r.split("@")[0]}
                  </TH>
                ))}
              </TR>
            </THead>
            <TBody>
              {c.members.map((m) => (
                <TR key={m.video_id} className={m.complete ? undefined : "bg-warning/5"}>
                  <TD className="max-w-xs">
                    <Link to="/videos/$videoId" params={{ videoId: String(m.video_id) }} className="line-clamp-1 hover:underline">
                      {m.title}
                    </Link>
                    <div className="text-xs text-muted-foreground">{m.reason}</div>
                  </TD>
                  <TD>
                    <Badge tone={m.role === "hit" || m.role === "owned" ? "success" : m.role === "flop" ? "destructive" : "default"}>
                      {m.role}
                    </Badge>
                  </TD>
                  <TD className="text-xs text-muted-foreground">{m.group}</TD>
                  <TD className="text-right tabular">{fmtRatio(m.outlier_score)}</TD>
                  <TD className="text-right tabular">{fmtDuration(m.duration_s)}</TD>
                  {c.requirements.map((r) => (
                    <TD key={r} className="text-center">
                      <StatusBadge status={m.requirements[r]} />
                    </TD>
                  ))}
                </TR>
              ))}
            </TBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Results</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {!latest ? (
            <p className="text-sm text-muted-foreground">Not analyzed yet.</p>
          ) : (
            <>
              <div className="text-xs text-muted-foreground">
                Computed {fmtDateTime(latest.computed_at)} on code {latest.code_version} with {latest.n_members} members.
              </div>
              {s.slug === "tribe-virality" ? <ViralityResults r={latest.results} /> : null}
              {s.slug === "tribe-retention" ? <RetentionResults r={latest.results} /> : null}
              {latest.figures.length ? (
                <div className="grid gap-3 md:grid-cols-2">
                  {latest.figures.map((f) => (
                    <img key={f} src={f} alt="" className="rounded-lg border bg-white" />
                  ))}
                </div>
              ) : null}
              <details>
                <summary className="cursor-pointer text-sm text-muted-foreground">Raw results JSON</summary>
                <pre className="mt-2 max-h-96 overflow-auto rounded bg-muted p-2 text-xs">{JSON.stringify(latest.results, null, 2)}</pre>
              </details>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
