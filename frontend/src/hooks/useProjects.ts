import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

export interface CanvasProjectMeta {
  id: number;
  title: string;
  updated_at: string | null;
}

/** 项目列表查询：仅页面顶层使用（避免每张卡重复订阅触发 refetch）。 */
export function useProjectsQuery() {
  return useQuery({
    queryKey: ["canvas", "projects"],
    queryFn: () => apiFetch<{ projects: CanvasProjectMeta[] }>("/api/canvas/projects"),
  });
}

function useInvalidate() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: ["canvas", "projects"] });
}

export function useProjectMutations() {
  const invalidate = useInvalidate();

  const create = useMutation({
    mutationFn: () =>
      apiFetch<{ project: CanvasProjectMeta }>("/api/canvas/projects", {
        method: "POST",
        json: { title: "未命名画布" },
      }),
    onSuccess: invalidate,
  });

  const rename = useMutation({
    mutationFn: ({ id, title }: { id: number; title: string }) =>
      apiFetch<{ project: CanvasProjectMeta }>(`/api/canvas/projects/${id}`, {
        method: "PUT",
        json: { title },
      }),
    onSuccess: invalidate,
  });

  const remove = useMutation({
    mutationFn: (id: number) => apiFetch<void>(`/api/canvas/projects/${id}`, { method: "DELETE" }),
    onSuccess: invalidate,
  });

  return { create, rename, remove };
}

export function useProjects() {
  const query = useProjectsQuery();
  const mutations = useProjectMutations();
  return {
    projects: query.data?.projects,
    isLoading: query.isLoading,
    ...mutations,
  };
}
