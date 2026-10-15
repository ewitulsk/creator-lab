import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, type Schemas, unwrap } from "./client";

export type JobSummary = Schemas["JobSummary"];
const ACTIVE = new Set(["queued", "claimed", "running"]);

export function isActive(status: string) {
  return ACTIVE.has(status);
}

/**
 * Live jobs over server-sent events, falling back to polling if the stream fails.
 * Returns active jobs plus ones finished in the last 10 minutes.
 */
export function useLiveJobs() {
  const [streamed, setStreamed] = useState<{ jobs: JobSummary[]; runners_online: number } | null>(null);
  const [fallback, setFallback] = useState(typeof EventSource === "undefined");
  const qc = useQueryClient();

  useEffect(() => {
    if (fallback) return;
    let es: EventSource | null = new EventSource("/api/jobs/stream");
    let failures = 0;
    es.addEventListener("jobs", (e) => {
      failures = 0;
      const data = JSON.parse((e as MessageEvent).data);
      setStreamed(data);
      // Anything showing per-video status may have changed.
      qc.invalidateQueries({ queryKey: ["coverage"] });
    });
    es.onerror = () => {
      failures += 1;
      if (failures >= 3 && es) {
        es.close();
        es = null;
        setFallback(true);
      }
    };
    return () => es?.close();
  }, [fallback, qc]);

  const polled = useQuery({
    queryKey: ["jobs", "live-fallback"],
    queryFn: () => unwrap(api.GET("/api/jobs", { params: { query: { limit: 100 } } })),
    enabled: fallback,
    refetchInterval: 3000,
  });

  if (!fallback) return { jobs: streamed?.jobs, runnersOnline: streamed?.runners_online, live: true };
  return { jobs: polled.data?.jobs, runnersOnline: undefined, live: false };
}

export function useJobs(query: { status?: string; type?: string; limit?: number; offset?: number; video_id?: number }) {
  return useQuery({
    queryKey: ["jobs", query],
    queryFn: () => unwrap(api.GET("/api/jobs", { params: { query } })),
    placeholderData: keepPreviousData,
    refetchInterval: 10_000,
  });
}

export function useJob(id: number) {
  return useQuery({
    queryKey: ["job", id],
    queryFn: () => unwrap(api.GET("/api/jobs/{job_id}", { params: { path: { job_id: id } } })),
    refetchInterval: (q) => (q.state.data && isActive(q.state.data.job.status) ? 2000 : false),
  });
}

export function useJobLog(id: number, active: boolean) {
  return useQuery({
    queryKey: ["job", id, "log"],
    queryFn: () => unwrap(api.GET("/api/jobs/{job_id}/log", { params: { path: { job_id: id }, query: { tail: 400 } } })),
    refetchInterval: active ? 3000 : false,
  });
}

export function useCancelJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => unwrap(api.POST("/api/jobs/{job_id}/cancel", { params: { path: { job_id: id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["job"] }),
  });
}

export function useRetryJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => unwrap(api.POST("/api/jobs/{job_id}/retry", { params: { path: { job_id: id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useIssueJobs() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { requests: { analyzer: string; video_ids: number[] }[]; force?: boolean }) =>
      unwrap(api.POST("/api/jobs", { body })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["coverage"] });
      qc.invalidateQueries({ queryKey: ["video"] });
    },
  });
}

export function useRunners() {
  return useQuery({
    queryKey: ["runners"],
    queryFn: () => unwrap(api.GET("/api/runners")),
    refetchInterval: 10_000,
  });
}

export function useAnalyzers() {
  return useQuery({ queryKey: ["analyzers"], queryFn: () => unwrap(api.GET("/api/analyzers")), staleTime: 60_000 });
}

export type CoverageQuery = { channel_id?: string; selection?: "tracked" | "discovered" | "all"; format?: string; limit?: number; offset?: number };

export function useCoverage(query: CoverageQuery) {
  return useQuery({
    queryKey: ["coverage", query],
    queryFn: () => unwrap(api.GET("/api/coverage", { params: { query } })),
    placeholderData: keepPreviousData,
  });
}
