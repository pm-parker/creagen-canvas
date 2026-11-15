import { useEffect, useState } from "react";

const PHASES = ["排队中…", "已提交渠道…", "模型生成中…", "即将完成…"];
const SHIMMER_LINES = [
  { width: "100%", delay: "0ms" },
  { width: "88%", delay: "120ms" },
  { width: "64%", delay: "240ms" },
];

const KEYFRAMES = `
@keyframes cg-orbit { to { transform: rotate(360deg); } }
@keyframes cg-fade { from { opacity: 0; transform: translateY(3px); } to { opacity: 1; transform: translateY(0); } }
@keyframes cg-sweep { 0% { transform: translateX(-100%); } 100% { transform: translateX(100%); } }
`;

function useElapsed(startedAt?: number | null) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!startedAt) return;
    const id = setInterval(() => setElapsed((Date.now() - startedAt) / 1000), 100);
    return () => clearInterval(id);
  }, [startedAt]);
  return elapsed;
}

/** 阶段文案轮换 + mono 计时（Beautiful UI Loading State 模式）。 */
export function GenerationPhaseText({ startedAt, dark = false }: { startedAt?: number | null; dark?: boolean }) {
  const [phase, setPhase] = useState(0);
  const elapsed = useElapsed(startedAt);

  useEffect(() => {
    const id = setInterval(() => setPhase((p) => (p + 1) % PHASES.length), 2200);
    return () => clearInterval(id);
  }, []);

  return (
    <span className={`inline-flex items-center gap-2 font-mono text-xs ${dark ? "text-zinc-400" : "text-zinc-400"}`}>
      <style>{KEYFRAMES}</style>
      <span key={phase} style={{ animation: "cg-fade 400ms both" }} className="inline-block">
        {PHASES[phase]}
      </span>
      {startedAt ? <span className="text-zinc-300">{elapsed.toFixed(1)}s</span> : null}
    </span>
  );
}

/** shimmer 骨架行（宽度递减 + 扫光）。 */
export function ShimmerLines({ dark = false, count = 3 }: { dark?: boolean; count?: number }) {
  const line = dark ? "bg-zinc-700/70" : "bg-zinc-200/80";
  return (
    <div className="space-y-2.5" aria-hidden="true">
      <style>{KEYFRAMES}</style>
      {SHIMMER_LINES.slice(0, count).map((l) => (
        <div key={l.width} className={`relative h-2.5 overflow-hidden rounded-full ${line}`} style={{ width: l.width }}>
          <div
            className="absolute inset-0"
            style={{
              animation: "cg-sweep 1.6s infinite",
              background: dark
                ? "linear-gradient(90deg, transparent, rgba(255,255,255,0.08), transparent)"
                : "linear-gradient(90deg, transparent, rgba(255,255,255,0.7), transparent)",
            }}
          />
        </div>
      ))}
    </div>
  );
}

/** 生成中卡片主体：orbit + 阶段文案 + 计时 + shimmer。 */
export function GenerationPulseCard({ startedAt, dark = false }: { startedAt?: number | null; dark?: boolean }) {
  return (
    <div
      className={`w-full rounded-xl border p-3.5 ${
        dark ? "border-zinc-700 bg-zinc-800/60" : "border-zinc-200 bg-zinc-50/80"
      }`}
    >
      <div className="mb-3 flex items-center gap-2.5">
        <span className="relative flex h-4 w-4 items-center justify-center">
          <span
            className={`absolute inset-0 rounded-full border-2 border-zinc-300 border-t-zinc-900 ${
              dark ? "!border-zinc-700 !border-t-zinc-300" : ""
            }`}
            style={{ animation: "cg-orbit 900ms linear infinite" }}
          />
        </span>
        <GenerationPhaseText startedAt={startedAt} dark={dark} />
      </div>
      <ShimmerLines dark={dark} />
    </div>
  );
}
