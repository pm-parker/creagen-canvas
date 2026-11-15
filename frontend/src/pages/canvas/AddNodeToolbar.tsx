import { Panel, ReactFlowProvider, useReactFlow } from "@xyflow/react";
import { useCanvasStore, type CanvasNodeType } from "@/stores/canvas";
import { LayoutGrid } from "lucide-react";

const NODE_MENU: Array<{ type: CanvasNodeType; label: string; icon: string }> = [
  { type: "text", label: "文字", icon: "T" },
  { type: "image", label: "图片", icon: "🖼" },
  { type: "video", label: "视频", icon: "▶" },
  { type: "audio", label: "音频", icon: "♪" },
];

function ToolbarInner() {
  const addNode = useCanvasStore((s) => s.addNode);
  const { screenToFlowPosition } = useReactFlow();

  function add(type: CanvasNodeType) {
    const jitter = () => (Math.random() - 0.5) * 320;
    const position = screenToFlowPosition({
      x: window.innerWidth / 2 - 110 + jitter(),
      y: window.innerHeight / 3 + jitter(),
    });
    addNode(type, position);
  }

  const autoArrange = useCanvasStore((s) => s.autoArrange);
  return (
    <Panel position="top-left" className="flex gap-1 rounded-xl bg-white/95 p-1 shadow-md backdrop-blur">
      {NODE_MENU.map((item) => (
        <button
          key={item.type}
          data-testid={`add-node-${item.type}`}
          aria-label={`添加${item.label}节点`}
          onClick={() => add(item.type)}
          className="flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-100 cursor-pointer"
        >
          <span aria-hidden>{item.icon}</span>
          {item.label}
        </button>
      ))}
      <button
        aria-label="自动整理"
        title="自动整理布局"
        onClick={autoArrange}
        className="flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs font-medium text-indigo-600 hover:bg-indigo-50 cursor-pointer"
      >
        <LayoutGrid className="h-3.5 w-3.5" /> 整理
      </button>
    </Panel>
  );
}

export function AddNodeToolbar() {
  return (
    <ReactFlowProvider>
      <ToolbarInner />
    </ReactFlowProvider>
  );
}
