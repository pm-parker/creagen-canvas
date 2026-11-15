import { useEffect, useRef, useState } from "react";

/**
 * 打字机文本（Beautiful UI Streaming Text 模式）：
 * 文本变化时逐字显现；新文本以旧文本为前缀则增量续播；点击跳过。
 */
export function StreamingText({
  text,
  className,
  cps = 120,
}: {
  text: string;
  className?: string;
  cps?: number;
}) {
  const [shown, setShown] = useState(text);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    setShown((prev) => {
      // 增量续播：新文本是已显示文本的扩展 → 从当前长度继续；否则重播
      return text.startsWith(prev) ? prev : "";
    });
  }, [text]);

  useEffect(() => {
    setShown((prev) => {
      if (prev.length >= text.length) return text;
      // 启动逐字推进
      if (timer.current) clearInterval(timer.current);
      const start = text.startsWith(prev) ? prev.length : 0;
      let i = start;
      timer.current = setInterval(() => {
        i += Math.max(1, Math.round(cps / 60));
        if (i >= text.length) {
          setShown(text);
          if (timer.current) clearInterval(timer.current);
        } else {
          setShown(text.slice(0, i));
        }
      }, 16);
      return start < text.length ? text.slice(0, start) : text;
    });
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [text, cps]);

  const typing = shown.length < text.length;

  return (
    <span className={className} onClick={() => setShown(text)} title={typing ? "点击立即显示全部" : undefined}>
      {shown}
      {typing && <span className="ml-0.5 inline-block h-3 w-[2px] animate-pulse bg-zinc-400 align-middle" />}
    </span>
  );
}
