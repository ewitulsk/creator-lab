import { MonitorPlay as Youtube } from "lucide-react";
import type { Schemas } from "@/api/client";
import { useOwnerDisconnect, useOwnerSignIn, useOwnerStatus, useOwnerSync } from "@/api/owner";
import { Button } from "@/components/ui/button";
import { Alert, Badge, Card, CardContent, CardDescription, CardHeader, CardTitle, Spinner } from "@/components/ui/primitives";
import { fmtAgo, fmtDateTime } from "@/lib/format";

const DOC = "https://github.com/ewitulsk/creator-lab/blob/main/docs/youtube-oauth.md";

function SyncSummary({ s }: { s: Schemas["OwnerSync"] }) {
  const c = s.counts;
  return (
    <div className="space-y-1 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={s.status === "done" ? "success" : s.status === "failed" ? "destructive" : "primary"}>{s.status}</Badge>
        <span className="text-muted-foreground">
          {s.mode === "full" ? "re-pull everything" : "new only"} · started {fmtDateTime(s.started_at)}
          {s.finished_at ? ` · finished ${fmtAgo(s.finished_at)}` : ""}
        </span>
      </div>
      <div className="text-muted-foreground">
        {c.videos_listed ?? 0} uploads listed · {c.videos_synced ?? 0} synced
        {c.videos_failed ? ` · ${c.videos_failed} failed` : ""} · {c.retention_curves ?? 0} retention curves ·{" "}
        {c.daily_rows ?? 0} daily rows
      </div>
      {Object.keys(s.unavailable).length ? (
        <div className="text-xs text-muted-foreground">
          Not available from the API:{" "}
          {Object.entries(s.unavailable)
            .map(([k, v]) => `${k} (${v.join(", ")})`)
            .join("; ")}
        </div>
      ) : null}
      {s.error ? <Alert tone="destructive">{s.error}</Alert> : null}
      {s.status === "running" && s.log.length ? (
        <pre className="max-h-32 overflow-auto rounded bg-muted p-2 text-xs">{s.log.slice(-6).join("\n")}</pre>
      ) : null}
    </div>
  );
}

export function OwnerSyncCard() {
  const { data, isLoading } = useOwnerStatus();
  const signIn = useOwnerSignIn();
  const sync = useOwnerSync();
  const disconnect = useOwnerDisconnect();
  const params = new URLSearchParams(window.location.search);
  const justConnected = params.get("youtube") === "connected";
  const error = params.get("youtube") === "error" ? params.get("message") : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Youtube className="size-4" /> YouTube owner sync
        </CardTitle>
        <CardDescription>
          Sign in to your own channel to pull what only the owner can see: retention curves, watch time, traffic sources,
          demographics, and private or unlisted uploads. There's no schedule: sync whenever you like.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading || !data ? (
          <Spinner />
        ) : !data.configured ? (
          <Alert tone="warning">
            Not set up yet. Follow{" "}
            <a href={DOC} target="_blank" rel="noreferrer" className="underline">
              docs/youtube-oauth.md
            </a>{" "}
            (5 minutes in Google Cloud), put GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET and GOOGLE_OAUTH_REDIRECT_URI in{" "}
            <code>backend/.env</code>, and restart the server.
          </Alert>
        ) : (
          <>
            {data.redirect_uri_problem ? <Alert tone="destructive">{data.redirect_uri_problem}</Alert> : null}
            <p className="text-xs text-muted-foreground">
              Redirect URI (must match the Google console exactly): <code>{data.redirect_uri}</code>
            </p>
            {error ? <Alert tone="destructive">Sign-in failed: {error}</Alert> : null}
            {data.credentials.length === 0 ? (
              <Button onClick={() => signIn.mutate()} disabled={signIn.isPending}>
                <Youtube /> Sign in with YouTube
              </Button>
            ) : null}
            {data.credentials.map((c) => {
              const running = c.last_sync?.status === "running";
              return (
                <div key={c.channel.id} className="space-y-3 rounded-lg border p-3">
                  <div className="flex flex-wrap items-center gap-3">
                    {c.channel.thumbnail_url ? (
                      <img src={c.channel.thumbnail_url} alt="" className="size-9 rounded-full" referrerPolicy="no-referrer" />
                    ) : null}
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 font-medium">
                        {c.channel.title} <Badge tone="primary">owned</Badge>
                        <Badge tone={c.status === "active" ? "success" : "destructive"}>{c.status}</Badge>
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {c.channel.handle} · connected {fmtAgo(c.connected_at)} by {c.connected_by} · {c.synced_videos} videos
                        synced · last sync {c.last_finished_sync ? fmtAgo(c.last_finished_sync.finished_at) : "never"}
                      </div>
                    </div>
                  </div>
                  {c.status !== "active" ? (
                    <Alert tone="warning">
                      {c.status_detail}{" "}
                      <Button size="sm" className="ml-2" onClick={() => signIn.mutate()}>
                        Sign in again
                      </Button>
                    </Alert>
                  ) : null}
                  {justConnected && !running ? (
                    <Alert>Connected. Pull everything now, or only what's new since the last sync.</Alert>
                  ) : null}
                  <div className="flex flex-wrap gap-2">
                    <Button
                      disabled={running || c.status !== "active" || sync.isPending}
                      onClick={() => sync.mutate({ channelId: c.channel.id, mode: "full" })}
                      title="Refresh all data; keeps old retention snapshots as earlier versions"
                    >
                      Re-pull everything
                    </Button>
                    <Button
                      variant="outline"
                      disabled={running || c.status !== "active" || sync.isPending}
                      onClick={() => sync.mutate({ channelId: c.channel.id, mode: "new" })}
                      title="Videos published since the last sync, plus any never synced"
                    >
                      Pull new only
                    </Button>
                    <Button variant="ghost" onClick={() => signIn.mutate()}>
                      Sign in again
                    </Button>
                    <Button
                      variant="ghost"
                      className="text-destructive"
                      onClick={() => window.confirm("Disconnect and revoke access?") && disconnect.mutate(c.channel.id)}
                    >
                      Disconnect
                    </Button>
                  </div>
                  {sync.error ? <Alert tone="destructive">{sync.error.message}</Alert> : null}
                  {c.last_sync ? <SyncSummary s={c.last_sync} /> : null}
                </div>
              );
            })}
          </>
        )}
        {signIn.error ? <Alert tone="destructive">{signIn.error.message}</Alert> : null}
      </CardContent>
    </Card>
  );
}
