/** 更新日志抽屉（首页「最近上新」）——解析 src/assets/CHANGELOG.md（与后端内嵌同一份）。 */
import { useMemo, useState } from "react";
import changelogRaw from "@/assets/CHANGELOG.md?raw";

const DATE_RE = /\d{4}-\d{2}-\d{2}/;

export interface ChangelogEntry {
  version: string;
  date: string;
  summary: string;
}

/** 从 CHANGELOG.md 提取最近 n 个版本的 {版本, 日期, 一句话摘要}。 */
export function parseChangelog(markdown: string, limit = 3): ChangelogEntry[] {
  const entries = markdown
    .split(/^## /m)
    .slice(1)
    .map((block) => {
      const lines = block.split("\n");
      const head = lines[0].trim();
      const version = head.split(/[（(]/)[0].trim();
      const date = head.match(DATE_RE)?.[0] ?? "";
      let summary = "";
      const paren = (head.match(/[（(](.*)[）)]/)?.[1] ?? "").split(DATE_RE)[1];
      if (paren) summary = paren.replace(/^[，,、:：\s]+/, "").trim();
      if (!summary) {
        const firstText = lines.slice(1).find((l) => {
          const t = l.trim();
          return t !== "" && !l.startsWith("-") && !l.startsWith("#");
        });
        if (firstText) {
          summary = firstText.trim();
        } else {
          const firstBullet = lines.slice(1).find((l) => l.trim().startsWith("-"));
          if (firstBullet) summary = firstBullet.trim().replace(/^-\s*/, "").replace(/\*\*/g, "").trim();
        }
      }
      return { version, date, summary };
    });
  const dated = entries.filter((e) => e.date !== "");
  const undated = entries.filter((e) => e.date === "");
  dated.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
  return [...dated, ...undated].slice(0, limit);
}

/** 画布首页同款更新提示（AppShell 右上抽屉）。 */
export function ChangelogDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const entries = useMemo(() => parseChangelog(changelogRaw, 20), []);
  const [expanded, setExpanded] = useState(false);
  if (!open) return null;
  const shown = expanded ? entries : entries.slice(0, 3);
  return (
    <div className="absolute inset-0 z-50" role="dialog" aria-modal="true">
      <button aria-label="关闭更新日志" onClick={onClose} className="absolute inset-0 cursor-default bg-black/20" />
      <div className="absolute top-14 right-4 max-h-[70vh] w-80 overflow-y-auto rounded-xl border border-zinc-200 bg-white p-4 shadow-xl">
        <p className="mb-2 text-sm font-semibold text-zinc-900">更新日志</p>
        <div className="space-y-3">
          {shown.map((e) => (
            <div key={e.version} className="border-b border-zinc-100 pb-2 last:border-0">
              <p className="flex items-center gap-2 text-xs font-medium text-zinc-700">
                <span className="rounded bg-indigo-50 px-1.5 py-0.5 font-medium text-indigo-600">v{e.version}</span>
                {e.date && <span className="text-zinc-500">{e.date}</span>}
              </p>
              {e.summary && <p className="mt-1.5 line-clamp-3 text-xs text-zinc-500">{e.summary}</p>}
            </div>
          ))}
        </div>
        {!expanded && entries.length > 3 && (
          <button
            type="button"
            onClick={() => setExpanded(true)}
            className="mt-2 cursor-pointer text-xs text-indigo-500 hover:underline"
          >
            查看全部 →
          </button>
        )}
      </div>
    </div>
  );
}
