import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "./client";

export function useStudies() {
  return useQuery({ queryKey: ["studies"], queryFn: () => unwrap(api.GET("/api/studies")) });
}

export function useStudy(id: number) {
  return useQuery({
    queryKey: ["study", id],
    queryFn: () => unwrap(api.GET("/api/studies/{study_id}", { params: { path: { study_id: id } } })),
    refetchInterval: (q) => (q.state.data?.study.analyzing ? 2000 : 15_000),
  });
}

function useStudyAction<T>(id: number, fn: () => Promise<T>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["study", id] });
      qc.invalidateQueries({ queryKey: ["studies"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

const path = (id: number) => ({ params: { path: { study_id: id } } });

export const useSelectMembers = (id: number) =>
  useStudyAction(id, () => unwrap(api.POST("/api/studies/{study_id}/select", path(id))));
export const useLockStudy = (id: number) =>
  useStudyAction(id, () => unwrap(api.POST("/api/studies/{study_id}/lock", path(id))));
export const useNewVersion = (id: number) =>
  useStudyAction(id, () => unwrap(api.POST("/api/studies/{study_id}/new-version", path(id))));
export const useFillGaps = (id: number) =>
  useStudyAction(id, () => unwrap(api.POST("/api/studies/{study_id}/fill-gaps", path(id))));
export const useAnalyze = (id: number) =>
  useStudyAction(id, () => unwrap(api.POST("/api/studies/{study_id}/analyze", path(id))));
