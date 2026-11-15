import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
import { ClipboardCopy, Copy, Plus, Search, Sparkles, Star } from "lucide-react";
import { useNavigate } from "react-router";
import { apiFetch } from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface PromptItem {
  id: number;
  title: string;
  content: string;
  category?: string;
}

export function PromptsPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);
  const [importUrl, setImportUrl] = useState("");
  const [importJson, setImportJson] = useState("");
  const [importResult, setImportResult] = useState("");
  const [form, setForm] = useState({ title: "", content: "" });
  const [copied, setCopied] = useState<number | null>(null);

  const prompts = useQuery({
    queryKey: ["prompts", search, category],
    queryFn: () =>
      apiFetch<{ prompts: PromptItem[] }>(
        `/api/prompts?limit=100${search ? `&search=${encodeURIComponent(search)}` : ""}`,
      ),
  });
  const allPrompts = prompts.data?.prompts ?? [];
  const categories = Array.from(new Set(allPrompts.map((x) => x.category ?? "").filter(Boolean)));
  const visible = category ? allPrompts.filter((x) => x.category === category) : allPrompts;

  const create = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      apiFetch("/api/prompts", { method: "POST", json: body }),
    onSuccess: () => {
      setCreating(false);
      setForm({ title: "", content: "" });
      qc.invalidateQueries({ queryKey: ["prompts"] });
    },
  });

  const importMut = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      apiFetch<{ created: number; skipped: number }>("/api/prompts/import", { method: "POST", json: body }),
    onSuccess: (res) => {
      setImportResult(`导入 ${res.created} 条，跳过重复 ${res.skipped} 条`);
      qc.invalidateQueries({ queryKey: ["prompts"] });
    },
    onError: (e) => setImportResult(`导入失败：${e.message}`),
  });

  const importUrlMut = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      apiFetch<{ created: number; skipped: number }>("/api/prompts/import-url", { method: "POST", json: body }),
    onSuccess: (res) => {
      setImportResult(`从 URL 导入 ${res.created} 条，跳过 ${res.skipped} 条`);
      qc.invalidateQueries({ queryKey: ["prompts"] });
    },
    onError: (e) => setImportResult(`导入失败：${e.message}`),
  });

  function doImport() {
    if (importUrl) {
      importUrlMut.mutate({ url: importUrl });
      return;
    }
    try {
      const parsed = JSON.parse(importJson);
      const items = Array.isArray(parsed) ? parsed : (parsed.prompts ?? parsed.data);
      if (!Array.isArray(items)) {
        setImportResult("JSON 格式不对：需为数组");
        return;
      }
      importMut.mutate({ items });
    } catch {
      setImportResult("粘贴的内容不是合法 JSON");
    }
  }

  const favorite = useMutation({
    mutationFn: (id: number) => apiFetch(`/api/prompts/${id}/favorite`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["prompts"] }),
  });

  function copy(p: PromptItem) {
    copyText(p.content).then(() => setCopied(p.id));
    setCopied(p.id);
    setTimeout(() => setCopied(null), 1200);
  }

  return (
    <div className="min-h-screen bg-zinc-50">
      <header className="border-b border-zinc-200 bg-white px-8 py-6">
        <h1 className="text-xl font-semibold">提示词库</h1>
        <p className="mt-1 text-sm text-zinc-500">收藏优质提示词，一键复制到生成页或画布节点</p>
      </header>
      <main className="mx-auto max-w-4xl space-y-4 p-8">
        <div className="flex items-center gap-3">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-2.5 h-4 w-4 text-zinc-400" />
            <Input
              placeholder="搜索标题或内容…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-9"
            />
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => setImporting((v) => !v)}>
              批量导入
            </Button>
            <Button onClick={() => setCreating(true)}>
              <Plus className="h-4 w-4" /> 新建提示词
            </Button>
          </div>
        </div>
        {importing && (
          <div className="space-y-3 rounded-2xl border border-zinc-200 bg-white p-5">
            <h3 className="text-sm font-medium">批量导入</h3>
            <div className="space-y-1.5">
              <Label htmlFor="imp-url">远程 JSON 地址（可选）</Label>
              <Input
                id="imp-url"
                placeholder="https://example.com/prompts.json"
                value={importUrl}
                onChange={(e) => setImportUrl(e.target.value)}
              />
              <p className="text-xs text-zinc-400">
                每条支持 {"{title?, content 或 prompt, category?}"}。填了 URL 优先从 URL 导入。
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="imp-json">或直接粘贴 JSON</Label>
              <textarea
                id="imp-json"
                rows={6}
                placeholder='[{"title":"示例","content":"提示词内容","category":"人像"}]'
                value={importJson}
                onChange={(e) => setImportJson(e.target.value)}
                className="w-full rounded-xl border border-zinc-200 p-3 font-mono text-xs outline-none focus:border-zinc-400"
              />
            </div>
            {importResult && <p className="text-xs text-zinc-600">{importResult}</p>}
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setImporting(false)}>关闭</Button>
              <Button onClick={doImport}>开始导入</Button>
            </div>
          </div>
        )}
        {categories.length > 0 && (
          <div className="flex flex-wrap gap-1">
            <button
              onClick={() => setCategory("")}
              className={"h-7 rounded-full px-3 text-xs cursor-pointer " + (category === "" ? "bg-zinc-900 text-white" : "bg-zinc-100 text-zinc-600")}
            >
              全部
            </button>
            {categories.map((c) => (
              <button
                key={c}
                onClick={() => setCategory(c)}
                className={"h-7 rounded-full px-3 text-xs cursor-pointer " + (category === c ? "bg-zinc-900 text-white" : "bg-zinc-100 text-zinc-600")}
              >
                {/^(image|video|llm|audio):/.test(c)
                  ? `${{ image: "图片", video: "视频", llm: "文案", audio: "音频" }[c.split(":")[0]] ?? ""}·${c.split(":")[1]}`
                  : c}
              </button>
            ))}
          </div>
        )}

        {creating && (
          <form
            className="space-y-3 rounded-2xl border border-zinc-200 bg-white p-5"
            onSubmit={(e) => {
              e.preventDefault();
              create.mutate(form);
            }}
          >
            <div className="space-y-1.5">
              <Label htmlFor="p-title">标题（可选，默认取内容前 30 字）</Label>
              <Input id="p-title" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="p-content">提示词内容</Label>
              <textarea
                id="p-content"
                required
                rows={4}
                value={form.content}
                onChange={(e) => setForm({ ...form, content: e.target.value })}
                className="w-full rounded-xl border border-zinc-200 p-3 text-sm outline-none focus:border-zinc-400"
              />
            </div>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => setCreating(false)}>取消</Button>
              <Button type="submit">保存</Button>
            </div>
          </form>
        )}

        {visible.length > 0 ? (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {visible.map((p) => (
              <div key={p.id} className="h-fit rounded-xl border border-zinc-200 bg-white p-4">
                <div className="flex items-center justify-between gap-2">
                  <p className="min-w-0 flex-1 truncate font-medium text-zinc-900" title={p.title}>
                    {p.title}
                    {p.category && (
                      <span className="ml-2 rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] font-normal text-zinc-500">
                        {(p.category.split(":")[1] ?? p.category).slice(0, 6)}
                      </span>
                    )}
                  </p>
                  <div className="flex items-center gap-1">
                    {(p.category ?? "").startsWith("llm") ? (
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label="复制文案提示词"
                        title="复制到剪贴板（文案类提示词请粘贴到 LLM 渠道使用）"
                        onClick={() => copy(p)}
                      >
                        <ClipboardCopy className="h-4 w-4 text-indigo-500" />
                      </Button>
                    ) : (
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label="去生成"
                        title="用这条提示词生成"
                        onClick={() =>
                          navigate(
                            `/generate/${(p.category ?? "").startsWith("video") ? "video" : "image"}?prompt=${encodeURIComponent(p.content)}`,
                          )
                        }
                      >
                        <Sparkles className="h-4 w-4 text-indigo-500" />
                      </Button>
                    )}
                    <Button variant="ghost" size="icon" aria-label="复制" onClick={() => copy(p)}>
                      <Copy className="h-4 w-4 text-zinc-500" />
                    </Button>
                    <Button variant="ghost" size="icon" aria-label="收藏" onClick={() => favorite.mutate(p.id)}>
                      <Star className="h-4 w-4 text-amber-400" />
                    </Button>
                  </div>
                </div>
                <p className="mt-1 line-clamp-2 text-sm text-zinc-600">{p.content}</p>
                {copied === p.id && <p className="mt-1 text-xs text-emerald-600">已复制到剪贴板</p>}
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-zinc-400">
            暂无提示词。<Link to="/generate/image" className="underline">去生图页试试 →</Link>
          </p>
        )}
      </main>
    </div>
  );
}
