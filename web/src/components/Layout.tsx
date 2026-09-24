import {
  Camera,
  ClipboardCheck,
  FileVideo,
  LayoutDashboard,
  LogOut,
  MonitorPlay,
  Moon,
  ShieldCheck,
  Sparkles,
  Sun,
  Warehouse,
} from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import type { Me } from "../auth/session";
import { CrateMotif, RAIL_GRADIENT, RAIL_STACKS } from "./brand";

/** Navigation grouped by what the person is doing, not by page count. */
const NAV = [
  {
    group: "Operations",
    items: [
      { to: "/", label: "Dashboard", icon: LayoutDashboard },
      { to: "/live", label: "Live View", icon: MonitorPlay },
      { to: "/sessions", label: "Reconciliation", icon: ClipboardCheck },
    ],
  },
  {
    group: "Analysis",
    items: [
      { to: "/analysis", label: "Video Analysis", icon: FileVideo },
      { to: "/assistant", label: "Assistant", icon: Sparkles },
    ],
  },
  { group: "Configure", items: [{ to: "/cameras", label: "Cameras", icon: Camera }] },
];
const FLAT = NAV.flatMap((g) => g.items);

const SITE = "Bakery Industrial Site";
const BAY = "Loading Bay";

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

/** Highest role held, which is what "access level" means to the person reading it. */
function accessLevel(me: Me | undefined): string {
  for (const role of ["admin", "operator", "viewer"]) {
    if (me?.roles.includes(role)) {
      return role === "admin" ? "System Administrator" : role === "operator" ? "Bay Operator" : "Viewer";
    }
  }
  return "Signed in";
}

const initials = (name: string) =>
  name
    .split(/[\s._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join("") || "?";

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
  const level = accessLevel(me);

  return (
    <div className="flex min-h-full">
      {/* navigation rail: a lit brand surface, the same in both themes */}
      <aside
        className="fixed inset-y-0 left-0 hidden w-64 flex-col overflow-hidden text-white lg:flex"
        style={{ background: RAIL_GRADIENT }}
      >
        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-72 opacity-90">
          <CrateMotif stacks={RAIL_STACKS} viewBox="0 0 256 300" />
        </div>
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 bg-gradient-to-b from-transparent via-transparent to-[#131d43]/80"
        />

        <div className="relative px-5 pb-5 pt-6">
          <img
            src="/logo-liquid.png"
            alt="Liquid Intelligent Technologies"
            className="h-8 brightness-0 invert"
          />
        </div>

        {/* who you are and what you can do, stated before the menu */}
        <div className="relative mx-4 rounded-xl border border-white/15 bg-white/10 px-3.5 py-3 backdrop-blur-sm">
          <div className="text-[10px] font-bold uppercase tracking-[0.14em] text-white/55">
            Access level
          </div>
          <div className="mt-1 flex items-center gap-1.5 text-sm font-bold">
            <ShieldCheck size={13} className="flex-none text-[#f06ab5]" />
            <span className="truncate">{level}</span>
          </div>
          <div className="mt-2 flex items-center gap-1.5 border-t border-white/10 pt-2 text-[11px] text-white/65">
            <Warehouse size={12} className="flex-none" />
            <span className="truncate">
              {SITE} · {BAY}
            </span>
          </div>
        </div>

        <nav className="relative mt-5 flex-1 space-y-5 overflow-y-auto px-3">
          {NAV.map(({ group, items }) => (
            <div key={group}>
              <div className="px-3 pb-1.5 text-[10px] font-bold uppercase tracking-[0.14em] text-white/40">
                {group}
              </div>
              <div className="space-y-0.5">
                {items.map(({ to, label, icon: Icon }) => (
                  <NavLink
                    key={to}
                    to={to}
                    end={to === "/"}
                    className={({ isActive }) =>
                      `relative flex items-center gap-2.5 rounded-lg py-2 pl-3 pr-3 text-sm font-semibold transition ${
                        isActive
                          ? "bg-white/15 text-white"
                          : "text-white/65 hover:bg-white/8 hover:text-white"
                      }`
                    }
                  >
                    {({ isActive }) => (
                      <>
                        <span
                          className={`absolute left-0 h-5 w-[3px] rounded-r-full transition ${
                            isActive ? "bg-[#f06ab5]" : "bg-transparent"
                          }`}
                        />
                        <Icon size={16} strokeWidth={2.2} />
                        {label}
                      </>
                    )}
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
        </nav>

        <div className="relative border-t border-white/10 bg-[#131d43]/85 px-3 py-3 backdrop-blur-sm">
          <button
            onClick={onLogout}
            className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-semibold text-white/70 transition hover:bg-white/10 hover:text-white"
          >
            <LogOut size={16} strokeWidth={2.2} /> Sign out
          </button>
          <div className="px-3 pt-2 text-[10px] text-white/35">
            Intelligent Video as a Service · v0.1
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col lg:pl-64">
        <header
          className="sticky z-10 flex h-14 items-center justify-between gap-4 border-b border-line bg-surface/85 px-4 backdrop-blur sm:px-6"
          style={{ top: "env(safe-area-inset-top, 0px)" }}
        >
          <div className="flex min-w-0 items-center gap-3">
            <img
              src="/logo-liquid.png"
              alt="Liquid"
              className="h-6 lg:hidden dark:brightness-0 dark:invert"
            />
            <span className="hidden min-w-0 items-center gap-1.5 rounded-lg border border-line bg-ground px-2.5 py-1 lg:inline-flex">
              <Warehouse size={13} className="flex-none text-muted" />
              <span className="truncate text-xs font-semibold text-ink">{SITE}</span>
              <span className="text-xs text-faint">/ {BAY}</span>
            </span>
          </div>

          <div className="flex items-center gap-1.5">
            <span
              className={`chip ${connected ? "bg-good/10 text-good" : "bg-ground text-muted"}`}
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
              className="rounded-lg p-2 text-muted transition hover:bg-ground hover:text-ink lg:hidden"
            >
              <LogOut size={17} />
            </button>
            {/* identity, at the end of the bar where people look for it */}
            <div className="ml-1 flex items-center gap-2 border-l border-line pl-3">
              <span className="grid h-8 w-8 flex-none place-items-center rounded-full bg-brand text-xs font-bold text-white">
                {initials(me?.name ?? "")}
              </span>
              <div className="hidden min-w-0 sm:block">
                <div className="truncate text-xs font-bold leading-tight text-ink">
                  {me?.name ?? ""}
                </div>
                <div className="truncate text-[11px] leading-tight text-muted">{level}</div>
              </div>
            </div>
          </div>
        </header>

        <nav className="flex gap-1 overflow-x-auto border-b border-line bg-surface px-2 lg:hidden">
          {FLAT.map(({ to, label }) => (
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

        <main className="flex-1 px-4 pb-10 pt-5 sm:px-6">{children}</main>
      </div>
    </div>
  );
}
