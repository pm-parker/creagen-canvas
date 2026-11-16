import { useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { Copy, Hexagon, Loader2, Lock } from "lucide-react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
} from "@xyflow/react";
import type { Edge, Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { CanvasNode } from "@/pages/canvas/nodes/CanvasNode";
import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";
import { useAuthStore } from "@/stores/auth";

interface SharedCanvas {
  project: { id: number; title: string };
  author: string;
  nodes: {
    id: number;
    type: string;
    title: string | null;
    position: { x: number; y: number };
    metadata: Record<string, unknown>;
  }[];
  connections: { from_node_id: number; to_node_id: number }[];
}

interface ForkResult {
  project: { id: number };
}

const nodeTypes = { canvas: CanvasNode };

/** 只读分享页：未登录可看，登录后可一键复制到自己的画布。 */
export function SharePage() {
  const { token = "" } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const user = useAuthStore((s) => s.user);
  const isLoggedIn = !!user;
  const [forking, setForking] = useState(false);
  const [error, setError] = useState("");
  const { data, isLoading, isError } = useQuery({
    queryKey: ["shared-canvas", token],
    queryFn: () => apiFetch<SharedCanvas>(`/api/canvas/shared/${token}`),
    retry: false,
  });

  const { nodes, edges } = useMemo(() => {
    if (!data) return { nodes: [] as Node[], edges: [] as Edge[] };
    const idMap = new Map<number, string>();
    const flowNodes: Node[] = data.nodes.map((n) => {
      const clientId = String(n.metadata?.client_id ?? n.id);
      idMap.set(n.id, clientId);
      return {
        id: clientId,
        type: "canvas",
        position: n.position,
        data: {
          nodeType: n.type,
          title: n.title ?? "",
          prompt: typeof n.metadata?.prompt === "string" ? n.metadata.prompt : "",
          outputUrl:
            typeof n.metadata?.outputUrl === "string" ? n.metadata.outputUrl : undefined,
          durationSec:
            typeof n.metadata?.durationSec === "number" ? n.metadata.durationSec : undefined,
          resolution:
            typeof n.metadata?.resolution === "string" ? n.metadata.resolution : undefined,
        },
        draggable: false,
      } as Node;
    });
    const flowEdges: Edge[] = data.connections
      .filter((c) => idMap.has(c.from_node_id) && idMap.has(c.to_node_id))
      .map((c) => ({
        id: `e-${c.from_node_id}-${c.to_node_id}`,
        source: idMap.get(c.from_node_id) as string,
        target: idMap.get(c.to_node_id) as string,
      }));
    return { nodes: flowNodes, edges: flowEdges };
  }, [data]);

  async function fork() {
    if (forking) return;
    if (!isLoggedIn) {
      const next = `${location.pathname}${location.search}`;
      navigate(`/login?next=${encodeURIComponent(next)}`);
      return;
    }
    setForking(true);
    setError("");
    try {
      const res = await apiFetch<ForkResult>(`/api/canvas/shared/${token}/fork`, {
        method: "POST",
      });
      navigate(`/canvas/${res.project.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "复制失败，请稍后重试");
    } finally {
      setForking(false);
    }
  }

  return (
    <div className="flex h-screen flex-col bg-zinc-100">
      <header className="flex items-center gap-3 border-b border-zinc-200 bg-white px-5 py-3 shadow-sm">
        <Hexagon className="h-6 w-6 shrink-0 text-zinc-900" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-zinc-900">
            {data?.project.title ?? (isLoading ? "加载中…" : "分享的画布")}
          </p>
          <p className="text-xs text-zinc-500">
            {data ? `作者 ${data.author} · ${data.nodes.length} 个节点（只读）` : ""}
          </p>
        </div>
        <span className="hidden items-center gap-1 rounded-full bg-zinc-100 px-2.5 py-1 text-xs text-zinc-500 md:flex">
          <Lock className="h-3 w-3" /> 只读分享
        </span>
        <span
          title="依据《人工智能生成合成内容标识办法》标注"
          className="hidden items-center gap-1 rounded-full border border-zinc-200 px-2.5 py-1 text-xs text-zinc-500 md:flex"
        >
          AI 生成内容
        </span>
        <Button size="sm" onClick={fork} disabled={forking || isLoading || isError}>
          {forking ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Copy className="h-3.5 w-3.5" />
          )}
          {isLoggedIn ? "复制到我的画布" : "登录后复制"}
        </Button>
      </header>

      <a
        href="https://github.com/pm-parker/creagen-canvas"
        target="_blank"
        rel="noreferrer"
        className="fixed bottom-3 right-3 z-20 flex items-center gap-1.5 rounded-full border border-zinc-200 bg-white/90 px-3 py-1.5 text-xs text-zinc-500 shadow-sm backdrop-blur transition-colors hover:text-amber-700"
      >
        <span className="inline-block h-2.5 w-2.5 rotate-45 rounded-[2px] bg-amber-500" />
        开源自托管 · CreaGen Canvas
      </a>

      {error && <p className="bg-red-50 px-5 py-1.5 text-xs text-red-600">{error}</p>}

      <div className="min-h-0 flex-1">
        {isLoading ? (
          <div className="flex h-full items-center justify-center text-zinc-400">
            <Loader2 className="h-6 w-6 animate-spin" />
          </div>
        ) : isError ? (
          <div className="flex h-full flex-col items-center justify-center gap-3">
            <p className="text-sm text-zinc-500">分享链接不存在或已关闭</p>
            <Button variant="outline" size="sm" onClick={() => navigate("/")}>
              回到首页
            </Button>
          </div>
        ) : (
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable={true}
            panOnDrag={true}
            zoomOnScroll={true}
            minZoom={0.05}
            fitView
            proOptions={{ hideAttribution: true }}
          >
            <Background variant={BackgroundVariant.Dots} gap={24} className="bg-zinc-50" />
            <MiniMap />
            <Controls className="!bg-white" />
          </ReactFlow>
        )}
      </div>
    </div>
  );
}
