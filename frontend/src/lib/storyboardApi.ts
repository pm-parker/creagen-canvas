import type {
  Storyboard,
  StoryboardSegment,
  StoryboardTemplate,
  StoryboardListItem,
  CharacterAsset,
  ProviderChannel,
  GenerateEstimate,
} from "@/lib/storyboard";
import { apiFetch } from "@/lib/api";

/** 分镜生成 SSE 消费：流断开时以「拉一次快照」为兜底事件源。 */
export async function subscribeStoryboardGeneration(
  storyboardId: number,
  onEvent: (type: string, data: Record<string, unknown>) => void,
  opts: { signal?: AbortSignal; fallback: () => Promise<{ type: string; data: Record<string, unknown> } | undefined> },
): Promise<void> {
  const headers: Record<string, string> = {};
  try {
    const raw = localStorage.getItem("cc-auth");
    const parsed = raw ? (JSON.parse(raw) as { state?: { token?: string } }) : undefined;
    const token = parsed?.state?.token;
    if (token) headers["Authorization"] = `Bearer ${token}`;
  } catch {
    /* 未登录态走匿名流 */
  }

  let resp: Response;
  try {
    resp = await fetch(`/api/storyboards/${storyboardId}/stream`, { headers, signal: opts.signal });
  } catch {
    await _snapshotFallback(opts, onEvent);
    return;
  }

  if (!resp.ok || !resp.body) {
    await _snapshotFallback(opts, onEvent);
    return;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let terminal = false;

  function handleLine(line: string) {
    if (!line.startsWith("data: ")) return;
    try {
      const payload = JSON.parse(line.slice(6)) as { type: string; data?: Record<string, unknown> };
      onEvent(payload.type, payload.data ?? {});
      if (payload.type === "done") terminal = true;
    } catch {
      /* 非 JSON 行忽略（keepalive 注释帧） */
    }
  }

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        handleLine(line);
        if (terminal) return;
      }
    }
  } catch {
    /* 流中途断开：走兜底 */
  }

  if (!terminal) await _snapshotFallback(opts, onEvent);
}

async function _snapshotFallback(
  opts: { fallback: () => Promise<{ type: string; data: Record<string, unknown> } | undefined> },
  onEvent: (type: string, data: Record<string, unknown>) => void,
): Promise<void> {
  try {
    const evt = await opts.fallback();
    if (evt) onEvent(evt.type, evt.data);
  } catch {
    /* 兜底失败静默结束——上层有 3s refetchInterval 兜住 */
  }
}

/** 下载媒体文件：流式读取带进度，blob 触发浏览器下载。 */
export async function downloadMedia(
  url: string,
  filename: string,
  onProgress?: (done: number, total: number | null) => void,
): Promise<void> {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const lengthHeader = resp.headers.get("Content-Length");
  const total = lengthHeader ? Number(lengthHeader) : null;
  let blob: Blob;
  if (resp.body) {
    const reader = resp.body.getReader();
    const chunks: Uint8Array[] = [];
    let received = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (value) {
        chunks.push(value);
        received += value.byteLength;
        onProgress?.(received, total);
      }
    }
    blob = new Blob(chunks as BlobPart[]);
  } else {
    blob = await resp.blob();
    onProgress?.(blob.size, blob.size);
  }
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(objectUrl);
}

export interface DownloadItem {
  url: string;
  filename: string;
}

/** 批量串行下载（间隔防浏览器拦截多下载）。 */
export async function downloadMediaAll(
  items: DownloadItem[],
  gapMs = 400,
  onProgress?: (p: { done: number; total: number; failed: string[] }) => void,
): Promise<{ done: number; total: number; failed: string[] }> {
  const state = { done: 0, total: items.length, failed: [] as string[] };
  onProgress?.({ ...state });
  for (const item of items) {
    try {
      await downloadMedia(item.url, item.filename);
    } catch {
      state.failed.push(item.url);
    }
    state.done += 1;
    onProgress?.({ ...state, failed: [...state.failed] });
    if (gapMs > 0) await new Promise((r) => setTimeout(r, gapMs));
  }
  return state;
}

/** 文件 → dataURL（上传用）。 */
export function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("读取文件失败"));
    reader.readAsDataURL(file);
  });
}

/* ---------- 段操作（对应 bundle nN：段徽标 + 重试/重出） ---------- */

export async function retrySegment(storyboardId: number, index: number): Promise<void> {
  await apiFetch(`/api/storyboards/${storyboardId}/segments/${index}/retry`, { method: "POST" });
}

export async function regenerateSegment(storyboardId: number, index: number): Promise<void> {
  await apiFetch(`/api/storyboards/${storyboardId}/segments/${index}/regenerate`, { method: "POST" });
}

/* ---------- 生成（对应 bundle rN） ---------- */

export interface GenerateConfirmResponse {
  requires_confirm?: boolean;
  estimate?: GenerateEstimate;
}

export function estimateStoryboardGeneration(
  storyboardId: number,
  model: string,
  concurrency: number,
): Promise<GenerateConfirmResponse> {
  return apiFetch<GenerateConfirmResponse>(`/api/storyboards/${storyboardId}/generate`, {
    method: "POST",
    json: { model, confirm: false, concurrency },
  });
}

export function confirmStoryboardGeneration(
  storyboardId: number,
  model: string,
  concurrency: number,
): Promise<GenerateConfirmResponse> {
  return apiFetch<GenerateConfirmResponse>(`/api/storyboards/${storyboardId}/generate`, {
    method: "POST",
    json: { model, confirm: true, concurrency },
  });
}

export function stopStoryboardGeneration(storyboardId: number): Promise<unknown> {
  return apiFetch(`/api/storyboards/${storyboardId}/stop`, { method: "POST" });
}

/** 关键帧：chain=true 全片接力；segments 指定段。 */
export function generateKeyframes(
  storyboardId: number,
  body: { chain?: boolean; segments?: number[]; count?: number },
): Promise<unknown> {
  return apiFetch(`/api/storyboards/${storyboardId}/keyframes`, { method: "POST", json: body });
}

export function selectKeyframe(storyboardId: number, index: number, url: string): Promise<unknown> {
  return apiFetch(`/api/storyboards/${storyboardId}/segments/${index}/keyframe/select`, {
    method: "POST",
    json: { url },
  });
}

export interface SplitResponse {
  segments: StoryboardSegment[];
}

export function splitStoryboard(storyboardId: number): Promise<SplitResponse> {
  return apiFetch<SplitResponse>(`/api/storyboards/${storyboardId}/split`, {
    method: "POST",
    json: { confirm: true },
  });
}

export interface CreateTemplateResponse {
  template?: { id: number; title: string };
}

export function createStoryboardTemplate(
  title: string,
  segments: Pick<StoryboardSegment, "prompt" | "camera_motion" | "mood" | "audio_cue">[],
): Promise<CreateTemplateResponse> {
  return apiFetch<CreateTemplateResponse>("/api/storyboard-templates", {
    method: "POST",
    json: { title, segments },
  });
}

export function applyStoryboardTemplate(templateId: number): Promise<{ storyboard: Storyboard }> {
  return apiFetch<{ storyboard: Storyboard }>("/api/storyboards/from-template", {
    method: "POST",
    json: { template_id: templateId },
  });
}

/* ---------- 元数据 PATCH ---------- */

function patchStoryboard(storyboardId: number, body: Record<string, unknown>): Promise<unknown> {
  return apiFetch(`/api/storyboards/${storyboardId}`, { method: "PATCH", json: body });
}

export function setStoryboardStyle(storyboardId: number, stylePrompt: string): Promise<unknown> {
  return patchStoryboard(storyboardId, { style_prompt: stylePrompt });
}

export function setStoryboardCharacterRefImage(storyboardId: number, url: string): Promise<unknown> {
  return patchStoryboard(storyboardId, { character_ref_image_url: url });
}

export function setStoryboardBgm(storyboardId: number, url: string): Promise<unknown> {
  return patchStoryboard(storyboardId, { bgm_url: url });
}

/* ---------- 角色 ---------- */

export async function saveCharacterToLibrary(name: string, description: string): Promise<unknown> {
  return apiFetch("/api/characters", { method: "POST", json: { name, description } });
}

export function fetchCharacterLibrary(): Promise<{ characters: CharacterAsset[] }> {
  return apiFetch<{ characters: CharacterAsset[] }>("/api/characters");
}

export function deleteCharacter(id: number): Promise<unknown> {
  return apiFetch(`/api/characters/${id}`, { method: "DELETE" });
}

export function attachCharacters(storyboardId: number, characterIds: number[]): Promise<unknown> {
  return apiFetch(`/api/storyboards/${storyboardId}/characters/attach`, {
    method: "POST",
    json: { character_ids: characterIds },
  });
}

/* ---------- 合成 / 回流 / 字幕 ---------- */

export function composeStoryboard(storyboardId: number): Promise<unknown> {
  return apiFetch(`/api/storyboards/${storyboardId}/compose`, { method: "POST", json: {} });
}

export function storyboardToCanvas(storyboardId: number): Promise<{ project_id: number }> {
  return apiFetch<{ project_id: number }>(`/api/storyboards/${storyboardId}/to-canvas`, { method: "POST" });
}

export function burnSubtitles(url: string, text: string): Promise<{ url: string }> {
  return apiFetch<{ url: string }>("/api/media/subtitles", { method: "POST", json: { url, text } });
}

/* ---------- 上传 ---------- */

export function uploadDataUrl(dataUrl: string): Promise<{ url: string }> {
  return apiFetch<{ url: string }>("/api/uploads", { method: "POST", json: { data_url: dataUrl } });
}

/* ---------- 渠道 ---------- */

export function fetchProviderChannels(): Promise<{ channels: ProviderChannel[] }> {
  return apiFetch<{ channels: ProviderChannel[] }>("/api/providers/channels");
}

/* ---------- 列表 / 详情 / 创建 ---------- */

export function fetchStoryboards(): Promise<{ storyboards: StoryboardListItem[] }> {
  return apiFetch<{ storyboards: StoryboardListItem[] }>("/api/storyboards");
}

export function fetchStoryboard(id: number): Promise<{ storyboard: Storyboard }> {
  return apiFetch<{ storyboard: Storyboard }>(`/api/storyboards/${id}`);
}

export function fetchStoryboardTemplates(): Promise<{ templates: StoryboardTemplate[] }> {
  return apiFetch<{ templates: StoryboardTemplate[] }>("/api/storyboard-templates");
}

export function createStoryboard(shortDescription: string): Promise<{ storyboard: Storyboard }> {
  return apiFetch<{ storyboard: Storyboard }>("/api/storyboards", {
    method: "POST",
    json: { short_description: shortDescription },
  });
}

export function saveStoryboardSegments(storyboardId: number, segments: StoryboardSegment[]): Promise<unknown> {
  return apiFetch(`/api/storyboards/${storyboardId}/segments`, {
    method: "PUT",
    json: {
      segments: segments.map((s, i) => ({
        segment_index: i + 1,
        prompt: s.prompt,
        camera_motion: s.camera_motion,
        mood: s.mood,
        audio_cue: s.audio_cue,
        target_duration_s: s.target_duration_s ?? 6,
      })),
    },
  });
}
