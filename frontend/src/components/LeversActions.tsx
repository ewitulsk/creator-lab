import { Link } from "@tanstack/react-router";
import { Scale } from "lucide-react";
import { useState } from "react";
import { fmtUsd, type LeverEstimate, useLeversConfig, useLeversEstimate, useLeversJobCost, usePairwise, useStudyPairwise } from "@/api/levers";
import { Button } from "@/components/ui/button";
import { Alert, Card, CardContent, CardDescription, CardHeader, CardTitle, Select, Stat } from "@/components/ui/primitives";

function JobLink({ id }: { id: number }) {
  return (
    <Link to="/jobs/$jobId" params={{ jobId: String(id) }} className="text-primary underline">
      job #{id}
    </Link>
  );
}

/** Judge every matched pair of a study with Gemini, after showing the estimate. */
export function StudyPairwiseButton({ studyId }: { studyId: number }) {
  const config = useLeversConfig();
  const run = useStudyPairwise(studyId);
  const [est, setEst] = useState<{ estimate: LeverEstimate; pairs?: number } | null>(null);
  return (
    <>
      <Button
        variant="outline"
        disabled={!config.data?.key_set || run.isPending}
        title={config.data?.key_set ? "Gemini judges each matched pair in both orders" : "Set GEMINI_API_KEY in backend/.env"}
        onClick={() => run.mutate(true, { onSuccess: (r) => setEst({ estimate: r.estimate, pairs: r.pairs }) })}
      >
        <Scale /> Judge pairs (Gemini)
      </Button>
      {est && !run.data?.job ? (
        <Alert className="basis-full">
          {est.pairs} pairs, {est.estimate.calls} calls on {est.estimate.model}, about {fmtUsd(est.estimate.cost_usd)}.{" "}
          <Button size="sm" disabled={run.isPending} onClick={() => run.mutate(false)}>
            Confirm and queue
          </Button>
        </Alert>
      ) : null}
      {run.data?.job ? (
        <Alert className="basis-full">
          Queued <JobLink id={run.data.job.id} />.
        </Alert>
      ) : null}
      {run.error ? <Alert tone="destructive" className="basis-full">{run.error.message}</Alert> : null}
    </>
  );
}

type Pickable = { id: number; title: string; channel: string };

/** Compare page: pick two videos and have Gemini judge which is stronger on each lever. */
export function ComparePairwiseCard({ videos }: { videos: Pickable[] }) {
  const config = useLeversConfig();
  const estimate = useLeversEstimate();
  const start = usePairwise();
  const [a, setA] = useState<number | "">("");
  const [b, setB] = useState<number | "">("");
  const ready = a !== "" && b !== "" && a !== b;
  const est = estimate.data?.estimate;
  const options = videos.map((v) => (
    <option key={v.id} value={v.id}>
      {v.channel}: {v.title.slice(0, 70)}
    </option>
  ));
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Scale className="size-4" /> Pairwise levers (Gemini)
        </CardTitle>
        <CardDescription>
          Both videos are uploaded blinded and judged lever by lever, once in each order to cancel position bias.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <div className="grid gap-2 md:grid-cols-2">
          <Select value={a} onChange={(e) => setA(e.target.value ? Number(e.target.value) : "")} aria-label="Video A">
            <option value="">Video A…</option>
            {options}
          </Select>
          <Select value={b} onChange={(e) => setB(e.target.value ? Number(e.target.value) : "")} aria-label="Video B">
            <option value="">Video B…</option>
            {options}
          </Select>
        </div>
        <Button
          variant="outline"
          disabled={!ready || !config.data?.key_set || estimate.isPending}
          title={config.data?.key_set ? undefined : "Set GEMINI_API_KEY in backend/.env"}
          onClick={() => ready && estimate.mutate({ pairs: [[a, b]] })}
        >
          Estimate
        </Button>
        {est && ready && !start.data ? (
          <Alert>
            {est.calls} calls on {est.model}, about {fmtUsd(est.cost_usd)}.{" "}
            <Button size="sm" disabled={start.isPending} onClick={() => start.mutate([[a, b]])}>
              Confirm and queue
            </Button>
          </Alert>
        ) : null}
        {start.data?.job ? (
          <Alert>
            Queued <JobLink id={start.data.job.id} />.
          </Alert>
        ) : null}
        {start.error ? <Alert tone="destructive">{start.error.message}</Alert> : null}
      </CardContent>
    </Card>
  );
}

/** Job page: estimated vs actual Gemini cost. */
export function LeversJobCost({ jobId }: { jobId: number }) {
  const { data } = useLeversJobCost(jobId, true);
  if (!data) return null;
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      <Stat label="Gemini calls" value={data.actual.calls} hint={data.estimate ? `estimated ${data.estimate.calls}` : undefined} />
      <Stat
        label="Cost so far"
        value={fmtUsd(data.actual.cost_usd)}
        hint={data.estimate ? `estimated ${fmtUsd(data.estimate.cost_usd)}` : undefined}
      />
      <Stat label="Input tokens" value={data.actual.input_tokens.toLocaleString()} />
      <Stat label="Output + thinking" value={data.actual.output_tokens.toLocaleString()} />
    </div>
  );
}
