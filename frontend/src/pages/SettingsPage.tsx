import { CheckCircle2, XCircle } from "lucide-react";
import { useState } from "react";
import type { Schemas } from "@/api/client";
import { DataCard } from "@/components/DataCard";
import { OwnerSyncCard } from "@/components/OwnerSyncCard";
import { useChangePassword, useSaveSchedules, useSettings } from "@/api/queries";
import { Button } from "@/components/ui/button";
import {
  Alert,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Input,
  Label,
  PageHeader,
  Spinner,
} from "@/components/ui/primitives";
import { fmtBytes } from "@/lib/format";

const SCHEDULE_FIELDS: { key: string; label: string; unit: string }[] = [
  { key: "snapshot_young_days", label: "A video counts as young for", unit: "days" },
  { key: "snapshot_young_interval_hours", label: "Snapshot young videos every", unit: "hours" },
  { key: "snapshot_old_interval_hours", label: "Snapshot older videos every", unit: "hours" },
  { key: "channel_snapshot_interval_hours", label: "Snapshot channels every", unit: "hours" },
  { key: "new_upload_check_hours", label: "Check for new uploads every", unit: "hours" },
  { key: "scheduler_tick_minutes", label: "Scheduler tick", unit: "minutes" },
  { key: "daily_quota_budget", label: "Daily YouTube quota budget", unit: "units" },
  { key: "runner_timeout_minutes", label: "Requeue a job when its runner is silent for", unit: "minutes" },
  { key: "download_pause_seconds", label: "Pause between downloads", unit: "seconds" },
  { key: "backup_interval_hours", label: "Back up the database every", unit: "hours" },
  { key: "backups_to_keep", label: "Backups to keep", unit: "" },
];

const KEY_LABELS: Record<string, string> = {
  youtube_api_key: "YouTube Data API key",
  runner_token: "Runner token",
  secret_key: "Session secret key",
};

export function SettingsPage() {
  const { data, isLoading, error } = useSettings();
  if (isLoading) return <Spinner />;
  if (error || !data) return <Alert tone="destructive">{error?.message ?? "Failed to load"}</Alert>;
  return <SettingsView data={data} />;
}

function SettingsView({ data }: { data: Schemas["Settings"] }) {
  const save = useSaveSchedules();
  const changePw = useChangePassword();
  const [form, setForm] = useState<Record<string, string>>(() =>
    Object.fromEntries(Object.entries(data.schedules).map(([k, v]) => [k, String(v)])),
  );
  const [pw, setPw] = useState({ current: "", next: "", confirm: "" });
  const pwMismatch = pw.next !== "" && pw.next !== pw.confirm;

  return (
    <div className="space-y-4">
      <PageHeader title="Settings" />

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Keys</CardTitle>
          <CardDescription>
            Secrets live in <code>backend/.env</code> and are never shown here. Edit that file and restart the server to
            change them.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          {Object.entries(data.keys).map(([k, isSet]) => (
            <div key={k} className="flex items-center gap-2">
              {isSet ? <CheckCircle2 className="size-4 text-success" /> : <XCircle className="size-4 text-destructive" />}
              <span>{KEY_LABELS[k] ?? k}</span>
              <span className="text-muted-foreground">{isSet ? "set" : "not set"}</span>
            </div>
          ))}
        </CardContent>
      </Card>

      <OwnerSyncCard />

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Data folder</CardTitle>
          <CardDescription>
            Set by <code>DATA_ROOT</code> in <code>backend/.env</code>.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-1 text-sm">
          <div>
            <code>{data.data_root}</code>
          </div>
          <div className="text-muted-foreground">
            {fmtBytes(data.disk.data_bytes)} used by creator-lab, {fmtBytes(data.disk.drive_free)} free on the drive.
          </div>
          <div className="text-muted-foreground">
            Downloads are capped at {data.max_video_height}p (4K is never stored). BrainThing:{" "}
            <code>{data.brainthing_dir ?? "not set"}</code>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Schedules</CardTitle>
        </CardHeader>
        <CardContent>
          <form
            className="grid gap-3 md:grid-cols-2"
            onSubmit={(e) => {
              e.preventDefault();
              save.mutate(Object.fromEntries(Object.entries(form).map(([k, v]) => [k, Number(v)])));
            }}
          >
            {SCHEDULE_FIELDS.map((f) => (
              <div key={f.key} className="flex items-center justify-between gap-3">
                <Label htmlFor={f.key} className="font-normal">
                  {f.label}
                </Label>
                <div className="flex items-center gap-2">
                  <Input
                    id={f.key}
                    type="number"
                    className="w-24 text-right"
                    value={form[f.key] ?? ""}
                    onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                  />
                  <span className="w-14 text-xs text-muted-foreground">{f.unit}</span>
                </div>
              </div>
            ))}
            <div className="flex items-center gap-3 md:col-span-2">
              <Button type="submit" disabled={save.isPending}>
                Save schedules
              </Button>
              {save.isSuccess ? <span className="text-sm text-success">Saved</span> : null}
              {save.error ? <span className="text-sm text-destructive">{save.error.message}</span> : null}
            </div>
          </form>
        </CardContent>
      </Card>

      <DataCard intervalHours={data.schedules.backup_interval_hours} keep={data.schedules.backups_to_keep} />

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Change password</CardTitle>
        </CardHeader>
        <CardContent>
          <form
            className="flex max-w-sm flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              changePw.mutate(
                { current_password: pw.current, new_password: pw.next },
                { onSuccess: () => setPw({ current: "", next: "", confirm: "" }) },
              );
            }}
          >
            <Input
              type="password"
              placeholder="Current password"
              autoComplete="current-password"
              value={pw.current}
              onChange={(e) => setPw({ ...pw, current: e.target.value })}
            />
            <Input
              type="password"
              placeholder="New password (10+ characters)"
              autoComplete="new-password"
              value={pw.next}
              onChange={(e) => setPw({ ...pw, next: e.target.value })}
            />
            <Input
              type="password"
              placeholder="Repeat new password"
              autoComplete="new-password"
              value={pw.confirm}
              onChange={(e) => setPw({ ...pw, confirm: e.target.value })}
            />
            {pwMismatch ? <span className="text-sm text-destructive">Passwords don't match.</span> : null}
            {changePw.error ? <Alert tone="destructive">{changePw.error.message}</Alert> : null}
            {changePw.isSuccess ? <span className="text-sm text-success">Password changed.</span> : null}
            <Button type="submit" disabled={changePw.isPending || pwMismatch || pw.next.length < 10 || !pw.current}>
              Change password
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
