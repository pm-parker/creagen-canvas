import { useEffect, useState } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Grid3x3, Crop, PenTool, Camera, PaintBucket, Wrench, SlidersHorizontal, Shuffle, Film, Scissors } from "lucide-react";
import type { CanvasNodeData } from "@/stores/canvas";
import { GenerateButton } from "./GenerateButton";
import { GenerationHistoryPicker } from "./GenerationHistoryPicker";
import { ImageEditorDialog } from "./ImageEditorDialog";
import { AdjustDialog } from "./AdjustDialog";
import { TransformDialog } from "./TransformDialog";
import { VideoToolDialog } from "./VideoToolDialog";
import { usePanel } from "@/stores/panel";
import { GridSplitDialog } from "./GridSplitDialog";
import { MultiAngleDialog } from "./MultiAngleDialog";
import { cn } from "@/lib/utils";

const TYPE_META: Record<string, { label: string; band: string; icon: string }> = {
  text: { label: "文字", band: "bg-zinc-700", icon: "T" },
  image: { label: "图片", band: "bg-emerald-600", icon: "🖼" },
  video: { label: "视频", band: "bg-indigo-600", icon: "▶" },
  audio: { label: "音频", band: "bg-amber-600", icon: "♪" },
};

export function CanvasNode({ id, data, selected }: NodeProps & { id?: string }) {
  const d = data as unknown as CanvasNodeData & { outputUrl?: string };
  const meta = TYPE_META[d.nodeType] ?? TYPE_META.text;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(d.title);
  const [editorMode, setEditorMode] = useState<null | "crop" | "annotate" | "inpaint">(null);
  const [adjustOpen, setAdjustOpen] = useState(false);
  const [transformOpen, setTransformOpen] = useState(false);
  const [videoTool, setVideoTool] = useState<null | "frame" | "trim">(null);
  const [gridOpen, setGridOpen] = useState(false);
  const [angleOpen, setAngleOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const updateNodeData = useUpdateNodeData();
  const closePanel = usePanel((s) => s.close);

  useEffect(() => setDraft(d.title), [d.title]);

  function commitTitle() {
    setEditing(false);
    if (id && draft !== d.title) updateNodeData(id, { title: draft.trim() });
  }

  const hasPreview = Boolean(d.outputUrl) && d.nodeType === "image";
  const generatingRun = useTaskStore((s) =>
    Object.values(s.runs).find((r) => r.sourceNodeId === id && (r.status === "generating" || r.status === "queued")),
  );
  const isGeneratedText =
    d.nodeType === "text" && Boolean(d.prompt) && (d.title.startsWith("生成") || d.prompt.length > 80);

  return (
    <div
      data-testid={`canvas-node-${d.nodeType}`}
      className={cn(
        "w-60 overflow-hidden rounded-xl border bg-white shadow-sm transition-shadow",
        selected ? "border-zinc-900 ring-2 ring-zinc-900/20 shadow-md" : "border-zinc-200",
      )}
      onDoubleClick={() => !editing && setEditing(true)}
    >
      <div className={`h-1.5 ${meta.band}`} />
      {hasPreview && (
        <img
          src={d.outputUrl}
          alt={d.title || "生成图"}
          className="max-h-44 w-full object-cover"
          loading="lazy"
        />
      )}
      <div className="flex items-center gap-2 px-3 py-2.5">
        <span aria-hidden className="text-sm">
          {meta.icon}
        </span>
        <div className="min-w-0 flex-1">
          {editing ? (
            <input
              autoFocus
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={commitTitle}
              onKeyDown={(e) => {
                if (e.key === "Enter") commitTitle();
                if (e.key === "Escape") setEditing(false);
                e.stopPropagation();
              }}
              className="w-full rounded border border-zinc-300 px-1.5 py-0.5 text-sm outline-none"
            />
          ) : (
            <p className="truncate text-sm font-medium text-zinc-900">{d.title || meta.label}</p>
          )}
          <p className="truncate text-xs text-zinc-400">
            {isGeneratedText
              ? <StreamingText text={d.prompt.slice(0, 60)} className="block" cps={160} />
              : d.prompt
                ? d.prompt.slice(0, 40)
                : `双击命名 · ${meta.label}节点待写提示词`}
          </p>
        </div>
      </div>
      {generatingRun && d.nodeType !== "audio" && (
        <div className="px-3 pb-2">
          <GenerationPulseCard startedAt={generatingRun.startedAt} />
        </div>
      )}
      <GenerateButton node={{ id: String(id), data: d } as never} />
      {d.outputUrl && (d.nodeType === "image" || d.nodeType === "video") && (
        <div className="absolute -top-3 left-2 z-10 flex items-center gap-1">
          <GenerationHistoryPicker
            nodeId={String(id)}
            activeUrl={d.outputUrl}
            onSelect={(url) => {
              if (id) updateNodeData(String(id), { outputUrl: url });
            }}
          />
          {(d.nodeType === "image" || d.nodeType === "video") && id && (
            <div className="relative">
              <button
                aria-label="加工工具"
                title="加工工具"
                onClick={(e) => {
                  e.stopPropagation(); // 防冒泡到节点点击→打开提示词面板
                  setMenuOpen((v) => !v);
                }}
                className="flex items-center gap-1 rounded-full bg-zinc-100 px-2 py-0.5 text-xs text-zinc-600 hover:bg-zinc-200 cursor-pointer"
              >
                <Wrench className="h-3 w-3" /> 加工
              </button>
              {menuOpen && (
                <div
                  className="absolute left-0 top-6 z-30 w-36 rounded-xl border border-zinc-200 bg-white p-1 shadow-lg"
                  onMouseLeave={() => setMenuOpen(false)}
                >
                  {(d.nodeType === "video"
                    ? ([
                        { label: "提取帧…", icon: Film, act: () => setVideoTool("frame") },
                        { label: "截取视频…", icon: Scissors, act: () => setVideoTool("trim") },
                      ] as const)
                    : ([
                        { label: "调整…", icon: SlidersHorizontal, act: () => setAdjustOpen(true) },
                        { label: "变换与导出…", icon: Shuffle, act: () => setTransformOpen(true) },
                        { label: "裁切…", icon: Crop, act: () => setEditorMode("crop") },
                        { label: "标注…", icon: PenTool, act: () => setEditorMode("annotate") },
                        { label: "局部重绘…", icon: PaintBucket, act: () => setEditorMode("inpaint") },
                        { label: "多角度生成…", icon: Camera, act: () => setAngleOpen(true) },
                        { label: "宫格切分…", icon: Grid3x3, act: () => setGridOpen(true) },
                      ] as const)
                  ).map(({ label, icon: Icon, act }) => (
                    <button
                      key={label}
                      onClick={(e) => {
                        e.stopPropagation();
                        setMenuOpen(false);
                        closePanel(); // 弹窗与底部提示词面板会在 Flow 层叠上下文里重叠
                        act();
                      }}
                      className="flex w-full cursor-pointer items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs text-zinc-700 hover:bg-zinc-100"
                    >
                      <Icon className="h-3.5 w-3.5 text-zinc-400" /> {label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
      {editorMode && id && d.outputUrl && (
        <ImageEditorDialog
          nodeId={String(id)}
          src={d.outputUrl}
          mode={editorMode}
          onClose={() => setEditorMode(null)}
        />
      )}
      {adjustOpen && id && d.outputUrl && (
        <AdjustDialog nodeId={String(id)} src={d.outputUrl} onClose={() => setAdjustOpen(false)} />
      )}
      {transformOpen && id && d.outputUrl && (
        <TransformDialog nodeId={String(id)} src={d.outputUrl} onClose={() => setTransformOpen(false)} />
      )}
      {videoTool && id && d.nodeType === "video" && d.outputUrl && (
        <VideoToolDialog
          nodeId={String(id)}
          src={d.outputUrl}
          mode={videoTool}
          onClose={() => setVideoTool(null)}
        />
      )}
      {gridOpen && id && d.outputUrl && (
        <GridSplitDialog sourceNodeId={String(id)} imageUrl={d.outputUrl} onClose={() => setGridOpen(false)} />
      )}
      {angleOpen && id && (
        <MultiAngleDialog
          nodeId={String(id)}
          nodeType="image"
          prompt={d.prompt ?? ""}
          onClose={() => setAngleOpen(false)}
        />
      )}
      <Handle type="target" position={Position.Left} />
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

import { useCanvasStore } from "@/stores/canvas";
import { useTaskStore } from "@/stores/tasks";
import { GenerationPulseCard } from "@/components/GenerationPulse";
import { StreamingText } from "@/components/StreamingText";

function useUpdateNodeData() {
  return useCanvasStore((s) => s.updateNodeData);
}
