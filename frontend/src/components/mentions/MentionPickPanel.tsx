/** 首页自由创作——@ 引用面板：素材/角色/提示词/模型四个 tab。 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Image as ImageIcon,
  Video,
  Music,
  BookMarked,
  Bot,
  User,
  Sparkles,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import type { MentionOption, MentionType } from "./MentionTextarea";

const TABS: { key: MentionType; label: string; icon: typeof ImageIcon }[] = [
  { key: "asset", label: "素材", icon: ImageIcon },
  { key: "character", label: "角色", icon: User },
  { key: "prompt", label: "提示词", icon: BookMarked },
  { key: "model", label: "模型", icon: Bot },
];

interface TaskMeta { id: number; type: string; status: string; prompt: string; output_url?: string | null }
interface CharacterMeta { id: number; name: string; description: string }
interface PromptMeta { id: number; title?: string | null; content: string }
interface ChannelMeta { id: number; domain: string; enabled: boolean; models: { model_id: string; display_name?: string | null; enabled: boolean }[] }

function Empty({ text }: { text: string }) {
  return <p className="px-2 py-6 text-center text-xs text-zinc-400">{text}</p>;
}

interface PickPanelProps {
  onPick: (option: MentionOption) => void;
  picked: string[];
}

export function MentionPickPanel({ onPick, picked }: PickPanelProps) {
  const [tab, setTab] = useState<MentionType>("asset");
  const [assetKind, setAssetKind] = useState("all");
  const [search, setSearch] = useState("");

  const tasks = useQuery({
    queryKey: ["agent-mention", "assets"],
    queryFn: () => apiFetch<{ tasks: TaskMeta[] }>("/api/tasks?status=completed&limit=200"),
    staleTime: 30000,
  });
  const characters = useQuery({
    queryKey: ["agent-mention", "characters"],
    queryFn: () => apiFetch<{ characters: CharacterMeta[] }>("/api/characters"),
    staleTime: 30000,
  });
  const prompts = useQuery({
    queryKey: ["agent-mention", "prompts"],
    queryFn: () => apiFetch<{ prompts: PromptMeta[] }>("/api/prompts?limit=100"),
    staleTime: 30000,
  });
  const channels = useQuery({
    queryKey: ["providers", "channels"],
    queryFn: () => apiFetch<{ channels: ChannelMeta[] }>("/api/providers/channels"),
    staleTime: 30000,
    retry: false,
  });

  const assets = useMemo(() => {
    const withOutput = (tasks.data?.tasks ?? []).filter((t) => t.output_url);
    const byKind = assetKind === "all" ? withOutput : withOutput.filter((t) => t.type === assetKind);
    return (search ? byKind.filter((t) => t.prompt.includes(search)) : byKind).slice(0, 30);
  }, [tasks.data, assetKind, search]);

  const models = useMemo(() => {
    const out: { channelId: number; modelId: string; label: string; domain: string }[] = [];
    for (const ch of channels.data?.channels ?? []) {
      if (!ch.enabled) continue;
      for (const m of ch.models.filter((m) => m.enabled).slice(0, 4)) {
        out.push({
          channelId: ch.id,
          modelId: m.model_id,
          label: m.display_name || m.model_id,
          domain: ch.domain,
        });
      }
    }
    return out.slice(0, 12);
  }, [channels.data]);

  const isPicked = (label: string) => picked.includes(label);

  return (
    <div className="absolute top-full left-0 right-0 z-30 mt-2 rounded-xl border border-zinc-200 bg-white p-3 shadow-xl sm:right-auto sm:w-[560px] sm:max-w-[90vw]">
      <div className="mb-2 flex items-center gap-1 border-b border-zinc-100 pb-2">
        {TABS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            className={`flex items-center gap-1 rounded-lg px-2.5 py-1 text-xs transition-colors ${tab === key ? "bg-indigo-50 text-indigo-600" : "text-zinc-500 hover:bg-zinc-50"}`}
          >
            <Icon className="h-3.5 w-3.5" /> {label}
          </button>
        ))}
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="搜索…"
          className="ml-auto w-32 rounded-md border border-zinc-200 px-2 py-1 text-xs outline-none focus:border-indigo-300"
        />
      </div>
      <div className="max-h-64 space-y-1 overflow-y-auto">
        {tab === "asset" && (
          <>
            <div className="flex gap-1 pb-1">
              {["all", "image", "video", "audio"].map((k) => (
                <button
                  key={k}
                  type="button"
                  onClick={() => setAssetKind(k)}
                  className={`rounded-full px-2 py-0.5 text-[11px] ${assetKind === k ? "bg-zinc-900 text-white" : "bg-zinc-100 text-zinc-600"}`}
                >
                  {k === "all" ? "全部" : k === "image" ? "图片" : k === "video" ? "视频" : "音频"}
                </button>
              ))}
            </div>
            {assets.map((t) => {
              const label = `@素材:${t.prompt.slice(0, 12) || t.id}`;
              return (
                <button
                  key={t.id}
                  type="button"
                  disabled={isPicked(label)}
                  onClick={() => onPick({ type: "asset", task_id: t.id, label })}
                  className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs hover:bg-indigo-50 disabled:opacity-40"
                >
                  {t.type === "video" ? (
                    <Video className="h-3.5 w-3.5 text-indigo-400" />
                  ) : t.type === "audio" ? (
                    <Music className="h-3.5 w-3.5 text-emerald-400" />
                  ) : (
                    <ImageIcon className="h-3.5 w-3.5 text-amber-400" />
                  )}
                  <span className="truncate text-zinc-700">{t.prompt.slice(0, 40) || `任务 ${t.id}`}</span>
                  <span className="ml-auto shrink-0 text-[10px] text-zinc-300">{t.type}</span>
                </button>
              );
            })}
            {assets.length === 0 && <Empty text="暂无已完成素材" />}
          </>
        )}
        {tab === "character" &&
          (characters.data?.characters ?? []).map((c) => {
            const label = `@角色:${c.name}`;
            return (
              <button
                key={c.id}
                type="button"
                disabled={isPicked(label)}
                onClick={() => onPick({ type: "character", id: c.id, label })}
                className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs hover:bg-indigo-50 disabled:opacity-40"
              >
                <User className="h-3.5 w-3.5 text-rose-400" />
                <span className="font-medium text-zinc-700">{c.name}</span>
                <span className="truncate text-zinc-400">{c.description.slice(0, 36)}</span>
              </button>
            );
          })}
        {tab === "prompt" &&
          (prompts.data?.prompts ?? []).slice(0, 30).map((p) => {
            const label = `@提示词:${(p.title ?? p.content).slice(0, 12)}`;
            return (
              <button
                key={p.id}
                type="button"
                disabled={isPicked(label)}
                onClick={() => onPick({ type: "prompt", id: p.id, label })}
                className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs hover:bg-indigo-50 disabled:opacity-40"
              >
                <BookMarked className="h-3.5 w-3.5 text-amber-400" />
                <span className="truncate text-zinc-700">{(p.title ?? p.content).slice(0, 40)}</span>
              </button>
            );
          })}
        {tab === "model" &&
          models.map((m) => {
            const label = `@模型:${m.label}`;
            return (
              <button
                key={`${m.channelId}:${m.modelId}`}
                type="button"
                disabled={isPicked(label)}
                onClick={() => onPick({ type: "model", channel_id: m.channelId, model: m.modelId, label })}
                className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs hover:bg-indigo-50 disabled:opacity-40"
              >
                <Bot className="h-3.5 w-3.5 text-indigo-400" />
                <span className="text-zinc-700">{m.label}</span>
                <span className="ml-auto text-[10px] text-zinc-300">{m.domain}</span>
              </button>
            );
          })}
        {tab === "model" && models.length === 0 && <Empty text="暂无可用模型渠道" />}
      </div>
      <p className="mt-2 flex items-center gap-1 border-t border-zinc-100 pt-2 text-[10px] text-zinc-400">
        <Sparkles className="h-3 w-3" /> 素材作参考图/首帧 · 角色保持外观一致 · 模型 pin 本次生成
      </p>
    </div>
  );
}
