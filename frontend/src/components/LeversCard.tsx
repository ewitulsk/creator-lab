import { CheckCircle2, PlugZap, XCircle } from "lucide-react";
import { useState } from "react";
import { type LeversConfig, useLeversConfig, useSaveLeversConfig, useTestLevers } from "@/api/levers";
import { Button } from "@/components/ui/button";
import {
  Alert,
  Badge,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Input,
  Label,
  Select,
  Spinner,
} from "@/components/ui/primitives";

type Cfg = LeversConfig["config"];

/** Gemini lever scoring: key status (never the key), model, input mode and run settings. */
export function LeversCard() {
  const { data, isLoading } = useLeversConfig();
  if (isLoading || !data) return <Spinner />;
  return <LeversForm data={data} />;
}

function LeversForm({ data }: { data: LeversConfig }) {
  const save = useSaveLeversConfig();
  const test = useTestLevers();
  const [form, setForm] = useState<Cfg>(data.config);
  const set = <K extends keyof Cfg>(k: K, v: Cfg[K]) => setForm((f) => ({ ...f, [k]: v }));
  const dirty = JSON.stringify(form) !== JSON.stringify(data.config);
  const model = data.models.find((m) => m.id === form.model);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          Lever scoring (Gemini)
          {data.key_set ? <Badge tone="success">key set</Badge> : <Badge tone="destructive">no key</Badge>}
        </CardTitle>
        <CardDescription>
          Gemini watches each video and scores six levers 1 to 5 against{" "}
          <code className="text-xs">{data.rubric.path}</code> (v{data.rubric.version}, {data.rubric.hash.slice(0, 7)}).
          {data.key_set ? "" : " Add GEMINI_API_KEY to backend/.env and restart the app to enable it."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1">
            <Label>Model</Label>
            <Select className="w-full" value={form.model} onChange={(e) => set("model", e.target.value)}>
              {data.models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.title} ({m.id})
                </option>
              ))}
            </Select>
            {model ? (
              <p className="text-xs text-muted-foreground">
                ${model.price_per_mtok[0]} in / ${model.price_per_mtok[1]} out per 1M tokens today
              </p>
            ) : null}
          </div>
          <div className="space-y-1">
            <Label>Video input</Label>
            <Select className="w-full" value={form.input_mode} onChange={(e) => set("input_mode", e.target.value as Cfg["input_mode"])}>
              {data.input_modes.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.title}
                </option>
              ))}
            </Select>
            {form.input_mode !== "upload" ? (
              <p className="text-xs text-amber-600">
                Not blinded: a URL can let Gemini tie the video to its title, channel and popularity.
              </p>
            ) : null}
          </div>
          <div className="space-y-1">
            <Label>Upload height (px)</Label>
            <Input type="number" min={144} max={720} value={form.upload_height} onChange={(e) => set("upload_height", Number(e.target.value))} />
          </div>
          <div className="space-y-1">
            <Label>Upload frames per second</Label>
            <Input type="number" min={1} max={10} value={form.upload_fps} onChange={(e) => set("upload_fps", Number(e.target.value))} />
          </div>
          <div className="space-y-1">
            <Label>Runs per video (median is kept)</Label>
            <Input type="number" min={1} max={9} value={form.runs} onChange={(e) => set("runs", Number(e.target.value))} />
          </div>
          <div className="space-y-1">
            <Label>Temperature</Label>
            <Input
              type="number"
              step={0.1}
              min={0}
              max={2}
              value={form.temperature}
              onChange={(e) => set("temperature", Number(e.target.value))}
            />
          </div>
        </div>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={form.agentic} onChange={(e) => set("agentic", e.target.checked)} />
          Agentic video understanding (the model explores the timeline)
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <Button disabled={!dirty || save.isPending} onClick={() => save.mutate(form)}>
            Save
          </Button>
          <Button variant="outline" disabled={!data.key_set || test.isPending} onClick={() => test.mutate(form.model)}>
            <PlugZap /> Test connection
          </Button>
          {test.data ? (
            test.data.ok ? (
              <span className="flex items-center gap-1 text-success">
                <CheckCircle2 className="size-4" /> {test.data.model} is reachable
              </span>
            ) : (
              <span className="flex items-center gap-1 text-destructive">
                <XCircle className="size-4" /> {test.data.error}
              </span>
            )
          ) : null}
        </div>
        {save.error ? <Alert tone="destructive">{save.error.message}</Alert> : null}
        {test.error ? <Alert tone="destructive">{test.error.message}</Alert> : null}
      </CardContent>
    </Card>
  );
}
