import { useMemo, useState } from "react";
import { useReactFlow } from "@xyflow/react";
import { Search } from "lucide-react";
import { useCanvasStore } from "@/stores/canvas";

/** 节点搜索：按标题/提示词过滤，点击定位并选中。 */
export function NodeSearch() {
  const [query, setQuery] = useState("");
  const nodes = useCanvasStore((s) => s.nodes);
  const { setCenter } = useReactFlow();

  const results = useMemo(() => {
    if (query.trim().length < 1) return [];
    const q = query.trim().toLowerCase();
    return nodes.filter(
      (n) =>
        (n.data.title ?? "").toLowerCase().includes(q) ||
        (n.data.prompt ?? "").toLowerCase().includes(q),
    );
  }, [query, nodes]);

  function focusNode(id: string) {
    const n = nodes.find((x) => x.id === id);
    if (!n) return;
    setCenter(n.position.x + 120, n.position.y + 60, { zoom: 1.1, duration: 400 });
    useCanvasStore.setState({
      nodes: nodes.map((x) => ({ ...x, selected: x.id === id })),
    });
  }

  return (
    <div className="relative">
      <div className="flex items-center gap-1 rounded-full bg-white/95 px-2.5 py-1 shadow-sm">
        <Search className="h-3.5 w-3.5 text-zinc-400" />
        <input
          aria-label="搜索节点"
          placeholder="搜索节点…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="w-28 bg-transparent text-xs outline-none"
        />
      </div>
      {query.trim() && results.length > 0 && (
        <div className="absolute left-0 top-9 z-20 w-56 rounded-xl border border-zinc-200 bg-white p-1 shadow-lg">
          {results.map((n) => {
            const d = n.data as { title?: string; prompt?: string; nodeType?: string };
            return (
              <button
                key={n.id}
                onClick={() => focusNode(n.id)}
                className="flex w-full flex-col rounded-lg px-2 py-1.5 text-left hover:bg-zinc-100 cursor-pointer"
              >
                <span className="text-xs font-medium text-zinc-800">{d.title || d.nodeType}</span>
                <span className="truncate text-[10px] text-zinc-400">{d.prompt}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
