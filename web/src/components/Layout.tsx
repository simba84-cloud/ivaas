import {
  Camera,
  ClipboardCheck,
  FileVideo,
  LayoutDashboard,
  LogOut,
  MonitorPlay,
  Moon,
  Sparkles,
  Sun,
} from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import type { Me } from "../auth/session";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/live", label: "Live View", icon: MonitorPlay },
  { to: "/sessions", label: "Reconciliation", icon: ClipboardCheck },
  { to: "/analysis", label: "Video Analysis", icon: FileVideo },
  { to: "/cameras", label: "Cameras", icon: Camera },
  { to: "/assistant", label: "Assistant", icon: Sparkles },
];

type Theme = "light" | "dark" | null;

const systemDark = () =>
  typeof matchMedia !== "undefined" && matchMedia("(prefers-color-scheme: dark)").matches;

function useTheme(): [boolean, () => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      return (localStorage.getItem("ivaas.theme") as Theme) ?? null;
    } catch {
      return null;
    }
  });
  useEffect(() => {
    const root = document.documentElement;
    if (theme) root.setAttribute("data-theme", theme);
    else root.removeAttribute("data-theme");
    try {
      if (theme) localStorage.setItem("ivaas.theme", theme);
      else localStorage.removeItem("ivaas.theme");
    } catch {
      /* storage blocked: the choice lasts for this page */
    }
  }, [theme]);
  const isDark = theme === "dark" || (theme === null && systemDark());
  return [isDark, () => setTheme(isDark ? "light" : "dark")];
}

export function Layout({
  connected,
  me,
  onLogout,
  children,
}: {
  connected: boolean;
  me: Me | undefined;
  onLogout: () => void;
  children: ReactNode;
}) {
  const [isDark, toggleTheme] = useTheme();

  return (
    <div className="flex min-h-full">
      <aside className="fixed inset-y-0 left-0 hidden w-56 flex-col border-r border-line bg-surface lg:flex">
        <div className="px-5 pb-4 pt-5">
          <img
            src="/logo-liquid.png"
            alt="Liquid Intelligent Technologies"
            className="h-9 dark:brightness-0 dark:invert"
          />
          <div className="eyebrow mt-3">Intelligent Video</div>
        </div>
        <nav className="flex-1 space-y-0.5 px-3">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-semibold transition ${
                  isActive ? "bg-brand-tint text-brand" : "text-muted hover:bg-ground hover:text-ink"
                }`
              }
            >
              {({ isActive }) => (
                <>
                  <span
                    className={`h-5 w-0.5 rounded-full ${isActive ? "bg-accent" : "bg-transparent"}`}
                  />
                  <Icon size={17} strokeWidth={2.2} />
                  {label}
                </>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-line px-5 py-4">
          <div className="truncate text-sm font-semibold text-ink">{me?.name ?? ""}</div>
          <div className="truncate text-xs capitalize text-muted">{me?.roles.join(" · ")}</div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col lg:pl-56">
        <header
          className="sticky z-10 flex h-14 items-center justify-between gap-4 border-b border-line bg-surface/85 px-4 backdrop-blur sm:px-8"
          style={{ top: "env(safe-area-inset-top, 0px)" }}
        >
          <div className="flex min-w-0 items-center gap-3">
            <img
              src="/logo-liquid.png"
              alt="Liquid"
              className="h-6 lg:hidden dark:brightness-0 dark:invert"
            />
            <div className="hidden min-w-0 lg:block">
              <div className="truncate text-sm font-bold text-ink">Bakery Industrial Site</div>
              <div className="truncate text-xs text-muted">Loading Bay</div>
            </div>
          </div>
          <div className="flex items-center gap-1.5">
            <span
              className={`chip mr-1 ${connected ? "bg-good/10 text-good" : "bg-ground text-muted"}`}
              title={connected ? "Receiving live events" : "Reconnecting to the platform"}
            >
              <span className={`dot ${connected ? "animate-pulse bg-good" : "bg-faint"}`} />
              {connected ? "Live" : "Reconnecting"}
            </span>
            <button
              onClick={toggleTheme}
              aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"}
              className="rounded-lg p-2 text-muted transition hover:bg-ground hover:text-ink"
            >
              {isDark ? <Sun size={17} /> : <Moon size={17} />}
            </button>
            <button
              onClick={onLogout}
              aria-label="Sign out"
              title="Sign out"
              className="rounded-lg p-2 text-muted transition hover:bg-ground hover:text-ink"
            >
              <LogOut size={17} />
            </button>
          </div>
        </header>

        <nav className="flex gap-1 overflow-x-auto border-b border-line bg-surface px-2 lg:hidden">
          {NAV.map(({ to, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                `whitespace-nowrap border-b-2 px-3 py-2.5 text-sm font-semibold ${
                  isActive ? "border-accent text-ink" : "border-transparent text-muted"
                }`
              }
            >
              {label}
            </NavLink>
          ))}
        </nav>

        <main className="flex-1 px-4 pb-10 pt-6 sm:px-8">{children}</main>
      </div>
    </div>
  );
}
