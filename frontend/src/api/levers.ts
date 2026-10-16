import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Schemas, unwrap } from "./client";

export type LeversConfig = Schemas["LeversConfig"];
export type VideoLevers = Schemas["VideoLevers"];
export type LeverEstimate = Schemas["LeverEstimate"];

export const LEVERS = ["attention", "emotion", "comprehension", "valuable", "memorable", "shareable"] as const;
export const SYSTEM_1 = new Set(["attention", "emotion", "comprehension"]);

export function useLeversConfig() {
  return useQuery({ queryKey: ["levers", "config"], queryFn: () => unwrap(api.GET("/api/levers/config")) });
}

export function useSaveLeversConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (values: Record<string, unknown>) => unwrap(api.PUT("/api/levers/config", { body: values })),
    onSuccess: (data) => qc.setQueryData(["levers", "config"], data),
  });
}

export function useTestLevers() {
  return useMutation({
    mutationFn: (model?: string) => unwrap(api.POST("/api/levers/test", { body: model ? { model } : {} })),
  });
}

export function useLeversEstimate() {
  return useMutation({
    mutationFn: (body: { video_ids?: number[]; pairs?: number[][] }) => unwrap(api.POST("/api/levers/estimate", { body })),
  });
}

export function useScoreLevers() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (video_ids: number[]) => unwrap(api.POST("/api/levers/score", { body: { video_ids } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["coverage"] });
    },
  });
}

export function usePairwise() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (pairs: number[][]) => unwrap(api.POST("/api/levers/pairwise", { body: { pairs } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useStudyPairwise(studyId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (estimate_only: boolean) =>
      unwrap(
        api.POST("/api/studies/{study_id}/levers-pairwise", {
          params: { path: { study_id: studyId } },
          body: { estimate_only },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useVideoLevers(videoId: number) {
  return useQuery({
    queryKey: ["video", videoId, "levers"],
    queryFn: () => unwrap(api.GET("/api/videos/{video_id}/levers", { params: { path: { video_id: videoId } } })),
  });
}

export function useLeversJobCost(jobId: number, enabled: boolean) {
  return useQuery({
    queryKey: ["levers", "cost", jobId],
    queryFn: () => unwrap(api.GET("/api/levers/jobs/{job_id}/cost", { params: { path: { job_id: jobId } } })),
    enabled,
    refetchInterval: 15_000,
  });
}

export function fmtUsd(v: number | null | undefined) {
  if (v === null || v === undefined) return "—";
  return v < 0.01 && v > 0 ? "<$0.01" : `$${v.toFixed(2)}`;
}

/** Timeline markers from the latest scoring: emotional moments and evidence spans. */
export type LeverMarker = { t: number; t_end: number | null; label: string; kind: string };

export function leverMarkers(data: VideoLevers | undefined, run = 1): LeverMarker[] {
  return (data?.moments ?? [])
    .filter((m) => m.run_idx === run)
    .map((m) => ({
      t: m.t_start,
      t_end: m.t_end,
      kind: m.kind,
      label: m.kind === "evidence" ? `${m.lever}: ${m.label ?? ""}` : `${m.label ?? ""} (${m.kind}, ${m.intensity}/5)`,
    }));
}
