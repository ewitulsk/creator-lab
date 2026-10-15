import { Gauge, Sparkles } from "lucide-react";
import { useState } from "react";
import {
  fmtUsd,
  LEVERS,
  type LeverEstimate,
  SYSTEM_1,
  useLeversConfig,
  useLeversEstimate,
  useScoreLevers,
  useVideoLevers,
} from "@/api/levers";
import { Button } from "@/components/ui/button";
import { Alert, Badge, Card, CardContent, CardHeader, CardTitle, Select, Spinner } from "@/components/ui/primitives";
import { fmtDuration } from "@/lib/format";

const FLAG_TEXT: Record<string, string> = {
  high_spread: "runs disagree (spread ≥ 2)",
  possibly_contaminated: "Gemini may have recognised this video",
  not_blinded_url_input: "scored from the YouTube URL (not blinded)",
  url_rejected_fell_back_to_upload: "URL rejected; scored from the upload",
};

function ScoreBar({ score, spread }: { score: number | null; spread: number | null }) {
  const pct = score === null ? 0 : ((score - 1) / 4) * 100;
  const lo = score === null || spread === null ? null : Math.max(1, score - spread / 2);
  const hi = score === null || spread === null ? null : Math.min(5, score + spread / 2);
  return (
    <div className="relative h-2 w-full rounded bg-muted" title={spread !== null ? `spread ${spread}` : undefined}>
      {lo !== null && hi !== null ? (
        <div className="absolute h-2 rounded bg-primary/25" style={{ left: `${((lo - 1) / 4) * 100}%`, width: `${((hi - lo) / 4) * 100}%` }} />
      ) : null}
      <div className="absolute top-[-2px] size-3 -translate-x-1/2 rounded-full bg-primary" style={{ left: `${pct}%` }} />
    </div>
  );
}

/** The six levers for one video: median of the runs, spread, reasons and clickable evidence. */
export function LeversPanel({ videoId, onSeek }: { videoId: number; onSeek?: (s: number) => void }) {
  const { data, isLoading } = useVideoLevers(videoId);
  const config = useLeversConfig();
  const estimate = useLeversEstimate();
  const score = useScoreLevers();
  const [pick, setPick] = useState(0);
  const [open, setOpen] = useState<string | null>(null);
  const scoring = data?.scorings[pick];
  const est = estimate.data?.estimate as LeverEstimate | undefined;

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <Gauge className="size-4" /> Levers
        </CardTitle>
        <div className="flex items-center gap-2">
          {data && data.scorings.length > 1 ? (
            <Select value={pick} onChange={(e) => setPick(Number(e.target.value))} aria-label="Scoring">
              {data.scorings.map((s, i) => (
                <option key={i} value={i}>
                  {s.model} · {s.input_mode} · rubric v{s.rubric_version} ({s.rubric_hash?.slice(0, 7)})
                </option>
              ))}
            </Select>
          ) : null}
          <Button
            size="sm"
            variant="outline"
            disabled={!config.data?.key_set || estimate.isPending}
            title={config.data?.key_set ? "Estimate the cost first" : "Set GEMINI_API_KEY in backend/.env"}
            onClick={() => estimate.mutate({ video_ids: [videoId] })}
          >
            <Sparkles /> Score with Gemini
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {est && !score.data ? (
          <Alert>
            {est.videos ? (
              <div className="flex flex-wrap items-center gap-2">
                <span>
                  {est.calls} calls on {est.model} ({est.runs_per_video} runs + probe), about {fmtUsd(est.cost_usd)}.
                </span>
                <Button size="sm" disabled={score.isPending} onClick={() => score.mutate([videoId])}>
                  Confirm and queue
                </Button>
                <span className="text-xs text-muted-foreground">{est.assumptions}</span>
              </div>
            ) : (
              "Already scored with this model and rubric."
            )}
          </Alert>
        ) : null}
        {score.data?.job ? <Alert>Queued job #{score.data.job.id}. The server's Gemini worker picks it up.</Alert> : null}
        {score.error ? <Alert tone="destructive">{score.error.message}</Alert> : null}
        {isLoading ? (
          <Spinner />
        ) : !scoring ? (
          <p className="text-muted-foreground">
            Not scored yet.{" "}
            {config.data && !config.data.key_set ? "Add GEMINI_API_KEY to backend/.env to enable scoring." : ""}
          </p>
        ) : (
          <>
            <div className="flex flex-wrap gap-1 text-xs text-muted-foreground">
              <Badge tone="outline">{scoring.model}</Badge>
              <Badge tone={scoring.input_mode === "upload" ? "outline" : "warning"}>{scoring.input_mode}</Badge>
              <Badge tone="outline">rubric v{scoring.rubric_version}</Badge>
              <Badge tone="outline">{Object.keys(scoring.runs).length} runs, median shown</Badge>
              {data?.probe?.contaminated ? <Badge tone="destructive">possibly contaminated</Badge> : null}
            </div>
            {LEVERS.map((lever) => {
              const x = scoring.aggregate[lever];
              if (!x) return null;
              const flags = x.flags.filter((f) => f !== "not_blinded_url_input" || scoring.input_mode !== "upload");
              return (
                <div key={lever} className="space-y-1">
                  <button
                    type="button"
                    className="flex w-full cursor-pointer items-center gap-3 text-left"
                    onClick={() => setOpen(open === lever ? null : lever)}
                  >
                    <span className="w-32 font-medium capitalize">
                      {lever}
                      <span className="ml-1 text-[10px] font-normal text-muted-foreground">
                        S{SYSTEM_1.has(lever) ? 1 : 2}
                      </span>
                    </span>
                    <span className="w-10 tabular">{x.score?.toFixed(1)}</span>
                    <span className="flex-1">
                      <ScoreBar score={x.score} spread={x.spread} />
                    </span>
                    {x.flags.includes("high_spread") ? <Badge tone="warning">spread {x.spread}</Badge> : null}
                  </button>
                  {open === lever ? (
                    <div className="ml-32 space-y-1 text-xs">
                      <p>{x.reason}</p>
                      <div className="flex flex-wrap gap-1">
                        {x.evidence.map((e, i) => (
                          <button
                            key={i}
                            type="button"
                            className="cursor-pointer rounded border px-1.5 py-0.5 hover:bg-accent"
                            onClick={() => onSeek?.(e.t_start)}
                            title={e.note}
                          >
                            {fmtDuration(e.t_start)} {e.note.slice(0, 40)}
                          </button>
                        ))}
                      </div>
                      <div className="text-muted-foreground">
                        {Object.entries(x.subscores)
                          .map(([k, v]) => `${k.replaceAll("_", " ")} ${v}`)
                          .join(" · ")}
                        {" · confidence "}
                        {x.confidence}
                      </div>
                      {flags.length ? (
                        <div className="text-amber-600">{flags.map((f) => FLAG_TEXT[f] ?? f).join("; ")}</div>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              );
            })}
            {data?.probe ? (
              <p className="text-xs text-muted-foreground">
                Contamination probe:{" "}
                {data.probe.identified
                  ? `guessed "${data.probe.creator_guess}" / "${data.probe.title_guess}" (${data.probe.contaminated ? "correct: scores flagged" : "wrong"})`
                  : "could not identify the video"}
                .
              </p>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}
