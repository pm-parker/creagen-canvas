import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Copy,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  LockKeyhole,
  RefreshCw,
  Save,
  Sparkles,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import { ThemeToggle } from "@/components/ThemeToggle";
import { useAuthStore } from "@/stores/auth";

interface DefaultModelPick {
  channel_id: number;
  model: string;
}

interface DefaultModels {
  image?: DefaultModelPick;
  video?: DefaultModelPick;
  llm?: DefaultModelPick;
  audio?: DefaultModelPick;
  vision?: DefaultModelPick;
}

interface ChannelSummary {
  id: number;
  name: string;
  models: { model_id: string; meta_json?: { vision?: boolean } | null }[];
}

type DomainKey = "image" | "video" | "llm" | "audio";

const DOMAINS: { key: DomainKey; label: string }[] = [
  { key: "image", label: "图片" },
  { key: "video", label: "视频" },
  { key: "llm", label: "LLM" },
  { key: "audio", label: "音频" },
];

function agentSystemPrompt(origin: string): string {
  return `你可以调用 CreaGen Canvas（自托管 AI 创作画布）的 Agent API 完成图片与视频创作。基址：${origin}

规则：
1. 每次会话开始先 GET /api/agent/v1/me 自检（Header X-API-Key）；失败则停止并告知用户去「设置 → Agent API」生成。
2. 需要画布时先 GET /api/agent/v1/projects 找现有项目；没有合适的就 POST 创建一个。
3. 生成前先 GET /api/agent/v1/models 选模型：用户没指定时优先 is_free=true 的；
   用户要视频时确认 duration（默认 5 秒）与 resolution（竖屏用 "480x832"）。
4. 参考图（图生视频/图生图）必须用站内 /outputs/ 路径（从上一次生成结果的 output_url 取），
   放进 params.reference_urls。
5. 提交 POST /api/agent/v1/generate 拿 task_id 后，每 8 秒轮询 GET /api/agent/v1/tasks/{id}，
   超过 10 分钟未完成则告知用户稍后在画布查看。failed 时把 error 原文转告用户。
6. 产物 URL = 基址 + output_url，直接给用户这个链接。
7. 收到 429 就等 60 秒再试；401 则提示用户轮换 key 后重新提供。
完整文档：${origin} 打开的 Web 端仓库 docs/agent-api.md`;
}

export function SettingsPage() {
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === "admin";
  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-zinc-950">
      <header className="border-b border-zinc-200 bg-white px-8 py-6 dark:border-zinc-800 dark:bg-zinc-900">
        <h1 className="text-xl font-semibold dark:text-zinc-100">系统设置</h1>
        <p className="mt-1 text-sm text-zinc-500">外观与平台级默认配置</p>
      </header>
      <main className="mx-auto max-w-3xl space-y-5 p-8">
        <section className="space-y-3 rounded-2xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
          <h2 className="text-sm font-medium text-zinc-700 dark:text-zinc-300">外观</h2>
          <p className="text-xs text-zinc-500">主题偏好保存在本浏览器，跟随系统则随 OS 切换。</p>
          <ThemeToggle />
        </section>
        <AgentKeySection />
        {isAdmin ? (
          <DefaultModelsSection />
        ) : (
          <section className="space-y-2 rounded-2xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
            <h2 className="text-sm font-medium text-zinc-700 dark:text-zinc-300">默认模型</h2>
            <p className="text-xs text-zinc-500">由管理员配置（用户未自选模型时的全员兜底）。</p>
          </section>
        )}
        <ChangePasswordSection />
      </main>
    </div>
  );
}

function AgentKeySection() {
  const queryClient = useQueryClient();
  const [generated, setGenerated] = useState("");
  const [revealed, setRevealed] = useState("");
  const [guideOpen, setGuideOpen] = useState(false);
  const [copiedKey, setCopiedKey] = useState(false);
  const [copiedPrompt, setCopiedPrompt] = useState(false);
  const origin = typeof window !== "undefined" ? window.location.origin : "";

  const { data } = useQuery({
    queryKey: ["settings", "api-key"],
    queryFn: () => apiFetch<{ api_key: string | null }>("/api/settings/api-key"),
  });
  const existing = data?.api_key ?? null;

  const rotate = useMutation({
    mutationFn: () => apiFetch<{ api_key: string }>("/api/settings/api-key", { method: "POST" }),
    onSuccess: (r) => {
      setGenerated(r.api_key);
      void queryClient.invalidateQueries({ queryKey: ["settings", "api-key"] });
    },
  });
  const reveal = useMutation({
    mutationFn: () => apiFetch<{ api_key: string | null }>("/api/settings/api-key?reveal=true"),
    onSuccess: (r) => setRevealed(r.api_key ?? ""),
  });
  const revoke = useMutation({
    mutationFn: () => apiFetch("/api/settings/api-key", { method: "DELETE" }),
    onSuccess: () => {
      setGenerated("");
      setRevealed("");
      void queryClient.invalidateQueries({ queryKey: ["settings", "api-key"] });
    },
  });

  const shown = generated || revealed || existing;

  return (
    <section className="space-y-3 rounded-2xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
      <h2 className="flex items-center gap-1.5 text-sm font-semibold dark:text-zinc-200">
        <KeyRound className="h-4 w-4 text-zinc-400" /> Agent API Key
      </h2>
      <p className="text-xs leading-relaxed text-zinc-500">
        供外部 AI Agent（OpenClaw 等）直接向你的画布提交创作指令：
        <code className="mx-1 rounded bg-zinc-100 px-1 py-0.5 text-xs dark:bg-zinc-800">POST /api/canvas/&lt;id&gt;/agent</code>
        携带 <code className="rounded bg-zinc-100 px-1 py-0.5 text-xs dark:bg-zinc-800">X-API-Key</code> 头。
        任务完成后结果自动写入画布节点。
      </p>
      <button
        type="button"
        onClick={() => setGuideOpen((v) => !v)}
        className="flex w-full cursor-pointer items-center gap-1 rounded-lg px-1 py-1 text-xs font-medium text-zinc-600 hover:bg-zinc-50 dark:text-zinc-300 dark:hover:bg-zinc-800"
      >
        {guideOpen ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
        <Sparkles className="h-3.5 w-3.5 text-amber-600" /> Agent 接入指南（端点 + 一键系统指令）
      </button>
      {guideOpen && (
        <div className="space-y-3 rounded-xl border border-zinc-200 bg-zinc-50/60 p-3 text-xs dark:border-zinc-700 dark:bg-zinc-800/40">
          <div>
            <p className="font-medium text-zinc-700 dark:text-zinc-200">API 基址</p>
            <code className="mt-1 block break-all rounded bg-white px-2 py-1 font-mono text-[11px] text-zinc-600 ring-1 ring-zinc-200 dark:bg-zinc-900 dark:text-zinc-300 dark:ring-zinc-700">
              {origin}
            </code>
          </div>
          <div>
            <p className="font-medium text-zinc-700 dark:text-zinc-200">端点速览（Header 携带 X-API-Key）</p>
            <ul className="mt-1 space-y-0.5 font-mono text-[11px] text-zinc-600 dark:text-zinc-300">
              <li>GET /api/agent/v1/me — key 自检</li>
              <li>GET/POST /api/agent/v1/projects — 列/建画布</li>
              <li>GET /api/agent/v1/models?domain=image — 可用模型（含免费标记）</li>
              <li>POST /api/agent/v1/generate — 生成（返回 task_id）</li>
              <li>GET /api/agent/v1/tasks — 最近任务列表（跨会话记忆）</li>
              <li>GET /api/agent/v1/tasks/&#123;id&#125; — 轮询结果拿产物 URL（LLM 看 content）</li>
              <li>GET /api/agent/v1/projects/&#123;id&#125;/nodes — 画布节点读回（接力素材）</li>
            </ul>
            <p className="mt-1.5 text-zinc-500 dark:text-zinc-400">
              自检：
              <code className="rounded bg-white px-1 ring-1 ring-zinc-200 dark:bg-zinc-900 dark:ring-zinc-700">
                {`curl -H "X-API-Key: <key>" ${origin}/api/agent/v1/me`}
              </code>
            </p>
          </div>
          <div>
            <div className="flex items-center justify-between">
              <p className="font-medium text-zinc-700 dark:text-zinc-200">系统指令（粘贴给 Claude / GPT 等外部 Agent）</p>
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  copyText(agentSystemPrompt(origin)).then(() => {
                    setCopiedPrompt(true);
                    setTimeout(() => setCopiedPrompt(false), 1500);
                  })
                }
              >
                <Copy className="h-3.5 w-3.5" /> {copiedPrompt ? "已复制" : "复制系统指令"}
              </Button>
            </div>
            <pre className="mt-1 max-h-40 overflow-y-auto whitespace-pre-wrap rounded bg-white p-2 font-mono text-[11px] leading-relaxed text-zinc-600 ring-1 ring-zinc-200 dark:bg-zinc-900 dark:text-zinc-300 dark:ring-zinc-700">
              {agentSystemPrompt(origin)}
            </pre>
          </div>
          <p className="text-zinc-400 dark:text-zinc-500">常驻接入推荐装 Skill（教 Agent 能力面+标准工作流），两种获取方式：</p>
          <p className="mt-1">
            ① 仓库拷贝：
            <code className="rounded bg-white px-1 ring-1 ring-zinc-200 dark:bg-zinc-900 dark:ring-zinc-700">cp -r skills/creagen-canvas ~/.agents/skills/</code>
          </p>
          <p className="mt-1">
            ② 在线下载（无需 key）：
            <code className="break-all rounded bg-white px-1 ring-1 ring-zinc-200 dark:bg-zinc-900 dark:ring-zinc-700">{origin}/api/agent/v1/skill.md</code>
          </p>
          <p className="mt-1">完整 API 文档见仓库 docs/agent-api.md。</p>
        </div>
      )}
      {shown ? (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <code className="h-9 min-w-0 flex-1 truncate rounded-lg border border-zinc-200 bg-zinc-50 px-2.5 font-mono text-xs leading-9 text-zinc-700 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300">
              {shown}
            </code>
            {(generated || revealed) && (
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  copyText(generated || revealed).then(() => {
                    setCopiedKey(true);
                    setTimeout(() => setCopiedKey(false), 1500);
                  })
                }
              >
                <Copy className="h-3.5 w-3.5" /> {copiedKey ? "已复制" : "复制"}
              </Button>
            )}
            {!generated && existing && (
              <Button
                size="sm"
                variant="ghost"
                title={revealed ? "隐藏明文" : "显示完整 Key（登录态取回）"}
                onClick={() => (revealed ? setRevealed("") : reveal.mutate())}
              >
                {reveal.isPending ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : revealed ? (
                  <EyeOff className="h-3.5 w-3.5" />
                ) : (
                  <Eye className="h-3.5 w-3.5" />
                )}
                {revealed ? "隐藏" : "显示"}
              </Button>
            )}
          </div>
          {generated && (
            <p className="text-xs text-amber-600">
              完整 Key 仅显示这一次，请立即保存；离开页面后只显示脱敏版本。
            </p>
          )}
          <div className="flex gap-2">
            <Button size="sm" variant="outline" disabled={rotate.isPending} onClick={() => rotate.mutate()}>
              {rotate.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" />
              )}
              {existing ? "轮换（旧 Key 立即失效）" : "重新生成"}
            </Button>
            <Button size="sm" variant="ghost" disabled={revoke.isPending} onClick={() => revoke.mutate()}>
              <Trash2 className="h-3.5 w-3.5" /> 撤销
            </Button>
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          <p className="text-xs text-zinc-500">尚未生成。生成后即可让 Agent 通过 API 创作到画布。</p>
          <Button size="sm" disabled={rotate.isPending} onClick={() => rotate.mutate()}>
            {rotate.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <KeyRound className="h-4 w-4" />}
            生成 API Key
          </Button>
        </div>
      )}
    </section>
  );
}

function ChangePasswordSection() {
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [message, setMessage] = useState("");
  const [status, setStatus] = useState<"ok" | "err">("ok");

  const change = useMutation({
    mutationFn: () =>
      apiFetch("/api/auth/change-password", {
        method: "POST",
        json: { old_password: oldPassword, new_password: newPassword },
      }),
    onSuccess: () => {
      setMessage("密码已修改，下次登录请使用新密码");
      setStatus("ok");
      setOldPassword("");
      setNewPassword("");
      setConfirm("");
    },
    onError: (e: unknown) => {
      setMessage(e instanceof Error ? e.message : "修改失败");
      setStatus("err");
    },
  });

  const canSubmit = Boolean(oldPassword) && newPassword.length >= 8 && newPassword === confirm;

  return (
    <section className="space-y-3 rounded-2xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
      <h2 className="flex items-center gap-1.5 text-sm font-medium text-zinc-700 dark:text-zinc-300">
        <LockKeyhole className="h-4 w-4 text-zinc-400" /> 修改密码
      </h2>
      <div className="space-y-2">
        <input
          type="password"
          placeholder="当前密码"
          aria-label="当前密码"
          value={oldPassword}
          onChange={(e) => setOldPassword(e.target.value)}
          className="h-9 w-full rounded-lg border border-zinc-200 px-3 text-sm outline-none focus:border-zinc-400 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
        />
        <input
          type="password"
          placeholder="新密码（至少 8 位）"
          aria-label="新密码"
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
          className="h-9 w-full rounded-lg border border-zinc-200 px-3 text-sm outline-none focus:border-zinc-400 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
        />
        <input
          type="password"
          placeholder="确认新密码"
          aria-label="确认新密码"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          className="h-9 w-full rounded-lg border border-zinc-200 px-3 text-sm outline-none focus:border-zinc-400 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
        />
      </div>
      {message && <p className={`text-xs ${status === "ok" ? "text-emerald-600" : "text-red-600"}`}>{message}</p>}
      <Button size="sm" onClick={() => change.mutate()} disabled={!canSubmit || change.isPending}>
        {change.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <LockKeyhole className="h-4 w-4" />}
        修改密码
      </Button>
    </section>
  );
}

function DefaultModelsSection() {
  const queryClient = useQueryClient();
  const [picks, setPicks] = useState<DefaultModels>({});
  const [saved, setSaved] = useState(false);

  const defaultsQuery = useQuery({
    queryKey: ["settings", "default-models"],
    queryFn: () => apiFetch<{ defaults: DefaultModels }>("/api/settings/default-models"),
  });

  useEffect(() => {
    if (defaultsQuery.data) setPicks({ ...defaultsQuery.data.defaults });
  }, [defaultsQuery.data]);

  const channelsByDomain: Record<DomainKey, { channels: ChannelSummary[] }> = {
    image: useDomainChannels("image"),
    video: useDomainChannels("video"),
    llm: useDomainChannels("llm"),
    audio: useDomainChannels("audio"),
  };

  const save = useMutation({
    mutationFn: (next: DefaultModels) => apiFetch("/api/settings/default-models", { method: "PUT", json: next }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["settings", "default-models"] });
      void queryClient.invalidateQueries({ queryKey: ["settings", "platform-defaults"] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    },
  });

  return (
    <section
      className="space-y-3 rounded-2xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900"
      data-testid="default-models"
    >
      <h2 className="text-sm font-medium text-zinc-700 dark:text-zinc-300">默认模型（全员兜底）</h2>
      <p className="text-xs text-zinc-500">
        用户未自选渠道/模型时按此兜底（解析顺序：用户本地选择 → 平台默认 → 渠道目录）。
      </p>
      <div className="space-y-3">
        {DOMAINS.map(({ key, label }) => {
          const { channels } = channelsByDomain[key];
          const pick = picks[key];
          const channel = channels.find((c) => c.id === pick?.channel_id);
          return (
            <div key={key} className="flex flex-wrap items-center gap-2">
              <span className="w-12 shrink-0 text-xs font-medium text-zinc-600 dark:text-zinc-400">{label}</span>
              <select
                aria-label={`默认渠道-${label}`}
                value={pick?.channel_id ?? ""}
                onChange={(e) => {
                  const id = Number(e.target.value) || undefined;
                  const ch = channels.find((c) => c.id === id);
                  setPicks((p) => ({
                    ...p,
                    [key]: id ? { channel_id: id, model: ch?.models[0]?.model_id ?? "" } : undefined,
                  }));
                }}
                className="h-9 min-w-40 rounded-lg border border-zinc-200 bg-white px-2 text-sm dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
              >
                <option value="">（未设置）</option>
                {channels.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
              <select
                aria-label={`默认模型-${label}`}
                value={pick?.model ?? ""}
                onChange={(e) =>
                  setPicks((p) =>
                    p[key] ? { ...p, [key]: { ...p[key]!, model: e.target.value } } : p,
                  )
                }
                disabled={!channel}
                className="h-9 min-w-52 max-w-72 flex-1 rounded-lg border border-zinc-200 bg-white px-2 text-sm disabled:opacity-40 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
              >
                <option value="">{channel ? "（模型目录为空，测试连通后同步）" : "先选渠道"}</option>
                {(channel?.models ?? []).map((m) => (
                  <option key={m.model_id} value={m.model_id}>
                    {m.model_id}
                  </option>
                ))}
              </select>
            </div>
          );
        })}
        <VisionPick picks={picks} setPicks={setPicks} channelsByDomain={channelsByDomain} />
      </div>
      <div className="flex items-center gap-2">
        <Button size="sm" onClick={() => save.mutate(picks)} disabled={save.isPending}>
          {save.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
          保存默认模型
        </Button>
        {saved && <span className="text-xs text-emerald-600">已保存</span>}
        {save.isError && <span className="text-xs text-red-600">保存失败：{save.error.message}</span>}
      </div>
    </section>
  );
}

function VisionPick({
  picks,
  setPicks,
  channelsByDomain,
}: {
  picks: DefaultModels;
  setPicks: React.Dispatch<React.SetStateAction<DefaultModels>>;
  channelsByDomain: Record<string, { channels: ChannelSummary[] }>;
}) {
  const { channels } = channelsByDomain.llm;
  const pick = picks.vision;
  const channel = channels.find((c) => c.id === pick?.channel_id);
  const visionModels = (channel?.models ?? []).filter((m) => m.meta_json?.vision);

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="w-12 shrink-0 text-xs font-medium text-zinc-600 dark:text-zinc-400">视觉</span>
      <select
        aria-label="默认渠道-视觉"
        value={pick?.channel_id ?? ""}
        onChange={(e) => {
          const id = Number(e.target.value) || undefined;
          const ch = channels.find((c) => c.id === id);
          setPicks((p) => ({
            ...p,
            vision: id
              ? { channel_id: id, model: ch?.models.find((m) => m.meta_json?.vision)?.model_id ?? "" }
              : undefined,
          }));
        }}
        className="h-9 min-w-40 rounded-lg border border-zinc-200 bg-white px-2 text-sm dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
      >
        <option value="">（未设置）</option>
        {channels.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
          </option>
        ))}
      </select>
      <select
        aria-label="默认模型-视觉"
        value={pick?.model ?? ""}
        onChange={(e) =>
          setPicks((p) => (p.vision ? { ...p, vision: { ...p.vision!, model: e.target.value } } : p))
        }
        disabled={!channel || visionModels.length === 0}
        className="h-9 min-w-52 max-w-72 flex-1 rounded-lg border border-zinc-200 bg-white px-2 text-sm disabled:opacity-40 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
      >
        <option value="">
          {channel ? (visionModels.length === 0 ? "（该渠道无 vision 标记模型）" : "选择模型") : "先选渠道"}
        </option>
        {visionModels.map((m) => (
          <option key={m.model_id} value={m.model_id}>
            {m.model_id}
          </option>
        ))}
      </select>
      <span className="text-[10px] text-zinc-400">用于看图反推等图生文场景；模型可在渠道页勾选 vision 能力</span>
    </div>
  );
}

function useDomainChannels(domain: string): { channels: ChannelSummary[] } {
  const q = useQuery({
    queryKey: ["provider-channels", domain],
    queryFn: () => apiFetch<{ channels: ChannelSummary[] }>(`/api/providers/channels?domain=${domain}`),
  });
  return { channels: q.data?.channels ?? [] };
}
