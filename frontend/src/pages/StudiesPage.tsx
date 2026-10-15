import { Link } from "@tanstack/react-router";
import { Lock } from "lucide-react";
import { useStudies } from "@/api/studies";
import { Alert, Badge, Card, CardContent, EmptyState, PageHeader, Spinner } from "@/components/ui/primitives";
import { fmtAgo } from "@/lib/format";

export const STUDY_STATUS_TONE = { draft: "outline", locked: "primary", collecting: "warning", analyzed: "success" } as const;

export function StudiesPage() {
  const { data, isLoading, error } = useStudies();
  return (
    <div>
      <PageHeader
        title="Studies"
        description="Pre-registered questions: criteria pick the videos, measures are locked before the data is in, results are computed here."
      />
      {isLoading ? (
        <Spinner />
      ) : error ? (
        <Alert tone="destructive">{error.message}</Alert>
      ) : !data?.studies.length ? (
        <EmptyState title="No studies" />
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {data.studies.map((s) => (
            <Link key={s.id} to="/studies/$studyId" params={{ studyId: String(s.id) }}>
              <Card className="h-full transition-colors hover:border-primary/60">
                <CardContent className="space-y-2 p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={STUDY_STATUS_TONE[s.status]}>
                      {s.status === "locked" ? <Lock className="size-3" /> : null}
                      {s.status}
                    </Badge>
                    <span className="text-xs text-muted-foreground">
                      {s.slug} v{s.version}
                    </span>
                  </div>
                  <div className="font-medium">{s.title}</div>
                  <p className="line-clamp-3 text-sm text-muted-foreground">{s.summary}</p>
                  <div className="text-xs text-muted-foreground">
                    {s.members} members · last analysis {s.last_result_at ? fmtAgo(s.last_result_at) : "never"}
                  </div>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
