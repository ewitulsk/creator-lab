import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "./client";

export function useCompare(channelIds: number[], months: number) {
  return useQuery({
    queryKey: ["compare", channelIds, months],
    queryFn: () =>
      unwrap(api.GET("/api/compare", { params: { query: { channel_ids: channelIds.join(","), months } } })),
    enabled: channelIds.length > 0,
    placeholderData: keepPreviousData,
  });
}

export type ShortsSort = "published_at" | "views" | "outlier_score";

export function useShorts(query: { channel_id?: string; sort: ShortsSort; limit?: number }) {
  return useQuery({
    queryKey: ["shorts", query],
    queryFn: () => unwrap(api.GET("/api/shorts", { params: { query } })),
    placeholderData: keepPreviousData,
  });
}

export function useBackups() {
  return useQuery({ queryKey: ["backups"], queryFn: () => unwrap(api.GET("/api/backups")) });
}

export function useCreateBackup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/backups")),
    onSuccess: (data) => qc.setQueryData(["backups"], { backups: data.backups }),
  });
}

/** Plain links: the browser downloads the file with the session cookie. */
export function exportUrl(
  kind: "videos" | "video_snapshots" | "channel_snapshots" | "analyzer",
  fmt: "csv" | "parquet",
  params: Record<string, string | undefined> = {},
) {
  const q = new URLSearchParams(Object.entries(params).filter((e): e is [string, string] => !!e[1]));
  return `/api/exports/${kind}.${fmt}${q.size ? `?${q}` : ""}`;
}
