import * as Plot from "@observablehq/plot";
import { Pause, Play } from "lucide-react";
import { type RefObject, useEffect, useMemo, useRef, useState } from "react";
import { type BrainMeta, FrameStore, useMesh } from "@/api/brain";
import { PlotFigure } from "@/components/PlotFigure";
import { Button } from "@/components/ui/button";
import { Alert, Label, Select, Spinner, Tabs } from "@/components/ui/primitives";
import { COLORMAPS } from "@/lib/colormaps";
import { fmtDuration } from "@/lib/format";
import { BrainView, type View } from "./BrainView";

const NETWORK_COLORS: Record<string, string> = {
  visual: "#3b82f6",
  auditory: "#f59e0b",
  language: "#10b981",
  default_mode: "#ef4444",
  attention: "#a855f7",
};

/** Index of the timestep showing at `t` seconds. */
export function frameAt(t: number, startS: number[], tr: number): number {
  if (!startS.length) return 0;
  // Starts are a regular grid in practice; fall back to a search if not.
  const guess = Math.floor(t / tr);
  if (guess >= 0 && guess < startS.length && startS[guess] <= t && (guess + 1 >= startS.length || startS[guess + 1] > t)) {
    return guess;
  }
  let lo = 0;
  let hi = startS.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (startS[mid] <= t) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

function NetworkCurves({
  meta,
  cursor,
  onSeek,
}: {
  meta: BrainMeta;
  cursor: number;
  onSeek: (t: number) => void;
}) {
  const options = useMemo(() => {
    if (!meta.networks) return null;
    const t = meta.networks.t;
    const rows = Object.entries(meta.networks.networks).flatMap(([name, values]) =>
      values.map((v, i) => ({ name, t: t[i], v })).filter((r) => r.v !== null),
    );
    const peaks = Object.entries(meta.peaks).flatMap(([name, ps]) => ps.map((p) => ({ name, ...p })));
    return {
      height: 170,
      marginLeft: 44,
      x: { label: "seconds", grid: true },
      y: { label: "network mean", grid: true },
      color: { domain: Object.keys(NETWORK_COLORS), range: Object.values(NETWORK_COLORS), legend: true },
      marks: [
        Plot.ruleY([0], { stroke: "currentColor", strokeOpacity: 0.25 }),
        Plot.line(rows, { x: "t", y: "v", stroke: "name", strokeWidth: 1.3 }),
        Plot.dot(peaks, { x: "t", y: "value", fill: "name", r: 3.5, stroke: "white", strokeWidth: 0.8 }),
        Plot.ruleX([cursor], { stroke: "currentColor", strokeWidth: 1.5 }),
        Plot.tip(rows, Plot.pointerX({ x: "t", y: "v", title: (d) => `${d.name} at ${fmtDuration(d.t)}: ${d.v.toFixed(3)}` })),
      ],
    } as Plot.PlotOptions;
  }, [meta, cursor]);
  if (!options) return null;
  return <PlotFigure options={options} onPick={(d) => onSeek((d as { t: number }).t)} />;
}

/**
 * Video + predicted brain response. Syncs to the <video> with requestVideoFrameCallback;
 * plays on its own clock when there's no video file (e.g. title-only TRIBE runs).
 */
export function BrainPlayer({
  meta,
  videoRef,
  videoUrl,
  poster,
}: {
  meta: BrainMeta;
  videoRef: RefObject<HTMLVideoElement | null>;
  videoUrl?: string;
  poster?: string;
}) {
  const mesh = useMesh();
  const [frameIdx, setFrameIdx] = useState(0);
  const [cursor, setCursor] = useState(0);
  const [, setLoaded] = useState(0);
  const [view, setView] = useState<View>("lateral");
  const [colormap, setColormap] = useState("coolwarm");
  const [threshold, setThreshold] = useState(0.15); // fraction of the colour range hidden around 0
  const [ownClock, setOwnClock] = useState(false);
  const store = useMemo(
    () => new FrameStore(meta.frames_url, meta.n_timesteps, meta.n_vertices),
    [meta.frames_url, meta.n_timesteps, meta.n_vertices],
  );
  const lastIdx = useRef(-1);
  const lastCursor = useRef(0);

  useEffect(() => {
    store.onLoad = () => setLoaded((n) => n + 1);
  }, [store]);

  // Follow the video's presented frames.
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    let cancelled = false;
    let handle = 0;
    const tick = (t: number) => {
      const idx = frameAt(t, meta.start_s, meta.tr_s);
      if (idx !== lastIdx.current) {
        lastIdx.current = idx;
        setFrameIdx(idx);
      }
      if (Math.abs(t - lastCursor.current) > 0.25) {
        lastCursor.current = t;
        setCursor(t);
      }
    };
    const supportsRvfc = "requestVideoFrameCallback" in HTMLVideoElement.prototype;
    const onFrame = (_now: number, info: { mediaTime: number }) => {
      if (cancelled) return;
      tick(info.mediaTime);
      handle = v.requestVideoFrameCallback(onFrame);
    };
    const onSeeked = () => tick(v.currentTime);
    v.addEventListener("seeked", onSeeked);
    if (supportsRvfc) {
      handle = v.requestVideoFrameCallback(onFrame);
    } else {
      const loop = () => {
        if (cancelled) return;
        tick(v.currentTime);
        handle = requestAnimationFrame(loop);
      };
      handle = requestAnimationFrame(loop);
    }
    return () => {
      cancelled = true;
      v.removeEventListener("seeked", onSeeked);
      if (supportsRvfc) v.cancelVideoFrameCallback(handle);
      else cancelAnimationFrame(handle);
    };
  }, [videoRef, meta.start_s, meta.tr_s, videoUrl]);

  // Own clock when there's no video.
  useEffect(() => {
    if (videoUrl || !ownClock) return;
    const started = performance.now() - cursor * 1000;
    let handle = requestAnimationFrame(function loop(now) {
      const t = (now - started) / 1000;
      if (t > meta.n_timesteps * meta.tr_s) {
        setOwnClock(false);
        return;
      }
      setCursor(t);
      setFrameIdx(frameAt(t, meta.start_s, meta.tr_s));
      handle = requestAnimationFrame(loop);
    });
    return () => cancelAnimationFrame(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ownClock, videoUrl]);

  const seek = (t: number) => {
    const v = videoRef.current;
    if (v) v.currentTime = t;
    else {
      setCursor(t);
      setFrameIdx(frameAt(t, meta.start_s, meta.tr_s));
    }
  };

  const frame = store.frame(frameIdx);
  const thresholdCodes = Math.round(threshold * 127);
  const value = (code: number) => meta.scale.lo + ((code - 1) / meta.scale.levels) * (meta.scale.hi - meta.scale.lo);

  return (
    <div className="space-y-3">
      <div className="grid gap-3 lg:grid-cols-2">
        {videoUrl ? (
          <video ref={videoRef} src={videoUrl} controls poster={poster} className="w-full rounded-xl bg-black" />
        ) : (
          <div className="flex aspect-video items-center justify-center rounded-xl bg-muted">
            <Button variant="outline" onClick={() => setOwnClock((v) => !v)}>
              {ownClock ? <Pause /> : <Play />} {ownClock ? "Pause" : "Play"} brain response
            </Button>
          </div>
        )}
        {mesh.isLoading ? (
          <div className="flex h-80 items-center justify-center rounded-xl bg-black/90">
            <Spinner className="text-white" />
          </div>
        ) : mesh.error ? (
          <Alert tone="warning">{mesh.error.message}</Alert>
        ) : mesh.data ? (
          <div className="relative">
            <BrainView hemis={mesh.data} frame={frame} view={view} colormap={colormap} thresholdCodes={thresholdCodes} />
            <div className="pointer-events-none absolute left-3 top-2 text-xs text-white/80 tabular">
              t = {fmtDuration(cursor)} · step {frameIdx + 1}/{meta.n_timesteps}
              {frame ? "" : " · loading"}
            </div>
          </div>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-4 text-sm">
        <Tabs
          value={view}
          onChange={setView}
          tabs={[
            { value: "lateral", label: "Lateral" },
            { value: "medial", label: "Medial" },
          ]}
        />
        <div className="flex items-center gap-2">
          <Label className="text-xs text-muted-foreground">Colormap</Label>
          <Select value={colormap} onChange={(e) => setColormap(e.target.value)}>
            {COLORMAPS.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </Select>
        </div>
        <div className="flex items-center gap-2">
          <Label className="text-xs text-muted-foreground" htmlFor="brain-threshold">
            Threshold
          </Label>
          <input
            id="brain-threshold"
            type="range"
            min={0}
            max={0.9}
            step={0.05}
            value={threshold}
            onChange={(e) => setThreshold(Number(e.target.value))}
          />
          <span className="w-24 text-xs text-muted-foreground tabular">|x| ≥ {value(128 + thresholdCodes).toFixed(3)}</span>
        </div>
        <span className="text-xs text-muted-foreground">
          {meta.analyzer} · {meta.n_timesteps} steps of {meta.tr_s}s
          {meta.hrf_offset_s ? ` · responses already shifted for the ${meta.hrf_offset_s}s HRF lag` : ""}
        </span>
      </div>
      <NetworkCurves meta={meta} cursor={cursor} onSeek={seek} />
    </div>
  );
}
