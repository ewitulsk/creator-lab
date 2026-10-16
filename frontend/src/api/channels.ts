import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "./client";

export type VideoListParams = {
  q?: string;
  format?: string;
  sort?: "published_at" | "views" | "outlier_score" | "duration_s" | "title" | "likes" | "comments";
  order?: "asc" | "desc";
  limit?: string;
  offset?: number;
  min_outlier?: number;
  max_outlier?: number;
  channel_id?: string;
  is_mine?: "1";
  selection?: "discovered" | "tracked";
};

export function useChannels(opts: { poll?: boolean } = {}) {
  return useQuery({
    queryKey: ["channels"],
    queryFn: () => unwrap(api.GET("/api/channels")),
    refetchInterval: (q) => (opts.poll || q.state.data?.channels.some((c) => c.syncing) ? 4000 : false),
  });
}

export function useChannel(id: number) {
  return useQuery({
    queryKey: ["channel", id],
    queryFn: () => unwrap(api.GET("/api/channels/{channel_id}", { params: { path: { channel_id: id } } })),
    refetchInterval: (q) => (q.state.data?.channel.syncing ? 3000 : false),
  });
}

export function useChannelSnapshots(id: number) {
  return useQuery({
    queryKey: ["channel", id, "snapshots"],
    queryFn: () =>
      unwrap(api.GET("/api/channels/{channel_id}/snapshots", { params: { path: { channel_id: id } } })),
  });
}

export function useChannelVideos(id: number, query: VideoListParams, opts: { poll?: boolean } = {}) {
  return useQuery({
    queryKey: ["channel", id, "videos", query],
    queryFn: () =>
      unwrap(api.GET("/api/channels/{channel_id}/videos", { params: { path: { channel_id: id }, query } })),
    placeholderData: keepPreviousData,
    refetchInterval: opts.poll ? 4000 : false,
  });
}

export function useVideos(query: VideoListParams, enabled = true) {
  return useQuery({
    queryKey: ["videos", query],
    queryFn: () => unwrap(api.GET("/api/videos", { params: { query } })),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useAddChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { channel: string; is_mine?: boolean; follow?: boolean }) =>
      unwrap(api.POST("/api/channels", { body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["channels"] }),
  });
}

export function useUpdateChannel(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      followed?: boolean;
      auto_track_new?: boolean;
      is_mine?: boolean;
      tags?: string[];
      notes?: string | null;
    }) => unwrap(api.PATCH("/api/channels/{channel_id}", { params: { path: { channel_id: id } }, body })),
    onSuccess: (data) => {
      qc.setQueryData(["channel", id], data);
      qc.invalidateQueries({ queryKey: ["channels"] });
    },
  });
}

export function useDeleteChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, deleteFiles }: { id: number; deleteFiles: boolean }) =>
      unwrap(
        api.DELETE("/api/channels/{channel_id}", {
          params: { path: { channel_id: id }, query: deleteFiles ? { delete_files: "1" } : {} },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["channels"] }),
  });
}

export function useRefreshChannel(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (force: boolean = false) =>
      unwrap(
        api.POST("/api/channels/{channel_id}/refresh", {
          params: { path: { channel_id: id }, query: force ? { force: "1" } : {} },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["channel", id] }),
  });
}

export function useVideo(id: number, opts: { poll?: boolean } = {}) {
  return useQuery({
    queryKey: ["video", id],
    queryFn: () => unwrap(api.GET("/api/videos/{video_id}", { params: { path: { video_id: id } } })),
    refetchInterval: opts.poll ? 5000 : false,
  });
}

export function useRefreshVideo(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/videos/{video_id}/refresh", { params: { path: { video_id: id } } })),
    onSuccess: (data) => qc.setQueryData(["video", id], data),
  });
}
