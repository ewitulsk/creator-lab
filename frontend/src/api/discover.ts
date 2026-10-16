import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "./client";

export type DiscoverFilters = {
  timeframe: "7d" | "30d" | "90d" | "1y" | "all" | "custom";
  since?: string;
  until?: string;
  pick: "all" | "top" | "bottom";
  rank: "outlier" | "views";
  n: number;
  formats: string;
  include_live?: "1";
  min_duration?: number;
  study_id?: number;
};

export const DEFAULT_FILTERS: DiscoverFilters = {
  timeframe: "all",
  pick: "all",
  rank: "outlier",
  n: 10,
  formats: "long,short",
};

export function useChannelSearch(q: string) {
  return useQuery({
    queryKey: ["discover", "search", q],
    queryFn: () => unwrap(api.GET("/api/discover/search", { params: { query: { q } } })),
    enabled: q.length > 0,
    staleTime: Infinity, // name searches cost 100 quota units; never refetch on their own
    retry: false,
  });
}

export function useDiscoverVideos(channelId: number, filters: DiscoverFilters) {
  return useQuery({
    queryKey: ["discover", channelId, filters],
    queryFn: () =>
      unwrap(
        api.GET("/api/discover/channels/{channel_id}/videos", {
          params: { path: { channel_id: channelId }, query: filters },
        }),
      ),
    placeholderData: keepPreviousData,
    refetchInterval: (q) => (q.state.data?.listing.syncing ? 3000 : false),
  });
}

export function usePull() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { video_ids: number[]; study_id?: number | null }) =>
      unwrap(api.POST("/api/discover/pull", { body })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["discover"] });
      qc.invalidateQueries({ queryKey: ["channels"] });
      qc.invalidateQueries({ queryKey: ["video"] });
    },
  });
}

/** Rough download size at the cap: H.264 + AAC as YouTube serves it. */
export function estimateBytes(seconds: number, maxHeight: number): number {
  const bytesPerSecond = maxHeight >= 1080 ? 600_000 : maxHeight >= 720 ? 350_000 : 200_000;
  return seconds * bytesPerSecond;
}
