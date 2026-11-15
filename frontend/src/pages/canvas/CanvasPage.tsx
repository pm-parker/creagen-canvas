import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  Panel,
  ReactFlow,
} from "@xyflow/react";
import { Redo2, Undo2 } from "lucide-react";
import type { ReactNode } from "react";
import { apiFetch } from "@/lib/api";
import { toBackendViewport } from "@/lib/viewport";
import { useCanvasStore } from "@/stores/canvas";
import { CanvasNode } from "./nodes/CanvasNode";
import { AddNodeToolbar } from "./AddNodeToolbar";
import { PromptPanel } from "./PromptPanel";
import { SaveIndicator } from "@/components/canvas/SaveIndicator";
import { SnapshotDrawer } from "./SnapshotDrawer";
import { BatchTablePanel } from "./BatchTablePanel";
import { NodeSearch } from "@/components/canvas/NodeSearch";
import { buildExport, parseImport } from "@/lib/canvasIO";
import { usePanel } from "@/stores/panel";
import { AdjustDialog } from "./nodes/AdjustDialog";

const nodeTypes = { canvas: CanvasNode };

/** 画布工具栏统一按钮样式（icon+文字，禁用态降透明度）。 */
function ToolbarBtn({
  label,
  onClick,
  disabled,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  children: ReactNode;
}) {
  return (
    <button
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className={`flex items-center gap-1 rounded-full px-2.5 py-1 text-xs text-zinc-600 hover:bg-zinc-100 ${
        disabled ? "cursor-not-allowed opacity-40" : "cursor-pointer"
      }`}
    >
      {children}
    </button>
  );
}

let viewportTimer: ReturnType<typeof setTimeout> | null = null;

export function CanvasPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const [batchOpen, setBatchOpen] = useState(false);
  const [batchAdjustOpen, setBatchAdjustOpen] = useState(false);
  const [projectTitle, setProjectTitle] = useState("");
  const [isLoadingProject, setIsLoadingProject] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const { nodes, edges, viewport, onNodesChange, onEdgesChange, onConnect, loadFromApi } =
    useCanvasStore();
  // P1-4 批量调整入口：框选 ≥2 个带产物图片节点时出现（选择计算放 selector 外，AGENTS #185）
  const selectedImageNodes = useMemo(
    () =>
      nodes.filter(
        (n) => n.selected && n.data.nodeType === "image" && Boolean(n.data.outputUrl),
      ),
    [nodes],
  );
  const canUndo = useCanvasStore((s) => s.canUndo);
  const canRedo = useCanvasStore((s) => s.canRedo);
  const openFor = usePanel((s) => s.openFor);

  useEffect(() => {
    if (!projectId) return;
    useCanvasStore.getState().reset();
    apiFetch<{ project: { title?: string } }>(`/api/canvas/projects/${projectId}`)
      .then((resp) => {
        setProjectTitle(resp.project?.title ?? "");
        loadFromApi(resp);
        setIsLoadingProject(false);
      })
      .catch(() => {
        setLoadFailed(true);
        setIsLoadingProject(false);
      });
  }, [projectId, loadFromApi]);

  function handleExport() {
    const store = useCanvasStore.getState();
    const data = buildExport(
      projectTitle || (store.nodes.length > 0 ? "画布" : "空画布"),
      store.viewport,
      store.nodes,
      store.edges,
    );
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${projectTitle || "canvas"}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  function handleMoveEnd(_event: MouseEvent | TouchEvent | null, vp: { x: number; y: number; zoom: number }) {
    useCanvasStore.getState().setViewport(vp);
    if (viewportTimer) clearTimeout(viewportTimer);
    viewportTimer = setTimeout(() => {
      apiFetch(`/api/canvas/projects/${projectId}`, {
        method: "PUT",
        json: { viewport: JSON.stringify(toBackendViewport(vp)) },
      }).catch(() => undefined);
    }, 800);
  }

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement;
      if (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable) return;
      if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== "z") return;
      e.preventDefault();
      const store = useCanvasStore.getState();
      if (e.shiftKey) store.redo();
      else store.undo();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (loadFailed) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-zinc-50">
        <p className="text-sm text-zinc-600">画布不存在，或已被删除</p>
        <a
          href="/projects"
          className="rounded-lg bg-zinc-900 px-4 py-2 text-sm text-white hover:bg-zinc-700"
        >
          返回项目库
        </a>
      </div>
    );
  }

  return (
    <div className="h-[calc(100vh-0px)] w-full" data-testid="canvas-editor">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onMoveEnd={handleMoveEnd}
        defaultViewport={viewport}
        minZoom={0.2}
        maxZoom={2}
        panOnScroll
        selectionOnDrag
        fitView={false}
        onNodeClick={(_, node) => openFor(node.id)}
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1.5} />
        <Controls />
        <MiniMap pannable zoomable />
        <AddNodeToolbar />
        <Panel
          position="top-left"
          style={{ top: 58, left: 12 }}
          className="!absolute flex max-w-[calc(100vw-140px)] flex-wrap items-center gap-1 rounded-xl bg-white/95 p-1 shadow-md backdrop-blur"
        >
          <div className="rounded-full bg-zinc-50 px-1">
            <NodeSearch />
          </div>
          <ToolbarBtn label="撤销 (Ctrl+Z)" disabled={!canUndo} onClick={() => useCanvasStore.getState().undo()}>
            <Undo2 className="h-3.5 w-3.5" /> 撤销
          </ToolbarBtn>
          <ToolbarBtn label="重做 (Ctrl+Shift+Z)" disabled={!canRedo} onClick={() => useCanvasStore.getState().redo()}>
            <Redo2 className="h-3.5 w-3.5" /> 重做
          </ToolbarBtn>
          <span className="mx-0.5 h-4 w-px bg-zinc-200" />
          <ToolbarBtn label="导出画布 JSON" onClick={handleExport}>
            导出
          </ToolbarBtn>
          <label
            className="flex cursor-pointer items-center gap-1 rounded-full px-2.5 py-1 text-xs text-zinc-600 hover:bg-zinc-100"
            title="导入画布 JSON（创建新画布）"
          >
            导入
            <input
              type="file"
              accept="application/json"
              className="hidden"
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                try {
                  const parsed = parseImport(await file.text());
                  // 创建新项目并写入
                  const created = await apiFetch<{ project: { id: number } }>("/api/canvas/projects", {
                    method: "POST",
                    json: { title: parsed.title },
                  });
                  const newId = created.project.id;
                  await apiFetch(`/api/canvas/projects/${newId}/nodes`, {
                    method: "PUT",
                    json: {
                      nodes: parsed.nodes.map((n) => ({
                        type: n.data.nodeType,
                        title: n.data.title,
                        position_x: n.position.x,
                        position_y: n.position.y,
                        metadata_json: JSON.stringify({ prompt: n.data.prompt, outputUrl: n.data.outputUrl }),
                      })),
                    },
                  });
                  await apiFetch(`/api/canvas/projects/${newId}/connections`, {
                    method: "PUT",
                    json: {
                      connections: parsed.edges.map((edge) => ({
                        from_node_id: Number(edge.source),
                        to_node_id: Number(edge.target),
                      })),
                    },
                  });
                  window.location.assign(`/canvas/${newId}`);
                } catch (err) {
                  alert(err instanceof Error ? err.message : "导入失败");
                }
              }}
            />
          </label>
          <ToolbarBtn label="批量创建多行提示词" onClick={() => setBatchOpen((v) => !v)}>
            批量创作
          </ToolbarBtn>
        </Panel>
        {batchOpen && <BatchTablePanel domain="image" onClose={() => setBatchOpen(false)} />}
        {selectedImageNodes.length >= 2 && !batchAdjustOpen && (
          <Panel position="bottom-left" className="!bottom-6">
            <button
              onClick={() => setBatchAdjustOpen(true)}
              className="rounded-full bg-zinc-900 px-3 py-1.5 text-xs text-white shadow-md hover:bg-zinc-700 cursor-pointer"
            >
              批量调整（已选 {selectedImageNodes.length} 张）
            </button>
          </Panel>
        )}
        {batchAdjustOpen && selectedImageNodes.length >= 2 && (
          <AdjustDialog
            nodeId={selectedImageNodes[0].id}
            src={selectedImageNodes[0].data.outputUrl as string}
            batchTargets={selectedImageNodes.map((n) => ({
              nodeId: n.id,
              src: n.data.outputUrl as string,
            }))}
            onClose={() => setBatchAdjustOpen(false)}
          />
        )}
        <Panel position="top-center" className="flex items-center gap-2">
          {projectTitle && (
            <span
              className="max-w-48 truncate rounded-full bg-white/95 px-3 py-1 text-xs font-medium text-zinc-700 shadow-sm"
              title={projectTitle}
            >
              {projectTitle}
            </span>
          )}
          <SnapshotDrawer projectId={projectId ?? ""} onRestored={() => window.location.reload()} />
        </Panel>
        <Panel position="top-right">
          <SaveIndicator projectId={projectId} />
        </Panel>
        {!isLoadingProject && nodes.length === 0 && (
          <Panel position="bottom-center" className="!bottom-24">
            <div className="rounded-xl bg-white/95 px-5 py-3 text-center text-xs text-zinc-500 shadow-md">
              <p className="text-sm font-medium text-zinc-700">从添加第一个节点开始</p>
              <p className="mt-1">点击左上角「文字 / 图片 / 视频 / 音频」添加节点，写好提示词后点「生成」。</p>
            </div>
          </Panel>
        )}
        <PromptPanel />
      </ReactFlow>
    </div>
  );
}
