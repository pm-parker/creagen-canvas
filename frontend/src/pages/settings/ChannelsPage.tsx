import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Plus, Trash2, PlugZap, Pencil } from "lucide-react";

interface Channel {
  id: number;
  domain: string;
  name: string;
  base_url: string;
  key_masked: string;
  enabled: boolean;
  sort_order: number;
  user_id_owned?: boolean;
  capabilities?: string[];
  models: Array<{ model_id: string; display_name: string; enabled: boolean }>;
}

const DOMAIN_LABEL: Record<string, string> = {
  image: "图片",
  video: "视频",
  llm: "LLM",
  audio: "音频",
};

export function ChannelsPage() {
  const qc = useQueryClient();
  const channels = useQuery({
    queryKey: ["providers", "channels"],
    queryFn: () => apiFetch<{ channels: Channel[] }>("/api/providers/channels"),
  });
  const [creating, setCreating] = useState(false);
  const [testResult, setTestResult] = useState<Record<number, string>>({});
  const [editingId, setEditingId] = useState<number | null>(null);

  const testChannel = useMutation({
    mutationFn: (id: number) =>
      apiFetch<{ ok: boolean; latency_ms?: number; code?: string; message?: string }>(
        `/api/providers/channels/${id}/test`,
        { method: "POST" },
      ),
    onSuccess: (res, id) => {
      setTestResult((m) => ({
        ...m,
        [id]: res.ok ? `✓ 连通 ${res.latency_ms}ms` : `✗ ${res.code}: ${res.message ?? ""}`,
      }));
    },
    onError: (err, id) => {
      setTestResult((m) => ({ ...m, [id]: `✗ 测试失败：${String(err).slice(0, 120)}` }));
    },
  });

  const updateChannel = useMutation({
    mutationFn: ({
      id,
      name,
      base_url,
      key,
      capabilities,
    }: {
      id: number;
      name: string;
      base_url: string;
      key: string;
      capabilities: string[];
    }) =>
      apiFetch(`/api/providers/channels/${id}`, {
        method: "PATCH",
        json: { name, base_url, ...(key ? { key } : {}), capabilities },
      }),
    onSuccess: (_res, v) => {
      qc.invalidateQueries({ queryKey: ["providers", "channels"] });
      setEditingId(null);
      setTestResult((m) => ({ ...m, [v.id]: "" }));
    },
  });

  const removeChannel = useMutation({
    mutationFn: (id: number) => apiFetch<void>(`/api/providers/channels/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["providers", "channels"] }),
  });

  const createChannel = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      apiFetch("/api/providers/channels", { method: "POST", json: body }),
    onSuccess: () => {
      setCreating(false);
      qc.invalidateQueries({ queryKey: ["providers", "channels"] });
    },
  });

  return (
    <div className="min-h-screen bg-zinc-50">
      <header className="flex items-center justify-between border-b border-zinc-200 bg-white px-6 py-4">
        <h1 className="text-lg font-semibold">模型渠道</h1>
        <Button onClick={() => setCreating(true)}>
          <Plus className="h-4 w-4" /> 新建渠道
        </Button>
      </header>
      <main className="mx-auto max-w-4xl space-y-3 p-6">
        {channels.isLoading && <p className="text-sm text-zinc-500">加载中…</p>}
        {(channels.data?.channels ?? []).map((ch) => (
          <div key={ch.id} className="rounded-xl border border-zinc-200 bg-white p-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="font-medium text-zinc-900">
                  <span className="mr-2 rounded bg-zinc-900 px-1.5 py-0.5 text-xs text-white">
                    {DOMAIN_LABEL[ch.domain] ?? ch.domain}
                  </span>
                  {!ch.user_id_owned && (
                    <span className="mr-1 rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] text-indigo-600">
                      系统
                    </span>
                  )}
                  {ch.name}
                  {!ch.enabled && <span className="ml-2 text-xs text-zinc-400">已停用</span>}
                </p>
                <p className="mt-1 text-xs text-zinc-500">
                  {ch.base_url} · <code>{ch.key_masked}</code> · {ch.models.length} 个模型
              {ch.capabilities?.includes("edit") && (
                <span className="ml-2 rounded bg-indigo-50 px-1.5 py-0.5 text-indigo-600">可编辑</span>
              )}
                </p>
              </div>
              <div className="flex items-center gap-2">
                {testResult[ch.id] && (
                  <span
                    className={`max-w-52 truncate text-xs ${
                      testResult[ch.id].startsWith("✓") ? "text-emerald-600" : "text-red-500"
                    }`}
                    title={testResult[ch.id]}
                  >
                    {testResult[ch.id]}
                  </span>
                )}
                <Button variant="outline" size="sm" onClick={() => testChannel.mutate(ch.id)}>
                  <PlugZap className="h-3.5 w-3.5" /> 测试连通
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="编辑渠道"
                  onClick={() => setEditingId(editingId === ch.id ? null : ch.id)}
                >
                  <Pencil className="h-4 w-4 text-zinc-500" />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="删除渠道"
                  onClick={() => removeChannel.mutate(ch.id)}
                >
                  <Trash2 className="h-4 w-4 text-zinc-500" />
                </Button>
              </div>
            </div>
            {editingId === ch.id && (
              <form
                className="mt-3 space-y-2 border-t border-zinc-100 pt-3"
                onSubmit={(e) => {
                  e.preventDefault();
                  const fd = new FormData(e.currentTarget);
                  const editCap = fd.get("edit_cap") === "on";
                  updateChannel.mutate({
                    id: ch.id,
                    name: String(fd.get("name") ?? "").trim(),
                    base_url: String(fd.get("base_url") ?? "").trim(),
                    key: String(fd.get("key") ?? "").trim(),
                    capabilities: editCap ? ["edit"] : [],
                  });
                }}
              >
                <div className="flex gap-2">
                  <Input name="name" defaultValue={ch.name} className="h-8 flex-1 text-xs" aria-label="渠道名称" />
                  <Input name="base_url" defaultValue={ch.base_url} className="h-8 flex-[2] text-xs" aria-label="Base URL" />
                </div>
                <div className="flex gap-2">
                  <Input
                    name="key"
                    placeholder="留空则不修改 API Key"
                    className="h-8 flex-1 text-xs"
                    aria-label="新 API Key"
                    type="password"
                    autoComplete="off"
                  />
                  <label className="flex cursor-pointer items-center gap-1.5 text-xs text-zinc-600">
                    <input
                      type="checkbox"
                      name="edit_cap"
                      defaultChecked={ch.capabilities?.includes("edit")}
                      className="h-3.5 w-3.5"
                    />
                    支持图像编辑
                  </label>
                  <Button type="submit" size="sm" disabled={updateChannel.isPending}>
                    保存
                  </Button>
                </div>
              </form>
            )}
          </div>
        ))}
        {!channels.isLoading && (channels.data?.channels ?? []).length === 0 && (
          <p className="text-sm text-zinc-500">
            还没有渠道。新建一个 OpenAI 兼容渠道，填入 Base URL 与 API Key 即可开始生成。
          </p>
        )}
      </main>
      {creating && (
        <ChannelDialog
          onClose={() => setCreating(false)}
          onSubmit={(body) => createChannel.mutate(body)}
        />
      )}
    </div>
  );
}

export function ChannelDialog({
  onClose,
  onSubmit,
}: {
  onClose: () => void;
  onSubmit: (body: Record<string, unknown>) => void;
}) {
  const [domain, setDomain] = useState("llm");
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [key, setKey] = useState("");
  const [editCap, setEditCap] = useState(false);

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/40">
      <form
        className="w-full max-w-md space-y-4 rounded-2xl bg-white p-6 shadow-xl"
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit({
            domain,
            name,
            base_url: baseUrl,
            key,
            capabilities: editCap ? ["edit"] : [],
          });
        }}
      >
        <h2 className="text-lg font-semibold">新建渠道</h2>
        <div className="flex flex-wrap gap-1.5">
          {[
            { label: "SiliconFlow", url: "https://api.siliconflow.cn/v1", domain: "image" },
            { label: "Minimax 视频", url: "https://api.minimaxi.com/v1", domain: "video" },
            { label: "OpenAI", url: "https://api.openai.com/v1", domain: "llm" },
          ].map((tpl) => (
            <button
              key={tpl.label}
              type="button"
              onClick={() => {
                setDomain(tpl.domain);
                setBaseUrl(tpl.url);
              }}
              className="rounded-full bg-zinc-100 px-2.5 py-1 text-xs text-zinc-600 hover:bg-zinc-200 cursor-pointer"
            >
              {tpl.label}
            </button>
          ))}
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="ch-domain">渠道域</Label>
          <select
            id="ch-domain"
            value={domain}
            onChange={(e) => setDomain(e.target.value)}
            className="h-9 w-full rounded-lg border border-zinc-300 bg-white px-2 text-sm"
          >
            <option value="llm">LLM</option>
            <option value="image">图片</option>
            <option value="video">视频</option>
            <option value="audio">音频</option>
          </select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="ch-name">名称</Label>
          <Input id="ch-name" value={name} onChange={(e) => setName(e.target.value)} required />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="ch-url">Base URL</Label>
          <Input
            id="ch-url"
            placeholder="https://api.example.com/v1"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            required
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="ch-key">API Key</Label>
          <Input
            id="ch-key"
            type="password"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            required
          />
          <p className="text-xs text-zinc-400">服务端 AES 加密保存，任何页面不回显明文。</p>
        </div>
        {domain === "image" && (
          <label className="flex cursor-pointer items-center gap-2 text-sm text-zinc-700">
            <input
              type="checkbox"
              checked={editCap}
              onChange={(e) => setEditCap(e.target.checked)}
              className="h-4 w-4"
              aria-label="支持图像编辑"
            />
            支持图像编辑（/images/edits 局部重绘）
          </label>
        )}
        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button type="submit">保存</Button>
        </div>
      </form>
    </div>
  );
}
