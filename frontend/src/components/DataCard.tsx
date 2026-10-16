import { DatabaseBackup, Download } from "lucide-react";
import { useState } from "react";
import { exportUrl, useBackups, useCreateBackup } from "@/api/data";
import { useAnalyzers } from "@/api/jobs";
import { Button } from "@/components/ui/button";
import {
  Alert,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Select,
  Spinner,
} from "@/components/ui/primitives";
import { fmtBytes, fmtDateTime } from "@/lib/format";

const EXPORTS = [
  { kind: "videos", label: "Videos", hint: "every stored video with its latest stats and outlier score" },
  { kind: "video_snapshots", label: "Video snapshots", hint: "view, like and comment history" },
  { kind: "channel_snapshots", label: "Channel snapshots", hint: "subscriber and view history" },
] as const;

export function DataCard({ intervalHours, keep }: { intervalHours: number; keep: number }) {
  const backups = useBackups();
  const create = useCreateBackup();
  const analyzers = useAnalyzers();
  const [analyzer, setAnalyzer] = useState("outlier_score");
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Exports and backups</CardTitle>
        <CardDescription>
          Downloads for your own analysis. The database is backed up every {intervalHours} h (newest {keep} kept) into
          the data folder's backups directory; change both under Schedules.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <ul className="space-y-2">
          {EXPORTS.map((e) => (
            <li key={e.kind} className="flex flex-wrap items-center gap-2">
              <span className="min-w-40 font-medium">{e.label}</span>
              <span className="flex-1 text-muted-foreground">{e.hint}</span>
              {(["csv", "parquet"] as const).map((fmt) => (
                <Button key={fmt} asChild variant="outline" size="sm">
                  <a href={exportUrl(e.kind, fmt)} download>
                    <Download /> {fmt.toUpperCase()}
                  </a>
                </Button>
              ))}
            </li>
          ))}
          <li className="flex flex-wrap items-center gap-2">
            <span className="min-w-40 font-medium">Analyzer results</span>
            <span className="flex-1">
              <Select value={analyzer} onChange={(e) => setAnalyzer(e.target.value)} aria-label="Analyzer">
                {(analyzers.data?.analyzers ?? [])
                  .filter((a) => a.is_current)
                  .map((a) => (
                    <option key={a.ref} value={a.name}>
                      {a.title}
                    </option>
                  ))}
              </Select>
            </span>
            {(["csv", "parquet"] as const).map((fmt) => (
              <Button key={fmt} asChild variant="outline" size="sm">
                <a href={exportUrl("analyzer", fmt, { analyzer })} download>
                  <Download /> {fmt.toUpperCase()}
                </a>
              </Button>
            ))}
          </li>
        </ul>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => create.mutate()} disabled={create.isPending}>
            <DatabaseBackup /> {create.isPending ? "Backing up…" : "Back up now"}
          </Button>
          {create.error ? <Alert tone="destructive">{create.error.message}</Alert> : null}
        </div>
        {backups.isLoading ? (
          <Spinner />
        ) : backups.data?.backups.length ? (
          <ul className="space-y-1">
            {backups.data.backups.map((b) => (
              <li key={b.name} className="flex items-center gap-3">
                <a className="font-mono text-xs hover:underline" href={`/api/backups/${encodeURIComponent(b.name)}`} download>
                  {b.name}
                </a>
                <span className="text-muted-foreground">{fmtBytes(b.bytes)}</span>
                <span className="text-muted-foreground">{fmtDateTime(b.created_at)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-muted-foreground">No backups yet.</p>
        )}
      </CardContent>
    </Card>
  );
}
