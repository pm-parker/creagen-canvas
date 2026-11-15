/** 首页自由创作 Agent——@引用输入区（mirror 高亮 + 提及 chip）。 */
import { forwardRef, useEffect, useMemo, useRef } from "react";

export type MentionType = "asset" | "character" | "prompt" | "model" | "unknown";

export interface MentionOption {
  type: MentionType;
  label: string;
  /** asset */
  task_id?: number;
  /** character / prompt */
  id?: number;
  /** model */
  channel_id?: number;
  model?: string;
}

const CHIP_CLASS: Record<MentionType, string> = {
  asset: "bg-amber-100 text-amber-800",
  character: "bg-rose-100 text-rose-800",
  prompt: "bg-indigo-100 text-indigo-800",
  model: "bg-emerald-100 text-emerald-800",
  unknown: "bg-zinc-100 text-zinc-700",
};

const FRAME_CLASS = "p-3 text-sm leading-6 font-sans border rounded-xl";

function mentionType(label: string): MentionType {
  if (label.startsWith("@素材:")) return "asset";
  if (label.startsWith("@角色:")) return "character";
  if (label.startsWith("@提示词:")) return "prompt";
  if (label.startsWith("@模型:")) return "model";
  return "unknown";
}

/** 把文本按已选提及 chip 切成 text/mention 片段，供镜像层高亮。 */
function segment(text: string, labels: string[]): { kind: "text" | "mention"; text?: string; label?: string; type?: MentionType }[] {
  const present = labels.filter((l) => l && text.includes(l));
  const out: { kind: "text" | "mention"; text?: string; label?: string; type?: MentionType }[] = [];
  let rest = text;
  while (rest.length > 0) {
    let found: string | null = null;
    let at = Infinity;
    for (const label of present) {
      const idx = rest.indexOf(label);
      if (idx !== -1 && idx < at) {
        at = idx;
        found = label;
      }
    }
    if (found === null) {
      out.push({ kind: "text", text: rest });
      break;
    }
    if (at > 0) out.push({ kind: "text", text: rest.slice(0, at) });
    out.push({ kind: "mention", label: found, type: mentionType(found) });
    rest = rest.slice(at + found.length);
  }
  return out;
}

interface MentionTextareaProps {
  value: string;
  onChange: (value: string) => void;
  onKeyDown?: (e: React.KeyboardEvent<HTMLTextAreaElement>) => void;
  placeholder?: string;
  rows?: number;
  mentions: string[];
}

/** 透明 textarea + 背后镜像层渲染彩色 chip（0.15 自由创作输入框）。 */
export const MentionTextarea = forwardRef<HTMLTextAreaElement, MentionTextareaProps>(
  function MentionTextarea({ value, onChange, onKeyDown, placeholder, rows = 3, mentions }, ref) {
    const parts = useMemo(() => segment(value, mentions), [value, mentions]);
    const mirrorRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
      const el = typeof ref === "function" ? null : ref?.current;
      if (!el || !mirrorRef.current) return;
      const sync = () => {
        if (mirrorRef.current) mirrorRef.current.scrollTop = el.scrollTop;
      };
      mirrorRef.current.addEventListener("scroll", sync);
      sync();
      return () => mirrorRef.current?.removeEventListener("scroll", sync);
    }, [ref, value]);

    return (
      <div className="relative">
        <div
          ref={mirrorRef}
          aria-hidden="true"
          className={`pointer-events-none absolute inset-0 z-0 overflow-hidden whitespace-pre-wrap break-words border-transparent ${FRAME_CLASS}`}
          data-testid="mention-mirror"
        >
          {parts.map((p, i) =>
            p.kind === "mention" ? (
              <span key={i} data-testid={`mention-chip-${p.label}`} className={`rounded px-1 py-px font-medium ${CHIP_CLASS[p.type ?? "unknown"]}`}>
                {p.label}
              </span>
            ) : (
              <span key={i}>{p.text}</span>
            ),
          )}
          {"\u200b"}
        </div>
        <textarea
          ref={ref}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
          rows={rows}
          placeholder={placeholder}
          className={`relative z-10 w-full resize-none bg-transparent text-transparent caret-zinc-900 outline-none transition-colors focus:border-indigo-300 ${FRAME_CLASS}`}
        />
      </div>
    );
  },
);
