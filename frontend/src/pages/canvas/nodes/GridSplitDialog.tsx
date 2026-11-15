import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { apiFetch } from "@/lib/api";
import { gridSplitRC } from "@/lib/grid-split";
import { useCanvasStore } from "@/stores/canvas";
import { Button } from "@/components/ui/button";

const PRESETS: Array<{ cols: number; rows: number; label: string }> = [
  { cols: 2, rows: 2, label: "2×2" },
  { cols: 3, rows: 3, label: "3×3" },
  { cols: 4, rows: 4, label: "4×4" },
];

/** 图片宫格切分（P0-4 起支持自定义行列 1~4）：切块 → 上传 → 子节点（自动连线排布）。 */
export function GridSplitDialog({
  imageUrl,
  sourceNodeId,
  onClose,
}: {
  imageUrl: string;
  sourceNodeId: string;
  onClose: () => void;
}) {
  const [cols, setCols] = useState(2);
  const [rows, setRows] = useState(2);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const imgRef = useRef<HTMLImageElement | null>(null);
  const addNode = useCanvasStore((s) => s.addNode);
  const updateNodeData = useCanvasStore((s) => s.updateNodeData);
  const onConnect = useCanvasStore((s) => s.onConnect);

  useEffect(() => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.src = imageUrl;
    imgRef.current = img;
  }, [imageUrl]);

  async function split() {
    setBusy(true);
    setError("");
    try {
      const img = await new Promise<HTMLImageElement>((resolve, reject) => {
        const el = new window.Image();
        el.crossOrigin = "anonymous";
        el.onload = () => resolve(el);
        el.onerror = () => reject(new Error("图片加载失败"));
        el.src = imageUrl;
      });
      const cells = gridSplitRC(img.naturalWidth, img.naturalHeight, cols, rows);
      if (cells.length === 0) throw new Error("行列需在 1~4 之间");
      const source = useCanvasStore.getState().nodes.find((n) => n.id === sourceNodeId);
      const created: string[] = [];
      for (const c of cells) {
        const canvas = document.createElement("canvas");
        canvas.width = c.sw;
        canvas.height = c.sh;
        const ctx = canvas.getContext("2d");
        if (!ctx) throw new Error("canvas 不可用");
        ctx.drawImage(img, c.sx, c.sy, c.sw, c.sh, 0, 0, c.sw, c.sh);
        const dataUrl = canvas.toDataURL("image/png");
        const up = await apiFetch<{ url: string }>("/api/uploads", {
          method: "POST",
          json: { data_url: dataUrl },
        });
        const parentPos = source?.position ?? { x: 0, y: 0 };
        const scale = 240 / Math.max(img.naturalWidth, 1);
        const id = addNode("image", {
          x: parentPos.x + c.sx * scale,
          y: parentPos.y + c.sy * scale,
        });
        updateNodeData(id, {
          title: `切块 ${c.sx}-${c.sy}`,
          outputUrl: up.url,
        });
        created.push(id);
      }
      // 子节点回连源节点
      for (const cid of created) {
        onConnect({ source: sourceNodeId, target: cid, sourceHandle: null, targetHandle: null });
      }
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "切分失败");
    } finally {
      setBusy(false);
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/40">
      <div className="w-full max-w-sm space-y-4 rounded-2xl bg-white p-6 shadow-xl">
        <h3 className="text-lg font-semibold">宫格切分</h3>
        <p className="text-sm text-zinc-500">
          把图片均分为若干块（行列 1~4 可自定义），每块生成一个独立图片节点。
        </p>
        <div className="flex gap-2">
          {PRESETS.map(({ cols: c, rows: r, label }) => (
            <button
              key={label}
              onClick={() => {
                setCols(c);
                setRows(r);
              }}
              className={
                "h-10 flex-1 rounded-lg border text-sm cursor-pointer " +
                (cols === c && rows === r
                  ? "border-zinc-900 bg-zinc-900 text-white"
                  : "border-zinc-200 hover:bg-zinc-50")
              }
            >
              {label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2 text-sm text-zinc-600">
          <span>自定义</span>
          <input
            type="number"
            min={1}
            max={4}
            value={cols}
            aria-label="列数"
            onChange={(e) => setCols(Math.min(4, Math.max(1, Number(e.target.value) || 1)))}
            className="h-8 w-16 rounded-lg border border-zinc-200 px-2"
          />
          <span>列 ×</span>
          <input
            type="number"
            min={1}
            max={4}
            value={rows}
            aria-label="行数"
            onChange={(e) => setRows(Math.min(4, Math.max(1, Number(e.target.value) || 1)))}
            className="h-8 w-16 rounded-lg border border-zinc-200 px-2"
          />
          <span>行</span>
        </div>
        {error && <p className="text-xs text-red-600">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button onClick={split} disabled={busy}>
            {busy ? "切分中…" : `切分为 ${cols * rows} 块`}
          </Button>
        </div>
      </div>
    </div>
  , document.body);
}
