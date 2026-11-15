const KEY = "cc-theme";

export type ThemeMode = "light" | "dark" | "system";

export function getStoredTheme(): ThemeMode {
  const v = localStorage.getItem(KEY);
  return v === "light" || v === "dark" || v === "system" ? v : "system";
}

export function storeTheme(mode: ThemeMode) {
  localStorage.setItem(KEY, mode);
  applyTheme();
}

export function applyTheme() {
  const mode = getStoredTheme();
  const dark =
    mode === "dark" ||
    (mode === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

export function initTheme() {
  applyTheme();
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (getStoredTheme() === "system") applyTheme();
  });
}
