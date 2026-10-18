import { Link, useLocation } from "react-router-dom";
import ThemeToggle from "./ThemeToggle";
import { supabase } from "./lib";

export function Brand() {
  const location = useLocation();
  return <Link to={location.pathname === "/candidate" ? "/candidate" : "/"} className="glass-brand" aria-label="InterviewOS home"><span className="brand-mark" aria-hidden="true"><svg width="23" height="23" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><rect x="4" y="4" width="16" height="13" rx="4" /><path d="m8 17-1 4 5-4M8 9h8m-8 4h4" /></svg></span><span>Interview<span className="brand-suffix">OS</span></span></Link>;
}

function WorkspaceTabs({ mobile = false }: { mobile?: boolean }) {
  const location = useLocation();
  const company = location.pathname === "/company";
  const results = company && location.hash === "#results";
  const candidate = location.pathname === "/candidate";
  return <nav className={`glass-tabs ${mobile ? "mobile-tabs" : "desktop-tabs"}${candidate ? " candidate-tabs" : ""}`} aria-label={candidate ? "Candidate navigation" : "Workspace navigation"}>
      {!candidate && <Link to="/company" className={company && !results ? "is-active" : ""} aria-current={company && !results ? "page" : undefined}>Workspace</Link>}
      {!candidate && <Link to="/company#results" className={results ? "is-active" : ""} aria-current={results ? "page" : undefined}>Results</Link>}
      <Link to="/candidate" className={!company ? "is-active" : ""} aria-current={!company ? "page" : undefined}>My interviews</Link>
    </nav>;
}

export default function GlassNavbar({ publicPage = false }: { publicPage?: boolean }) {
  return <header className="glass-nav-wrap"><div className="glass-navbar">
    <Brand />
    {!publicPage && <WorkspaceTabs />}
    <div className="glass-nav-actions"><ThemeToggle />{!publicPage && <button type="button" className="glass-control" onClick={() => supabase.auth.signOut()}>Sign out</button>}</div>
  </div>{!publicPage && <WorkspaceTabs mobile />}</header>;
}
