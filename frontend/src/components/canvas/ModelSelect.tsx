import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { apiFetch } from "@/lib/api";
import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface ChannelMeta {
  id: number;
  domain: string;
  name: string;
  enabled: boolean;
  models: Array<{ model_id: string; display_name: string; enabled: boolean }>;
}

interface ModelChoiceState {
  choice: Record<string, { channelId: number; model: string }>;
  setChoice: (domain: string, v: { channelId: number; model: string }) => void;
}

interface ModelChoiceStore extends ModelChoiceState {}

export const useModelChoice = create<ModelChoiceStore>()(
  persist(
    (set, get) => ({
      choice: {},
      setChoice: (domain: string, v: { channelId: number; model: string }) =>
        set({ choice: { ...get().choice, [domain]: v } }),
    }),
    { name: "cc-model-choice" },
  ),
);

export function useDomainChannels(domain: string) {
  const query = useQuery({
    queryKey: ["providers", "channels"],
    queryFn: () => apiFetch<{ channels: ChannelMeta[] }>("/api/providers/channels"),
  });
  const channels = useMemo(
    () => (query.data?.channels ?? []).filter((c) => c.domain === domain && c.enabled),
    [query.data, domain],
  );
  return { channels, isLoading: query.isLoading };
}

/** 域级渠道+模型级联选择（选择记忆到 localStorage）。 */
export function ModelSelect({ domain }: { domain: string }) {
  const { channels, isLoading } = useDomainChannels(domain);
  const choice = useModelChoice((s) => s.choice)[domain];
  const setChoice = useModelChoice((s) => s.setChoice);

  if (isLoading) return <span className="text-xs text-zinc-400">…</span>;
  if (channels.length === 0) {
    return (
      <Link to="/settings/channels" className="text-xs text-zinc-500 underline">
        尚未配置渠道，去设置
      </Link>
    );
  }
  const channel = channels.find((c) => c.id === choice?.channelId) ?? channels[0];
  const models = channel.models
    .filter((m) => m.enabled)
    .slice()
    .sort((a, b) => {
      // 免费图模型置顶，其余按名排序
      const free = (x: string) => (x.includes("Kolors") ? 0 : 1);
      return free(a.model_id) - free(b.model_id) || a.model_id.localeCompare(b.model_id);
    });
  const model = choice?.model ?? models[0]?.model_id ?? "";

  return (
    <div className="flex gap-1">
      <select
        aria-label={`渠道选择-${domain}`}
        value={channel.id}
        onChange={(e) => {
          const next = channels.find((c) => c.id === Number(e.target.value));
          if (next) setChoice(domain, { channelId: next.id, model: next.models[0]?.model_id ?? "" });
        }}
        className="h-7 rounded-md border border-zinc-200 bg-white px-1 text-xs"
      >
        {channels.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
          </option>
        ))}
      </select>
      <select
        aria-label={`模型选择-${domain}`}
        value={model}
        onChange={(e) => setChoice(domain, { channelId: channel.id, model: e.target.value })}
        className="h-7 max-w-36 rounded-md border border-zinc-200 bg-white px-1 text-xs"
      >
        {models.length === 0 && <option value="">默认模型（未同步目录，点渠道卡「测试连通」拉取）</option>}
        {models.map((m) => (
          <option key={m.model_id} value={m.model_id}>
            {m.model_id.includes("Kolors") ? "Kolors（免费）" : m.display_name}
          </option>
        ))}
      </select>
    </div>
  );
}
