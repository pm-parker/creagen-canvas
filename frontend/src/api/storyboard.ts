/** 分镜/角色 API——对齐 backend/app/routers/storyboard.py。 */
import { apiFetch } from "@/lib/api";
import type {
  CharacterAsset,
  GenerateEstimate,
  Storyboard,
  StoryboardListItem,
  StoryboardTemplate,
} from "@/lib/storyboard";

export async function fetchStoryboards(): Promise<StoryboardListItem[]> {
  return apiFetch("/api/storyboards");
}

export async function fetchStoryboard(id: number | string): Promise<Storyboard> {
  return apiFetch(`/api/storyboards/${id}`);
}

export async function createStoryboard(payload: {
  title: string;
  short_description: string;
  segment_count?: number;
  segment_duration?: number;
  template_id?: number | null;
}): Promise<Storyboard> {
  return apiFetch("/api/storyboards", { method: "POST", json: payload });
}

export async function deleteStoryboard(id: number): Promise<void> {
  await apiFetch(`/api/storyboards/${id}`, { method: "DELETE" });
}

export async function generateStoryboard(
  id: number,
  payload: { segments?: number[] },
): Promise<GenerateEstimate> {
  return apiFetch(`/api/storyboards/${id}/generate`, {
    method: "POST",
    json: payload,
  });
}

export async function cancelStoryboard(id: number): Promise<void> {
  await apiFetch(`/api/storyboards/${id}/cancel`, { method: "POST" });
}

export async function composeStoryboard(id: number, mode: string): Promise<void> {
  await apiFetch(`/api/storyboards/${id}/compose`, {
    method: "POST",
    json: { mode },
  });
}

export async function exportStoryboard(id: number, mode: string): Promise<void> {
  await apiFetch(`/api/storyboards/${id}/export`, {
    method: "POST",
    json: { mode },
  });
}

/** 读取模式存 metadata.mode；keyframe 支持 video/image 两种。 */
export async function updateSegment(
  storyboardId: number,
  segmentId: number,
  payload: Record<string, unknown>,
): Promise<Storyboard> {
  return apiFetch(`/api/storyboards/${storyboardId}/segments/${segmentId}`, {
    method: "PUT",
    json: payload,
  });
}

export async function generateKeyframe(
  storyboardId: number,
  segmentId: number,
  payload: { kind?: "video" | "image"; frame_spec?: string; ref_image_url?: string | null },
): Promise<void> {
  await apiFetch(`/api/storyboards/${storyboardId}/segments/${segmentId}/keyframe`, {
    method: "POST",
    json: payload,
  });
}

export async function fetchStoryboardTemplates(): Promise<StoryboardTemplate[]> {
  return apiFetch("/api/storyboard-templates");
}

// ---- 角色库 ----

export async function fetchCharacterAssets(): Promise<CharacterAsset[]> {
  return apiFetch("/api/characters");
}

export async function createCharacterAsset(payload: {
  name: string;
  description: string;
  ref_image_url?: string | null;
}): Promise<CharacterAsset> {
  return apiFetch("/api/characters", { method: "POST", json: payload });
}

export async function deleteCharacterAsset(id: number): Promise<void> {
  await apiFetch(`/api/characters/${id}`, { method: "DELETE" });
}

/** 生成角色参考图（异步任务，前端轮询 ref_image_url）。 */
export async function generateCharacterImage(id: number): Promise<void> {
  await apiFetch(`/api/characters/${id}/generate`, { method: "POST" });
}
