import { createContext } from "react";

export type ThemePreference = "system" | "light" | "dark";
export const themeStorageKey = "interviewos.theme";
export function parseTheme(value: unknown): ThemePreference {
  return value === "light" || value === "dark" ? value : "system";
}
export function readTheme(): ThemePreference {
  try { return parseTheme(localStorage.getItem(themeStorageKey)); }
  catch { return "system"; }
}
export const ThemeContext = createContext<{
  preference: ThemePreference;
  resolved: "light" | "dark";
  setPreference: (value: ThemePreference) => void;
} | null>(null);
