import { useEffect, useState } from "react";
import { Eye, EyeOff, Sun, Moon, Monitor } from "lucide-react";
import { getStoredTheme, storeTheme, type ThemeMode } from "@/lib/theme";

const MODES: { mode: ThemeMode; label: string; icon: typeof Sun }[] = [
  { mode: "light", label: "浅色", icon: Sun },
  { mode: "dark", label: "深色", icon: Moon },
  { mode: "system", label: "跟随系统", icon: Monitor },
];

export function ThemeToggle({ compact = false }: { compact?: boolean }) {
  const [mode, setMode] = useState<ThemeMode>(() => getStoredTheme());

  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === "cc-theme") setMode(getStoredTheme());
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  return (
    <div className="flex items-center gap-1" data-testid="theme-toggle">
      {MODES.map(({ mode: m, label, icon: Icon }) => (
        <button
          key={m}
          aria-label={`主题-${label}`}
          title={label}
          onClick={() => {
            setMode(m);
            storeTheme(m);
          }}
          className={`flex cursor-pointer items-center gap-1 rounded-lg ${
            compact ? "h-8 w-8 justify-center" : "h-9 px-3 text-sm"
          } ${
            mode === m
              ? "bg-zinc-900 text-white"
              : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-700"
          }`}
        >
          <Icon className="h-4 w-4" />
          {!compact && label}
        </button>
      ))}
    </div>
  );
}

export { Eye, EyeOff };
