import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { parseTheme, readTheme, ThemeContext, themeStorageKey } from "./theme";

export default function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreference] = useState(readTheme);
  const [systemDark, setSystemDark] = useState(() => window.matchMedia("(prefers-color-scheme: dark)").matches);
  const resolved = preference === "system" ? (systemDark ? "dark" : "light") : preference;
  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const update = (event: MediaQueryListEvent) => setSystemDark(event.matches);
    const storage = (event: StorageEvent) => { if (event.key === themeStorageKey || event.key === null) setPreference(parseTheme(event.newValue)); };
    media.addEventListener("change", update);
    window.addEventListener("storage", storage);
    return () => { media.removeEventListener("change", update); window.removeEventListener("storage", storage); };
  }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = resolved;
    document.documentElement.dataset.themePreference = preference;
    try { localStorage.setItem(themeStorageKey, preference); } catch { /* Session preference still works without storage. */ }
  }, [preference, resolved]);
  const value = useMemo(() => ({ preference, resolved, setPreference }), [preference, resolved]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}
