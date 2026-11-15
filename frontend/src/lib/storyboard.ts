/** 分镜（storyboard）领域类型与展示辅助——与 backend/app/routers/storyboard.py 契约对齐。 */

export interface StoryboardSegment {
  id?: number;
  segment_index: number;
  prompt: string;
  camera_motion: string;
  mood: string;
  audio_cue: string;
  status: string;
  task_id?: number | null;
  keyframe_url?: string | null;
  keyframe_task_id?: number | null;
  output_video_url?: string | null;
  target_duration_ms?: number | null;
  /** 编辑态用（PUT /segments 载荷）；读取时后端只回 target_duration_ms。 */
  target_duration_s?: number | null;
}

export interface CharacterProfile {
  name: string;
  description: string;
}

export interface StoryboardMetadata {
  compose_status?: string;
  bgm_url?: string;
  style_prompt?: string;
  [key: string]: unknown;
}

export interface Storyboard {
  id: number;
  title: string | null;
  short_description: string;
  segments: StoryboardSegment[];
  status: string;
  segment_duration: number;
  final_video_url?: string | null;
  character_ref_image_url?: string | null;
  character_profiles?: CharacterProfile[];
  metadata?: StoryboardMetadata;
}

export interface StoryboardListItem {
  id: number;
  title: string;
  short_description: string;
  segments_count: number;
  status: string;
}

export interface StoryboardTemplate {
  id: number;
  title: string;
  segments?: unknown[];
}

/** 角色库条目（backend/app/models/character.py）。 */
export interface CharacterAsset {
  id: number;
  name: string;
  description: string;
  ref_image_url?: string | null;
}

export interface ProviderChannel {
  domain: string;
  enabled: boolean;
  models: { model_id: string; enabled: boolean }[];
}

/** 一键生成的成本/时长预估（POST /api/storyboards/{id}/generate 返回）。 */
export interface GenerateEstimate {
  total: number;
  segments: number;
  est_minutes?: number;
  model?: string;
}

export type Tone = "zinc" | "amber" | "emerald" | "red";

export const TONE_CLASS: Record<Tone, string> = {
  zinc: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300",
  amber:
    "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300 animate-pulse",
  emerald: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  red: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
};

/** 段状态徽标：queued/generating → 生成中，completed → 已完成，failed → 失败。 */
export function segmentBadge(status?: string | null): { label: string; tone: Tone } {
  switch (status) {
    case "queued":
    case "generating":
      return { label: "生成中", tone: "amber" };
    case "completed":
      return { label: "已完成", tone: "emerald" };
    case "failed":
      return { label: "失败", tone: "red" };
    default:
      return { label: "待生成", tone: "zinc" };
  }
}

/** 合成状态徽标：running → 合成中，done → 已合成，failed → 合成失败。 */
export function composeBadge(status: string): { label: string; tone: Tone } {
  switch (status) {
    case "running":
      return { label: "合成中", tone: "amber" };
    case "done":
      return { label: "已合成", tone: "emerald" };
    case "failed":
      return { label: "合成失败", tone: "red" };
    default:
      return { label: "待合成", tone: "zinc" };
  }
}

/** 生成进度百分比：失败段计入已完成（终态），用于进度条。 */
export function progressPercent(segments: { status: string }[]): number {
  if (segments.length === 0) return 0;
  const settled = segments.filter((s) => s.status === "completed" || s.status === "failed").length;
  return Math.round((settled / segments.length) * 100);
}

/** 预估文案：本地引擎免费，云引擎按段计价。 */
export function estimateText(estimate: GenerateEstimate): string {
  if (estimate.total === 0) return "预计免费（本地引擎）";
  return `${estimate.segments} 段 · 预计 ¥${estimate.total.toFixed(4)}`;
}
