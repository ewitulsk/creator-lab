import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "./client";

export function useOwnerStatus() {
  return useQuery({
    queryKey: ["owner"],
    queryFn: () => unwrap(api.GET("/api/youtube/owner")),
    refetchInterval: (q) =>
      q.state.data?.credentials.some((c) => c.last_sync?.status === "running") ? 2000 : false,
  });
}

export function useOwnerSignIn() {
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/youtube/oauth/start", { body: { return_to: "/settings" } })),
    onSuccess: (data) => {
      window.location.href = data.auth_url;
    },
  });
}

export function useOwnerSync() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ channelId, mode }: { channelId: number; mode: "full" | "new" }) =>
      unwrap(
        api.POST("/api/youtube/owner/{channel_id}/sync", { params: { path: { channel_id: channelId } }, body: { mode } }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["owner"] }),
  });
}

export function useOwnerDisconnect() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (channelId: number) =>
      unwrap(api.DELETE("/api/youtube/owner/{channel_id}", { params: { path: { channel_id: channelId } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["owner"] }),
  });
}

export function useEngagement(videoId: number, syncId?: number, enabled = true) {
  return useQuery({
    queryKey: ["engagement", videoId, syncId],
    queryFn: () =>
      unwrap(
        api.GET("/api/videos/{video_id}/engagement", {
          params: { path: { video_id: videoId }, query: syncId ? { sync_id: syncId } : {} },
        }),
      ),
    enabled,
  });
}

export function useHeatmap(videoId: number, captureId?: number) {
  return useQuery({
    queryKey: ["heatmap", videoId, captureId],
    queryFn: () =>
      unwrap(
        api.GET("/api/videos/{video_id}/heatmap", {
          params: { path: { video_id: videoId }, query: captureId ? { capture_id: captureId } : {} },
        }),
      ),
  });
}
