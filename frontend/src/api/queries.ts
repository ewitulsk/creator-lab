import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, unwrap } from "./client";

export const keys = {
  me: ["me"] as const,
  dashboard: ["dashboard"] as const,
  settings: ["settings"] as const,
};

export function useMe() {
  return useQuery({
    queryKey: keys.me,
    queryFn: async () => {
      try {
        return await unwrap(api.GET("/api/auth/me"));
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
    staleTime: Infinity,
    retry: false,
  });
}

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { username: string; password: string }) =>
      unwrap(api.POST("/api/auth/login", { body: { ...body, remember: true } })),
    onSuccess: (data) => {
      qc.setQueryData(keys.me, data);
    },
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/auth/logout")),
    onSuccess: () => {
      qc.clear();
      qc.setQueryData(keys.me, null);
    },
  });
}

export function useDashboard() {
  return useQuery({
    queryKey: keys.dashboard,
    queryFn: () => unwrap(api.GET("/api/dashboard")),
    refetchInterval: 15_000,
  });
}

export function useSettings() {
  return useQuery({ queryKey: keys.settings, queryFn: () => unwrap(api.GET("/api/settings")) });
}

export function useSaveSchedules() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Record<string, number>) => unwrap(api.PUT("/api/settings/schedules", { body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.settings }),
  });
}

export function useChangePassword() {
  return useMutation({
    mutationFn: (body: { current_password: string; new_password: string }) =>
      unwrap(api.POST("/api/auth/password", { body })),
  });
}
