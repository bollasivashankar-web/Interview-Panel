import { useContext } from "react";
import { ThemeContext } from "./theme";

export default function ThemeToggle() {
  const theme = useContext(ThemeContext);
  if (!theme) return null;
  const next = theme.preference === "system" ? "light" : theme.preference === "light" ? "dark" : "system";
  return <button type="button" className="glass-control theme-toggle" aria-label={`Appearance: ${theme.preference}. Switch to ${next}`} title={`Appearance: ${theme.preference}. Switch to ${next}`} onClick={() => theme.setPreference(next)}>
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">{theme.resolved === "dark" ? <path d="M20.9 13.1A9 9 0 0 1 10.9 3.1 9 9 0 1 0 20.9 13.1Z" /> : <><circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.4 1.4m11.2 11.2L19 19M5 19l1.4-1.4M17.6 6.4 19 5" /></>}</svg>
    <span>{theme.preference === "system" ? "Auto" : theme.preference === "light" ? "Light" : "Dark"}</span>
  </button>;
}
