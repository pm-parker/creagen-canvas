import { Fragment, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Plus,
  Clapperboard,
  RefreshCw,
  Square,
  Wand2,
  Image as ImageIcon,
  Download,
  Upload,
  Captions,
  Film,
  ExternalLink,
  Trash2,
  Check,
  Circle,
  CircleDot,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  fetchStoryboards,
  fetchStoryboard,
  fetchStoryboardTemplates,
  createStoryboard,
  applyStoryboardTemplate,
  saveStoryboardSegments,
  splitStoryboard,
  createStoryboardTemplate,
  retrySegment,
  regenerateSegment,
  estimateStoryboardGeneration,
  confirmStoryboardGeneration,
  stopStoryboardGeneration,
  generateKeyframes,
  selectKeyframe,
  setStoryboardStyle,
  setStoryboardCharacterRefImage,
  setStoryboardBgm,
  saveCharacterToLibrary,
  fetchCharacterLibrary,
  attachCharacters,
  composeStoryboard,
  storyboardToCanvas,
  burnSubtitles,
  uploadDataUrl,
  fetchProviderChannels,
  subscribeStoryboardGeneration,
  downloadMedia,
  readFileAsDataUrl,
} from "@/lib/storyboardApi";
import { apiFetch } from "@/lib/api";
import {
  type Storyboard,
  type StoryboardSegment,
  type CharacterAsset,
  segmentBadge,
  composeBadge,
  progressPercent,
  estimateText,
  TONE_CLASS,
} from "@/lib/storyboard";

/* ================= 分镜列表页（bundle hN） ================= */

export function StoryboardsPage() {
  const navigate = useNavigate();
  const listQuery = useQuery({ queryKey: ["storyboards"], queryFn: fetchStoryboards });
  const templatesQuery = useQuery({ queryKey: ["storyboard-templates"], queryFn: fetchStoryboardTemplates });
  const fromTemplate = useMutation({
    mutationFn: (templateId: number) => applyStoryboardTemplate(templateId),
    onSuccess: (data) => navigate(`/storyboards/${data.storyboard.id}`),
  });
  const [brief, setBrief] = useState("");
  const create = useMutation({
    mutationFn: () => createStoryboard(brief.trim()),
    onSuccess: (data) => navigate(`/storyboards/${data.storyboard.id}`),
  });

  return (
    <div className="min-h-screen bg-zinc-50">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-zinc-200 bg-white px-6 py-4">
        <h1 className="text-lg font-semibold">分镜工作台</h1>
        <Button disabled={brief.trim().length < 4 || create.isPending} onClick={() => create.mutate()}>
          <Plus className="h-4 w-4" /> 新建分镜
        </Button>
      </header>
      <main className="mx-auto max-w-5xl space-y-4 p-6">
        <div className="rounded-2xl border border-zinc-200 bg-white p-5">
          <Label htmlFor="sb-brief">剧本 / 创意简报</Label>
          <textarea
            id="sb-brief"
            value={brief}
            onChange={(e) => setBrief(e.target.value)}
            rows={3}
            placeholder="用一两句话描述你想拍的片子，如：一只橘猫的都市冒险，白天穿行胡同，夜晚登上天台看灯海"
            className="mt-1.5 w-full rounded-xl border border-zinc-200 p-3 text-sm outline-none focus:border-zinc-400"
          />
          <p className="mt-1 text-xs text-zinc-400">至少 4 个字。创建后可在分镜表中用 AI 拆分为多个分镜段。</p>
        </div>
        {(templatesQuery.data?.templates ?? []).length > 0 && (
          <div className="rounded-2xl border border-amber-200 bg-amber-50/60 p-4 dark:border-amber-900 dark:bg-[#1a1405]">
            <p className="text-xs font-medium text-amber-700 dark:text-amber-400">从模板创建</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {(templatesQuery.data?.templates ?? []).map((t) => (
                <Button
                  key={String(t.id)}
                  variant="outline"
                  size="sm"
                  disabled={fromTemplate.isPending}
                  onClick={() => fromTemplate.mutate(Number(t.id))}
                >
                  用此模板 · {String(t.title)}（{String((t.segments as unknown[] | undefined)?.length ?? 0)} 段）
                </Button>
              ))}
            </div>
          </div>
        )}
        {listQuery.isLoading && <p className="text-sm text-zinc-500">加载中…</p>}
        {(listQuery.data?.storyboards ?? []).length > 0 && (
          <div className="space-y-2">
            {listQuery.data!.storyboards.map((sb) => (
              <Link
                key={String(sb.id)}
                to={`/storyboards/${sb.id}`}
                className="block rounded-xl border border-zinc-200 bg-white p-4 transition-shadow hover:shadow-md"
              >
                <div className="flex items-center justify-between">
                  <span className="font-medium text-zinc-900">{String(sb.title)}</span>
                  <span className="text-xs text-zinc-400">
                    {String(sb.segments_count)} 段 · {String(sb.status)}
                  </span>
                </div>
                <p className="mt-1 truncate text-xs text-zinc-500">{String(sb.short_description)}</p>
              </Link>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}

/* ================= 步骤条（bundle pN/dN/fN/mN） ================= */

type StepStatus = "done" | "current" | "upcoming";

interface Step {
  key: string;
  label: string;
  status: StepStatus;
  action?: string;
}

function nextStepAction(key: string, generating: boolean): string | undefined {
  if (key === "shots") return "下一步：AI 拆分分镜";
  if (key === "keyframes") return "下一步：生成关键帧，先看构图再拍";
  if (key === "video") return generating ? "生成中，可随时停止" : "下一步：一键生成全片";
  if (key === "film") return "下一步：合成成片";
  return undefined;
}

function buildPipelineSteps(storyboard: Storyboard): Step[] {
  const segments = storyboard.segments ?? [];
  const hasShots = segments.length > 0;
  const hasKeyframes = segments.some((s) => !!s.keyframe_url);
  const hasVideo = segments.some((s) => s.status === "completed");
  const hasFilm = !!storyboard.final_video_url;
  const steps: Step[] = [
    { key: "script", label: "剧本", status: "done" },
    { key: "shots", label: "分镜", status: hasShots ? "done" : "upcoming" },
    { key: "keyframes", label: "关键帧", status: hasKeyframes || hasVideo ? "done" : "upcoming" },
    { key: "video", label: "视频", status: hasVideo ? "done" : "upcoming" },
    { key: "film", label: "成片", status: hasFilm ? "done" : "upcoming" },
  ];
  const firstUndone = steps.find((s) => s.status !== "done");
  const generating = storyboard.status === "generating";
  for (const step of steps) {
    if (firstUndone && step.key === firstUndone.key) {
      step.status = "current";
      step.action = nextStepAction(step.key, generating);
    } else {
      step.status = step.status === "done" ? "done" : "upcoming";
    }
  }
  if (generating) {
    const video = steps.find((s) => s.key === "video");
    if (video) {
      video.status = "current";
      video.action = "生成中，可随时停止";
    }
  }
  return steps;
}

function StepChip({ step }: { step: Step }) {
  const icon =
    step.status === "done" ? (
      <Check className="h-3 w-3" />
    ) : step.status === "current" ? (
      <CircleDot className="h-3 w-3" />
    ) : (
      <Circle className="h-3 w-3" />
    );
  const cls =
    step.status === "done"
      ? "bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/60 dark:text-emerald-300 dark:border-emerald-900"
      : step.status === "current"
        ? "bg-amber-50 text-amber-700 border-amber-300 font-medium dark:bg-[#271c05] dark:text-amber-300 dark:border-amber-700"
        : "bg-zinc-50 text-zinc-400 border-zinc-200 dark:bg-zinc-800/60 dark:text-zinc-500 dark:border-zinc-700";
  return (
    <span className={`flex items-center gap-1 rounded-full border px-2 py-0.5 ${cls}`}>
      {icon} {step.label}
    </span>
  );
}

function PipelineStepper({ storyboard }: { storyboard: Storyboard }) {
  const steps = buildPipelineSteps(storyboard);
  const allDone = steps.every((s) => s.status === "done");
  const currentSteps = steps.filter((s) => s.status === "current");
  return (
    <div
      data-testid="pipeline-stepper"
      className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-xl border border-zinc-200 bg-white px-4 py-3 text-xs dark:border-zinc-800 dark:bg-zinc-900"
    >
      <span className="mr-1 font-medium text-zinc-700 dark:text-zinc-200">创作流程</span>
      {steps.map((step, i) => (
        <span key={step.key} className="flex items-center gap-2">
          {i > 0 && <span className="text-zinc-300 dark:text-zinc-600">→</span>}
          <StepChip step={step} />
        </span>
      ))}
      {allDone && (
        <span className="ml-1 text-[11px] text-emerald-600 dark:text-emerald-400">全部完成——可直接下载或回画布加工</span>
      )}
      {currentSteps
        .filter((s) => s.action)
        .map((s) => (
          <span key={s.key} className="ml-1 text-[11px] font-medium text-amber-600 dark:text-amber-400">
            {s.action}
          </span>
        ))}
    </div>
  );
}

/* ================= 段徽标 + 重试/重出（bundle nN） ================= */

function SegmentBadge({
  index,
  status,
  storyboardId,
  onMutated,
  locked = false,
}: {
  index: number;
  status: string;
  storyboardId: number;
  onMutated: () => void;
  locked?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const badge = segmentBadge(status);

  async function act(action: "retry" | "regenerate") {
    setBusy(true);
    try {
      if (action === "retry") await retrySegment(storyboardId, index);
      else await regenerateSegment(storyboardId, index);
      onMutated();
    } finally {
      setBusy(false);
    }
  }

  const actionButton =
    locked || busy ? null : status === "failed" ? (
      <button
        aria-label={`重试第 ${index} 段`}
        onClick={() => void act("retry")}
        className="cursor-pointer text-[11px] font-medium text-amber-600 hover:underline dark:text-amber-400"
      >
        重试
      </button>
    ) : status === "completed" ? (
      <button
        aria-label={`重出第 ${index} 段`}
        onClick={() => void act("regenerate")}
        className="inline-flex min-h-6 min-w-6 cursor-pointer items-center text-[11px] font-medium text-amber-600 hover:underline dark:text-amber-400"
      >
        重出
      </button>
    ) : null;

  return (
    <span className="flex items-center gap-1.5">
      <span data-testid={`segment-badge-${index}`} className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${TONE_CLASS[badge.tone]}`}>
        {badge.label}
      </span>
      {actionButton}
    </span>
  );
}

/* ================= 一键生成全片（bundle rN） ================= */

const STYLE_PRESETS: { value: string; label: string }[] = [
  { value: "", label: "默认风格" },
  { value: "cinematic film look, muted cool tones", label: "写实电影感" },
  { value: "japanese anime style, clean line art", label: "日系动画" },
  { value: "vintage film grain, warm tones", label: "复古胶片" },
  { value: "cyberpunk neon, high contrast", label: "赛博霓虹" },
];

function GeneratePanel({ storyboard, onMutated }: { storyboard: Storyboard; onMutated: () => void }) {
  const [estimating, setEstimating] = useState(false);
  const [estimate, setEstimate] = useState<{ model: string; estimate: NonNullable<
    Awaited<ReturnType<typeof estimateStoryboardGeneration>>["estimate"]
  > } | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [concurrency, setConcurrency] = useState(1);
  const [style, setStyle] = useState(String(storyboard.metadata?.style_prompt ?? ""));
  const channelsQuery = useQuery({ queryKey: ["provider-channels"], queryFn: fetchProviderChannels });
  const videoModels = Array.from(
    new Set(
      (channelsQuery.data?.channels ?? [])
        .filter((ch) => ch.domain === "video" && ch.enabled)
        .flatMap((ch) => ch.models.filter((m) => m.enabled).map((m) => m.model_id)),
    ),
  );
  const model = estimate?.model || videoModels[0] || "";
  const generating = storyboard.status === "generating";
  const allCompleted = storyboard.segments.length > 0 && storyboard.segments.every((s) => s.status === "completed");

  async function runEstimate() {
    setEstimating(true);
    try {
      const resp = await estimateStoryboardGeneration(storyboard.id, model, concurrency);
      if (resp.requires_confirm && resp.estimate) setEstimate({ model, estimate: resp.estimate });
    } finally {
      setEstimating(false);
    }
  }

  async function confirmGenerate() {
    if (!estimate) return;
    setConfirming(true);
    try {
      await confirmStoryboardGeneration(storyboard.id, estimate.model, concurrency);
      setEstimate(null);
      onMutated();
    } finally {
      setConfirming(false);
    }
  }

  async function stopGenerate() {
    setStopping(true);
    try {
      await stopStoryboardGeneration(storyboard.id);
      onMutated();
    } finally {
      setStopping(false);
    }
  }

  if (generating) {
    const percent = progressPercent(storyboard.segments);
    return (
      <div className="flex items-center gap-3" data-testid="generate-progress">
        <progress
          aria-label="生成进度"
          role="progressbar"
          aria-valuenow={percent}
          aria-valuemin={0}
          aria-valuemax={100}
          value={percent}
          max={100}
          className="h-2 w-40 accent-amber-500"
        />
        <span className="text-xs text-zinc-500">{percent}%</span>
        <Button variant="outline" size="sm" disabled={stopping} onClick={() => void stopGenerate()}>
          <Square className="h-3.5 w-3.5" /> {stopping ? "停止中…" : "停止生成"}
        </Button>
      </div>
    );
  }

  return (
    <div className="grid w-full grid-cols-2 items-center gap-2 sm:flex sm:w-auto sm:flex-wrap sm:justify-end">
      <select
        aria-label="生成并发"
        value={concurrency}
        onChange={(e) => setConcurrency(Number(e.target.value))}
        className="h-8 rounded-lg border border-zinc-200 bg-white px-2 text-xs outline-none focus:border-amber-500 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200"
      >
        {[1, 2, 3, 4].map((n) => (
          <option key={n} value={n}>
            并发 ×{n}
          </option>
        ))}
      </select>
      <select
        aria-label="全局风格"
        value={style}
        onChange={(e) => {
          const next = e.target.value;
          setStyle(next);
          void setStoryboardStyle(storyboard.id, next).catch(() => undefined);
        }}
        className="h-8 rounded-lg border border-zinc-200 bg-white px-2 text-xs outline-none focus:border-amber-500 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200"
      >
        {STYLE_PRESETS.map((p) => (
          <option key={p.value} value={p.value}>
            {p.label}
          </option>
        ))}
      </select>
      {videoModels.length === 0 ? (
        <span className="text-xs text-zinc-500">请先在模型渠道接入视频渠道</span>
      ) : (
        <select
          aria-label="生成模型"
          value={model}
          onChange={(e) => setEstimate((prev) => (prev ? { ...prev, model: e.target.value } : prev))}
          className="col-span-2 h-8 w-full rounded-lg border border-zinc-200 bg-white px-2 text-xs outline-none focus:border-amber-500 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 sm:col-span-1 sm:w-auto"
        >
          {videoModels.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      )}
      {estimate ? (
        <span
          data-testid="estimate-confirm"
          className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs text-amber-800 dark:border-amber-700 dark:bg-[#271c05] dark:text-amber-300"
        >
          <Wand2 className="h-3.5 w-3.5" />
          {estimateText(estimate.estimate)}
          {typeof estimate.estimate.est_minutes === "number" && estimate.estimate.est_minutes > 0 && (
            <span className="text-amber-600 dark:text-amber-400">· 预计 ~{estimate.estimate.est_minutes} 分钟</span>
          )}
          <button
            aria-label="确认开始"
            disabled={confirming}
            onClick={() => void confirmGenerate()}
            className="cursor-pointer font-semibold text-amber-700 hover:underline disabled:opacity-50 dark:text-amber-300"
          >
            {confirming ? "启动中…" : "确认开始"}
          </button>
          <button aria-label="取消生成" onClick={() => setEstimate(null)} className="cursor-pointer text-zinc-400 hover:underline">
            取消
          </button>
        </span>
      ) : (
        <Fragment>
          <Button
            size="sm"
            disabled={estimating || videoModels.length === 0 || storyboard.segments.length === 0 || allCompleted}
            title={
              allCompleted ? "全部分段已完成；调整内容请用分段卡上的「重出」逐段迭代" : undefined
            }
            onClick={() => void runEstimate()}
            className="col-span-2 w-full sm:col-span-1 sm:w-auto"
          >
            <RefreshCw className="h-3.5 w-3.5" /> {estimating ? "估算中…" : "一键生成全片"}
            <span className="hidden text-[11px] font-normal opacity-80 sm:inline">· 按分镜顺序逐段生成</span>
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="col-span-2 w-full sm:col-span-1 sm:w-auto"
            disabled={estimating || videoModels.length === 0}
            aria-label="接力生成关键帧"
            title="逐段传递角色形象：上一段的画面作为下一段的参考"
            onClick={async () => {
              setEstimating(true);
              try {
                await generateKeyframes(storyboard.id, { chain: true, count: 4 });
                onMutated();
              } finally {
                setEstimating(false);
              }
            }}
          >
            <ImageIcon className="h-3.5 w-3.5" /> 接力生成关键帧
            <span className="hidden text-[11px] font-normal opacity-80 sm:inline">· 逐段传递角色形象</span>
          </Button>
        </Fragment>
      )}
    </div>
  );
}

/* ================= 角色档案（bundle iN） ================= */

function CharacterPanel({ storyboard, onMutated }: { storyboard: Storyboard; onMutated: () => void }) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [hasRefImage, setHasRefImage] = useState(false);
  const [savingName, setSavingName] = useState<string | null>(null);
  const [attachOpen, setAttachOpen] = useState(false);
  const [attaching, setAttaching] = useState(false);
  const profiles = storyboard.character_profiles ?? [];
  const refImageUrl = storyboard.character_ref_image_url || "";
  const firstSegmentCompleted = (storyboard.segments ?? []).some((s) => s.segment_index === 1 && s.status === "completed");
  const showPreview = !!refImageUrl || hasRefImage;
  const showHint = hasRefImage || !!refImageUrl;

  async function handleUpload(file: File | undefined) {
    if (!file) return;
    setUploading(true);
    setError("");
    try {
      const uploaded = await uploadDataUrl(await readFileAsDataUrl(file));
      await setStoryboardCharacterRefImage(storyboard.id, uploaded.url);
      setHasRefImage(true);
      onMutated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "上传失败");
    } finally {
      setUploading(false);
    }
  }

  const libraryQuery = useQuery({
    queryKey: ["characters-lib"],
    queryFn: fetchCharacterLibrary,
    enabled: attachOpen,
  });

  async function saveToLibrary(name: string, description: string) {
    setSavingName(name);
    setError("");
    try {
      await saveCharacterToLibrary(name, description);
    } catch (e) {
      setError(e instanceof Error ? e.message : "存入失败");
    } finally {
      setSavingName(null);
    }
  }

  async function bringIn(ids: number[]) {
    setAttaching(true);
    setError("");
    try {
      await attachCharacters(storyboard.id, ids);
      setAttachOpen(false);
      onMutated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "带入失败");
    } finally {
      setAttaching(false);
    }
  }

  async function removeRefImage() {
    setError("");
    try {
      await setStoryboardCharacterRefImage(storyboard.id, "");
      onMutated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "移除失败");
    }
  }

  return (
    <section
      data-testid="character-card"
      className="rounded-xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-medium text-zinc-700 dark:text-zinc-200">角色档案</h2>
        <div className="grid w-full grid-cols-2 items-center gap-2 sm:flex sm:w-auto sm:flex-wrap">
          <input
            ref={fileInputRef}
            data-testid="character-ref-input"
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(e) => void handleUpload(e.target.files?.[0])}
          />
          <Button variant="outline" size="sm" disabled={uploading} onClick={() => fileInputRef.current?.click()}>
            {uploading ? "上传中…" : refImageUrl ? "更换参考图" : "+ 角色参考图"}
          </Button>
          <Button variant="ghost" size="sm" aria-label="带入角色" onClick={() => setAttachOpen((v) => !v)}>
            <Upload className="h-3.5 w-3.5" /> 带入角色
          </Button>
          {refImageUrl && (
            <Button variant="ghost" size="sm" onClick={() => void removeRefImage()}>
              移除参考图
            </Button>
          )}
        </div>
      </div>
      {showPreview && (
        <div className="mt-2 flex items-center gap-3">
          {refImageUrl && (
            <img
              aria-label="角色参考图"
              src={refImageUrl}
              alt="角色参考图"
              className="h-16 w-16 rounded-lg border border-zinc-200 object-cover dark:border-zinc-700"
            />
          )}
          {showHint && firstSegmentCompleted && (
            <span className="text-xs text-amber-600 dark:text-amber-400">重出第 1 段以应用参考图</span>
          )}
        </div>
      )}
      {profiles.length === 0 ? (
        <p className="mt-2 text-xs text-zinc-500">拆分后自动生成角色档案</p>
      ) : (
        <ul className="mt-2 space-y-1.5">
          {profiles.map((p) => (
            <li key={p.name} className="flex items-center gap-2 text-xs text-zinc-600 dark:text-zinc-300">
              <span className="font-medium text-zinc-800 dark:text-zinc-100">{p.name}</span>
              <span className="min-w-0 flex-1 truncate">
                <span className="mx-1 text-zinc-300">·</span>
                {p.description}
              </span>
              <button
                aria-label={`存入角色库 ${p.name}`}
                disabled={savingName === p.name}
                onClick={() => void saveToLibrary(p.name ?? "", p.description ?? "")}
                className="shrink-0 cursor-pointer text-[11px] text-amber-600 hover:underline disabled:opacity-50 dark:text-amber-400"
              >
                {savingName === p.name ? "保存中…" : "存入角色库"}
              </button>
            </li>
          ))}
        </ul>
      )}
      {attachOpen && (
        <div className="mt-2 rounded-lg border border-zinc-200 p-2 dark:border-zinc-700">
          {(libraryQuery.data?.characters ?? []).length === 0 ? (
            <p className="p-1 text-xs text-zinc-400">角色库为空——在左侧导航「角色库」页添加后即可带入</p>
          ) : (
            <div className="space-y-1">
              {(libraryQuery.data?.characters ?? []).map((c: CharacterAsset) => (
                <button
                  key={c.id}
                  onClick={() => void bringIn([c.id])}
                  disabled={attaching}
                  className="flex w-full cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-zinc-100 disabled:opacity-50 dark:hover:bg-zinc-800"
                >
                  <span className="font-medium text-zinc-700 dark:text-zinc-200">{c.name}</span>
                  <span className="min-w-0 flex-1 truncate text-zinc-400">{c.description}</span>
                  <span className="text-amber-600 dark:text-amber-400">带入</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
    </section>
  );
}

/* ================= 成片合成（bundle cN） ================= */

function FinalVideoPanel({ storyboard, onMutated }: { storyboard: Storyboard; onMutated: () => void }) {
  const navigate = useNavigate();
  const [composing, setComposing] = useState(false);
  const [flowing, setFlowing] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState("");
  const bgmInputRef = useRef<HTMLInputElement>(null);
  const [bgmUploading, setBgmUploading] = useState(false);
  const [subtitleOpen, setSubtitleOpen] = useState(false);
  const [subtitleText, setSubtitleText] = useState("");
  const [burning, setBurning] = useState(false);
  const [bgmUrl, setBgmUrl] = useState<string | null>(null);

  const metadata = storyboard.metadata ?? {};
  const composeStatus = typeof metadata.compose_status === "string" ? metadata.compose_status : "idle";
  const finalVideoUrl = storyboard.final_video_url;
  const completedSegments = storyboard.segments.filter((s) => s.status === "completed");
  const failedCount = storyboard.segments.filter((s) => s.status === "failed").length;
  const badge = composeBadge(finalVideoUrl ? "done" : composeStatus);

  if (!finalVideoUrl && completedSegments.length === 0) return null;
  const running = composeStatus === "running" && !finalVideoUrl;

  async function startCompose() {
    setComposing(true);
    setError("");
    try {
      await composeStoryboard(storyboard.id);
      onMutated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "合成启动失败");
    } finally {
      setComposing(false);
    }
  }

  async function flowToCanvas() {
    setFlowing(true);
    setError("");
    try {
      const resp = await storyboardToCanvas(storyboard.id);
      navigate(`/canvas/${resp.project_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "回流失败");
    } finally {
      setFlowing(false);
    }
  }

  async function downloadFilm() {
    if (!finalVideoUrl) return;
    setDownloading(true);
    try {
      await downloadMedia(finalVideoUrl, `${storyboard.title || "成片"}.mp4`);
    } catch {
      setError("下载失败，请稍后重试");
    } finally {
      setDownloading(false);
    }
  }

  return (
    <section
      data-testid="final-video-panel"
      className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 text-sm font-medium text-zinc-700 dark:text-zinc-200">
          <Film className="h-4 w-4 text-amber-500" /> 成片
          <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${TONE_CLASS[badge.tone]}`}>{badge.label}</span>
        </h2>
        <div className="flex flex-wrap items-center gap-2">
          {finalVideoUrl ? (
            <Fragment>
              <Button variant="outline" size="sm" disabled={downloading} onClick={() => void downloadFilm()}>
                <Download className="h-3.5 w-3.5" /> {downloading ? "下载中…" : "下载成片"}
              </Button>
              <Button variant="outline" size="sm" disabled={flowing} onClick={() => void flowToCanvas()}>
                <ExternalLink className="h-3.5 w-3.5" /> {flowing ? "回流中…" : "回流入画布"}
              </Button>
              <Button variant="outline" size="sm" aria-label="字幕" onClick={() => setSubtitleOpen((v) => !v)}>
                <Captions className="h-3.5 w-3.5" /> 字幕
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={composing || running}
                onClick={() => void startCompose()}
              >
                <RefreshCw className="h-3.5 w-3.5" /> 重新合成
              </Button>
            </Fragment>
          ) : (
            <span className="flex items-center gap-2">
              {failedCount > 0 && (
                <span className="text-xs text-zinc-500">
                  将跳过 {failedCount} 个失败段
                </span>
              )}
              <Button size="sm" disabled={composing || running} onClick={() => void startCompose()}>
                <ImageIcon className="h-3.5 w-3.5" /> {running || composing ? "合成中…" : "合成成片"}
              </Button>
            </span>
          )}
        </div>
      </div>
      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <input
          ref={bgmInputRef}
          data-testid="bgm-input"
          type="file"
          accept="audio/*"
          className="hidden"
          onChange={async (e) => {
            const file = e.target.files?.[0];
            if (!file) return;
            setBgmUploading(true);
            setError("");
            try {
              const uploaded = await uploadDataUrl(await readFileAsDataUrl(file));
              await setStoryboardBgm(storyboard.id, uploaded.url);
              setBgmUrl(uploaded.url);
              onMutated();
            } catch {
              setError("BGM 上传失败");
            } finally {
              setBgmUploading(false);
              e.target.value = "";
            }
          }}
        />
        <Button variant="outline" size="sm" disabled={bgmUploading} onClick={() => bgmInputRef.current?.click()}>
          <Upload className="h-3.5 w-3.5" /> {bgmUploading ? "上传中…" : "BGM"}
        </Button>
        {bgmUrl || metadata.bgm_url ? (
          <Fragment>
            <span className="text-xs text-zinc-500">{String(bgmUrl || String(metadata.bgm_url)).split("/").pop()}</span>
            <button
              aria-label="移除 BGM"
              onClick={async () => {
                await setStoryboardBgm(storyboard.id, "");
                onMutated();
              }}
              className="cursor-pointer text-[11px] text-zinc-400 hover:underline"
            >
              移除
            </button>
          </Fragment>
        ) : (
          <span className="text-[11px] text-zinc-400">上传后合成自动混入（音量 25%）</span>
        )}
      </div>
      {subtitleOpen && (
        <div data-testid="subtitle-panel" className="mt-2 rounded-lg border border-zinc-200 p-3 dark:border-zinc-700">
          <textarea
            aria-label="字幕文本"
            value={subtitleText}
            onChange={(e) => setSubtitleText(e.target.value)}
            rows={4}
            placeholder="粘贴字幕文本（按行，自动按时长均分）；或直接粘贴 .srt 内容"
            className="w-full rounded-lg border border-zinc-200 p-2 text-xs outline-none focus:border-zinc-400 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
          />
          <div className="mt-2 flex items-center gap-2">
            <Button
              size="sm"
              disabled={burning || !subtitleText.trim()}
              onClick={async () => {
                if (!finalVideoUrl) return;
                setBurning(true);
                setError("");
                try {
                  const resp = await burnSubtitles(finalVideoUrl, subtitleText);
                  await downloadMedia(resp.url, `${storyboard.title || "成片"}-字幕版.mp4`);
                  setSubtitleOpen(false);
                  setSubtitleText("");
                } catch (e) {
                  setError(e instanceof Error ? e.message : "字幕烧录失败");
                } finally {
                  setBurning(false);
                }
              }}
            >
              {burning ? "烧录中…" : "烧录并下载"}
            </Button>
            <span className="text-[11px] text-zinc-400">文本按行按时长均分；SRT 原样烧录。产物直接下载，不覆盖原片。</span>
          </div>
        </div>
      )}
      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
      {finalVideoUrl && (
        <div className="mt-3 overflow-hidden rounded-xl bg-[#18181b] p-2">
          <video
            aria-label="成片播放器"
            src={finalVideoUrl}
            controls
            className="mx-auto max-h-[420px] w-full rounded-lg"
          />
        </div>
      )}
    </section>
  );
}

/* ================= 分段卡（bundle lN） ================= */

function SegmentEditor({
  seg,
  index,
  total,
  disabled = false,
  onChange,
  onMoveUp,
  onMoveDown,
  onInsertAfter,
  onRemove,
}: {
  seg: StoryboardSegment;
  index: number;
  total: number;
  disabled?: boolean;
  onChange: (patch: Partial<StoryboardSegment>) => void;
  onMoveUp: (index: number) => void;
  onMoveDown: (index: number) => void;
  onInsertAfter: (segmentIndex: number) => void;
  onRemove: (index: number) => void;
}) {
  const segmentIndex = seg.segment_index ?? index + 1;
  const label = (field: string) => `分段 ${segmentIndex} ${field}`;

  return (
    <div
      className="rounded-xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900"
      data-testid={`segment-editor-${index}`}
    >
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-xs font-medium text-zinc-500">第 {segmentIndex} 段</span>
        {!disabled && (
          <span className="flex items-center gap-1">
            {index > 0 && (
              <button
                aria-label={`上移分段 ${segmentIndex}`}
                onClick={() => onMoveUp(index)}
                className="inline-flex min-h-6 min-w-6 cursor-pointer items-center justify-center rounded px-1.5 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800"
              >
                ↑
              </button>
            )}
            {index < total - 1 && (
              <button
                aria-label={`下移分段 ${segmentIndex}`}
                onClick={() => onMoveDown(index)}
                className="inline-flex min-h-6 min-w-6 cursor-pointer items-center justify-center rounded px-1.5 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800"
              >
                ↓
              </button>
            )}
            <button
              aria-label={`在第 ${segmentIndex} 段后插入`}
              onClick={() => onInsertAfter(segmentIndex)}
              className="inline-flex min-h-6 cursor-pointer items-center rounded px-1.5 text-[11px] text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800"
            >
              + 插入
            </button>
            <button
              aria-label={`删除分段 ${segmentIndex}`}
              onClick={() => onRemove(index)}
              className="cursor-pointer text-zinc-300 hover:text-red-500"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </span>
        )}
      </div>
      <textarea
        aria-label={label("提示词")}
        value={seg.prompt}
        disabled={disabled}
        onChange={(e) => onChange({ prompt: e.target.value })}
        rows={2}
        className="mt-2 w-full rounded-lg border border-zinc-200 p-2 text-sm outline-none focus:border-zinc-400 disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
      />
      <div className="mt-2 flex items-center gap-2">
        <span className="text-xs text-zinc-400">时长</span>
        {[4, 6, 10].map((d) => (
          <button
            key={d}
            type="button"
            aria-label={`分段 ${segmentIndex} 时长 ${d} 秒`}
            disabled={disabled}
            onClick={() => onChange({ target_duration_s: d })}
            className={`inline-flex min-h-6 cursor-pointer items-center rounded-full px-2.5 text-[11px] disabled:opacity-50 ${
              (seg.target_duration_s ?? 6) === d
                ? "bg-amber-500 text-zinc-950"
                : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300"
            }`}
          >
            {d}s
          </button>
        ))}
      </div>
      <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-3">
        <input
          aria-label={label("镜头")}
          value={seg.camera_motion}
          disabled={disabled}
          onChange={(e) => onChange({ camera_motion: e.target.value })}
          placeholder="镜头运动"
          className="w-full rounded-lg border border-zinc-200 px-2 py-1.5 text-xs outline-none focus:border-zinc-400 disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
        />
        <input
          aria-label={label("情绪")}
          value={seg.mood}
          disabled={disabled}
          onChange={(e) => onChange({ mood: e.target.value })}
          placeholder="情绪氛围"
          className="w-full rounded-lg border border-zinc-200 px-2 py-1.5 text-xs outline-none focus:border-zinc-400 disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
        />
        <input
          aria-label={label("音频")}
          value={seg.audio_cue}
          disabled={disabled}
          onChange={(e) => onChange({ audio_cue: e.target.value })}
          placeholder="音频提示"
          className="w-full rounded-lg border border-zinc-200 px-2 py-1.5 text-xs outline-none focus:border-zinc-400 disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
        />
      </div>
      {seg.status === "completed" && seg.output_video_url && (
        <video
          aria-label={label("产物")}
          src={seg.output_video_url}
          controls
          muted
          className="mt-2 h-20 w-32 rounded-lg border border-zinc-200 object-cover dark:border-zinc-700"
        />
      )}
    </div>
  );
}

/* ================= 关键帧条（bundle uN） ================= */

function KeyframeStrip({
  storyboardId,
  index,
  seg,
  locked = false,
  onMutated,
}: {
  storyboardId: number;
  index: number;
  seg: StoryboardSegment;
  locked?: boolean;
  onMutated: () => void;
}) {
  const keyframeUrl = seg.keyframe_url || "";
  const keyframeTaskId = seg.keyframe_task_id;
  const taskQuery = useQuery({
    queryKey: ["keyframe-task", keyframeTaskId],
    queryFn: () => apiFetch<{ task: { status: string; output_url?: string | null; params?: { extra_outputs?: (string | null)[] } } }>(`/api/tasks/${keyframeTaskId}`),
    enabled: !!keyframeTaskId && !keyframeUrl,
    refetchInterval: (query) =>
      !["completed", "failed"].includes(String(query.state.data?.task?.status)) && 3000,
  });

  async function generate() {
    await generateKeyframes(storyboardId, { segments: [index], count: 4 });
    onMutated();
  }

  async function select(url: string) {
    await selectKeyframe(storyboardId, index, url);
    onMutated();
  }

  if (locked) return null;

  const candidates: string[] = [];
  const task = taskQuery.data?.task;
  if (task?.status === "completed") {
    if (task.output_url) candidates.push(task.output_url);
    for (const extra of task.params?.extra_outputs ?? []) {
      if (extra && !candidates.includes(extra)) candidates.push(extra);
    }
  }

  return (
    <div
      data-testid={`keyframe-strip-${index}`}
      className="mt-2 flex flex-wrap items-center gap-2 rounded-lg bg-zinc-50 px-2.5 py-2 dark:bg-zinc-800/60"
    >
      <span className="flex items-center gap-1 text-[11px] font-medium text-zinc-500">
        <ImageIcon className="h-3 w-3 text-amber-500" /> 关键帧
      </span>
      {keyframeUrl ? (
        <Fragment>
          <img
            aria-label={`已定帧 ${index}`}
            src={keyframeUrl}
            alt="已定帧"
            className="h-12 w-16 rounded border border-amber-400 object-cover ring-1 ring-amber-400"
          />
          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:bg-amber-950 dark:text-amber-300">
            已定帧
          </span>
          <button
            aria-label={`清除定帧 ${index}`}
            onClick={() => void select("")}
            className="cursor-pointer text-[11px] text-zinc-400 hover:underline"
          >
            清除定帧
          </button>
        </Fragment>
      ) : keyframeTaskId ? (
        taskQuery.isLoading ? (
          <span className="text-[11px] text-zinc-400">候选加载中…</span>
        ) : candidates.length > 0 ? (
          <Fragment>
            {candidates.map((url, i) => (
              <button
                key={url}
                aria-label={`关键帧候选 ${i + 1}`}
                onClick={() => void select(url)}
                className="cursor-pointer rounded border border-zinc-300 hover:ring-2 hover:ring-amber-400 dark:border-zinc-600"
              >
                <img src={url} alt={`候选 ${i + 1}`} className="h-12 w-16 rounded object-cover" />
              </button>
            ))}
            <span className="text-[11px] text-zinc-400">点选定为该段首帧</span>
          </Fragment>
        ) : task?.status === "failed" ? (
          <Fragment>
            <span className="text-[11px] text-red-500">关键帧生成失败</span>
            <Button variant="outline" size="sm" onClick={() => void generate()}>
              重试
            </Button>
          </Fragment>
        ) : (
          <span className="flex items-center gap-1.5 text-[11px] text-zinc-400">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-500" />
            关键帧生成中…
          </span>
        )
      ) : (
        <Button variant="outline" size="sm" onClick={() => void generate()}>
          <ImageIcon className="h-3.5 w-3.5" /> 生成关键帧 ×4
          <span className="hidden text-[11px] font-normal opacity-80 sm:inline">· 出 4 张画面草稿（约 10 秒）</span>
        </Button>
      )}
    </div>
  );
}

/* ================= 分镜详情页（bundle gN） ================= */

export function StoryboardDetailPage() {
  const queryClient = useQueryClient();
  const { id } = useParams<{ id: string }>();
  const storyboardId = Number(id);
  const detailQuery = useQuery({
    queryKey: ["storyboard", storyboardId],
    queryFn: () => fetchStoryboard(storyboardId),
    enabled: Number.isFinite(storyboardId),
    refetchInterval: (query) => query.state.data?.storyboard?.metadata?.compose_status === "running" && 3000,
  });
  const [localSegments, setLocalSegments] = useState<StoryboardSegment[] | null>(null);
  const [splitting, setSplitting] = useState(false);
  const [error, setError] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  const storyboard = detailQuery.data?.storyboard;
  const segments = localSegments ?? storyboard?.segments ?? [];

  useEffect(() => {
    if (storyboard?.segments) setLocalSegments(storyboard.segments);
  }, [storyboard]);

  const status = storyboard?.status;
  useEffect(() => {
    if (status !== "generating" || !Number.isFinite(storyboardId)) return;
    const controller = new AbortController();
    abortRef.current = controller;
    void subscribeStoryboardGeneration(
      storyboardId,
      (type, data) => {
        if (type === "segment_update") {
          const index = Number(data.index);
          setLocalSegments((prev) =>
            (prev ?? []).map((seg, i) =>
              index === i + 1
                ? {
                    ...seg,
                    status: String(data.status ?? seg.status ?? ""),
                    output_video_url: (data.output_video_url as string | undefined) ?? seg.output_video_url,
                  }
                : seg,
            ),
          );
        } else if (type === "done" || type === "storyboard_update") {
          void queryClient.invalidateQueries({ queryKey: ["storyboard", storyboardId] });
        }
      },
      {
        signal: controller.signal,
        fallback: async () => ({
          type: "snapshot",
          data: { storyboard: (await fetchStoryboard(storyboardId)).storyboard },
        }),
      },
    ).catch(() => undefined);
    return () => {
      controller.abort();
      abortRef.current = null;
    };
  }, [status, storyboardId, queryClient]);

  async function runSplit() {
    if (!storyboard) return;
    setSplitting(true);
    setError("");
    try {
      const resp = await splitStoryboard(storyboardId);
      setLocalSegments(resp.segments);
      void queryClient.invalidateQueries({ queryKey: ["storyboards"] });
    } catch (e) {
      setError(e instanceof Error ? e.message : "拆分失败");
    } finally {
      setSplitting(false);
    }
  }

  function patchSegment(index: number, patch: Partial<StoryboardSegment>) {
    setLocalSegments((prev) => (prev ?? []).map((seg, i) => (index === i ? { ...seg, ...patch } : seg)));
  }

  function moveSegment(index: number, delta: number) {
    setLocalSegments((prev) => {
      const next = [...(prev ?? [])];
      const target = index + delta;
      if (target < 0 || target >= next.length) return prev;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  }

  function insertAfter(segmentIndex: number) {
    setLocalSegments((prev) => {
      const next = [...(prev ?? [])];
      next.splice(segmentIndex, 0, {
        segment_index: segmentIndex + 1,
        prompt: "",
        camera_motion: "",
        mood: "",
        audio_cue: "",
        status: "",
      });
      return next;
    });
  }

  function removeSegment(index: number) {
    setLocalSegments((prev) => (prev ?? []).filter((_, i) => index !== i));
  }

  async function saveSegments() {
    await saveStoryboardSegments(storyboardId, segments ?? []);
  }

  return (
    <div className="min-h-screen bg-zinc-50">
      <header className="flex items-center justify-between border-b border-zinc-200 bg-white px-6 py-4">
        <div className="flex items-center gap-3">
          <Link
            to="/storyboards"
            aria-label="返回分镜列表"
            className="inline-flex items-center justify-center p-1.5 -m-1.5"
          >
            <ArrowLeft className="h-4 w-4 text-zinc-500" />
          </Link>
          <h1 className="text-lg font-semibold">{storyboard?.title ?? "分镜"}</h1>
        </div>
        <div className="flex w-full flex-wrap items-center justify-end gap-2 sm:w-auto">
          {storyboard && Number.isFinite(storyboardId) && (
            <GeneratePanel
              storyboard={storyboard}
              onMutated={() => {
                setLocalSegments(null);
                void queryClient.invalidateQueries({ queryKey: ["storyboard", storyboardId] });
              }}
            />
          )}
          <Button onClick={() => void runSplit()} disabled={splitting || !storyboard}>
            <Wand2 className="h-4 w-4" /> {splitting ? "AI 拆分中…" : "AI 拆分分镜"}
          </Button>
        </div>
      </header>
      <main className="mx-auto max-w-4xl space-y-4 p-6">
        {storyboard && Number.isFinite(storyboardId) && <PipelineStepper storyboard={storyboard} />}
        {storyboard && Number.isFinite(storyboardId) && (
          <CharacterPanel
            storyboard={storyboard}
            onMutated={() => void queryClient.invalidateQueries({ queryKey: ["storyboard", storyboardId] })}
          />
        )}
        {storyboard && Number.isFinite(storyboardId) && (
          <FinalVideoPanel
            storyboard={storyboard}
            onMutated={() => {
              setLocalSegments(null);
              void queryClient.invalidateQueries({ queryKey: ["storyboard", storyboardId] });
            }}
          />
        )}
        <div className="rounded-xl border border-zinc-200 bg-white p-4">
          <p className="text-xs text-zinc-400">剧本简报</p>
          <p className="mt-1 text-sm text-zinc-700">{storyboard?.short_description ?? "—"}</p>
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <section className="space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium text-zinc-500">
              分镜表（{segments.length} 段 · 每段 {storyboard?.segment_duration ?? 6} 秒）
            </h2>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void saveSegments()}
              disabled={!localSegments || status === "generating"}
              title={status === "generating" ? "生成中不可保存，请等待生成完成" : undefined}
            >
              保存分镜
            </Button>
            <Button
              variant="outline"
              size="sm"
              aria-label="存为模板"
              onClick={async () => {
                await createStoryboardTemplate(`${storyboard?.title ?? "未命名"} · 模板`, (localSegments ?? segments).map((s) => ({
                  prompt: s.prompt,
                  camera_motion: s.camera_motion,
                  mood: s.mood,
                  audio_cue: s.audio_cue,
                })));
              }}
            >
              存为模板
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                setLocalSegments([
                  ...(localSegments ?? []),
                  {
                    segment_index: (localSegments?.length ?? 0) + 1,
                    prompt: "",
                    camera_motion: "",
                    mood: "",
                    audio_cue: "",
                    status: "",
                  },
                ])
              }
            >
              <Plus className="h-3.5 w-3.5" /> 添加分镜
            </Button>
          </div>
          {segments.map((seg, i) => (
            <div key={i} className="relative">
              <SegmentEditor
                seg={seg}
                index={i}
                total={segments.length}
                disabled={status === "generating"}
                onChange={(patch) => patchSegment(i, patch)}
                onMoveUp={(idx) => moveSegment(idx, -1)}
                onMoveDown={(idx) => moveSegment(idx, 1)}
                onInsertAfter={insertAfter}
                onRemove={removeSegment}
              />
              <KeyframeStrip
                storyboardId={storyboardId}
                index={seg.segment_index ?? i + 1}
                seg={seg}
                locked={status === "generating"}
                onMutated={() => void queryClient.invalidateQueries({ queryKey: ["storyboard", storyboardId] })}
              />
              <div className="absolute right-3 top-3 flex items-center gap-1.5">
                <SegmentBadge
                  index={seg.segment_index ?? i + 1}
                  status={seg.status}
                  storyboardId={storyboardId}
                  locked={status === "generating"}
                  onMutated={() => void queryClient.invalidateQueries({ queryKey: ["storyboard", storyboardId] })}
                />
              </div>
            </div>
          ))}
          {segments.length === 0 && !detailQuery.isLoading && (
            <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-zinc-300 py-10 text-center">
              <Clapperboard className="h-6 w-6 text-zinc-300" />
              <p className="text-sm text-zinc-400">还没有分镜——点右上角「AI 拆分分镜」</p>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
