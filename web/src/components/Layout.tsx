import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import {
  Bell,
  Camera,
  Cpu,
  FileWarning,
  Scale,
  Truck,
  ClipboardCheck,
  ClipboardList,
  ScrollText,
  Target,
  SlidersHorizontal,
  Users,
  FileVideo,
  LayoutDashboard,
  LogOut,
  MonitorPlay,
  Moon,
  Radar,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Sun,
  Warehouse,
} from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { type Me, type Permission, can } from "../auth/session";
import { useScope } from "../api/scope";
import { api } from "../api/client";
import { useAlerts } from "../live/alerts";
import { PageTransition, transition } from "../motion";
import { CrateMotif, RAIL_GRADIENT, RAIL_STACKS } from "./brand";

/**
 * Which bay the portal is showing. A single-bay deployment gets a plain label,
 * because a dropdown with one option is a decision nobody has to make.
 */
function BayPicker() {
  const { bays, bay, site, sites, multi, setBay } = useScope();
  const siteName = (id: string) => sites.find((s) => s.id === id)?.name ?? "Site";

  if (!bay) return null;
  if (!multi) {
    return (
      <span className="hidden min-w-0 items-center gap-1.5 rounded-lg border border-line bg-ground px-2.5 py-1 lg:inline-flex">
        <Warehouse size={13} className="flex-none text-muted" />
        <span className="truncate text-xs font-semibold text-ink">{site?.name ?? "Site"}</span>
        <span className="text-xs text-faint">/ {bay.name}</span>
      </span>
    );
  }
  return (
    <span className="hidden min-w-0 items-center gap-1.5 rounded-lg border border-line bg-ground pl-2.5 lg:inline-flex">
      <Warehouse size={13} className="flex-none text-muted" />
      <select
        id="bay-picker"
        aria-label="Bay"
        className="max-w-[18rem] truncate border-0 bg-transparent py-1 pr-2 text-xs font-semibold text-ink outline-none"
        value={bay.id}
        onChange={(e) => setBay(e.target.value)}
      >
        {bays.map((b) => (
          <option key={b.id} value={b.id}>
            {siteName(b.site_id)} / {b.name}
          </option>
        ))}
      </select>
    </span>
  );
}

/** Navigation grouped by what the person is doing, not by page count. */
const NAV = [
  {
    group: "Operations",
    items: [
      { to: "/", label: "Dashboard", icon: LayoutDashboard },
      { to: "/command", label: "Command", icon: Radar },
      { to: "/alerts", label: "Alerts", icon: Bell, badge: true },
      { to: "/security", label: "Security", icon: ShieldAlert, incidents: true },
      { to: "/live", label: "Live View", icon: MonitorPlay },
      { to: "/sessions", label: "Reconciliation", icon: ClipboardCheck },
      { to: "/tally", label: "Tally Sheets", icon: ClipboardList, needs: "groundtruth.enter" },
      { to: "/accuracy", label: "Accuracy", icon: Target },
      { to: "/exceptions", label: "Exceptions", icon: FileWarning },
      { to: "/balances", label: "Balances", icon: Scale },
    ],
  },
  {
    group: "Analysis",
    items: [
      { to: "/analysis", label: "Video Analysis", icon: FileVideo },
      { to: "/assistant", label: "Assistant", icon: Sparkles },
    ],
  },
  {
    group: "Configure",
    items: [
      { to: "/cameras", label: "Cameras", icon: Camera },
      { to: "/fleet", label: "Fleet", icon: Truck },
      { to: "/edge", label: "Edge Nodes", icon: Cpu, needs: "device.register" },
      { to: "/users", label: "Users", icon: Users, needs: "user.manage" },
      { to: "/audit", label: "Audit Log", icon: ScrollText, needs: "audit.read" },
      { to: "/settings", label: "Settings", icon: SlidersHorizontal, needs: "settings.manage" },
    ],
  },
];
/** A page someone cannot use is not advertised to them: they would only meet a refusal. */
const visible = (me: Me | undefined) =>
  NAV.map((g) => ({
    ...g,
    items: g.items.filter((i) => !("needs" in i && i.needs) || can(me, i.needs as Permission)),
  })).filter((g) => g.items.length);

/** Unacknowledged faults and warnings at this bay; pulses while any is critical. */
function AlertBadge({ compact = false }: { compact?: boolean }) {
  const { urgent, critical } = useAlerts();
  if (!urgent) return null;
  return (
    <motion.span
      key={urgent}
      initial={{ scale: 0.6 }}
      animate={{ scale: 1 }}
      transition={transition.elastic}
      aria-label={`${urgent} alert${urgent === 1 ? "" : "s"} need attention`}
      className={`num relative grid min-w-[1.25rem] place-items-center rounded-full px-1.5 text-[10.5px] font-bold text-white ${
        critical ? "bg-bad" : "bg-warn"
      } ${compact ? "h-4" : "h-5"}`}
    >
      {critical && <span aria-hidden className="ping absolute inset-0 rounded-full text-bad" />}
      <span className="relative">{urgent}</span>
    </motion.span>
  );
}

/** Open security incidents at this bay; pulses while any is fire, smoke or intrusion. */
function IncidentBadge({ compact = false }: { compact?: boolean }) {
  const { bay } = useScope();
  const open = useQuery({
    queryKey: ["incidents", bay?.id, "open"],
    queryFn: () => api.incidents({ bayId: bay?.id, status: "open", days: 30 }),
    enabled: !!bay,
  });
  const n = open.data?.length ?? 0;
  if (!n) return null;
  const critical = open.data!.some((i) => i.kind === "fire" || i.kind === "smoke" || i.kind === "intrusion");
  return (
    <motion.span
      key={n}
      initial={{ scale: 0.6 }}
      animate={{ scale: 1 }}
      transition={transition.elastic}
      aria-label={`${n} open security incident${n === 1 ? "" : "s"}`}
      className={`num relative grid min-w-[1.25rem] place-items-center rounded-full px-1.5 text-[10.5px] font-bold text-white ${
        critical ? "bg-bad" : "bg-warn"
      } ${compact ? "h-4" : "h-5"}`}
    >
      {critical && <span aria-hidden className="ping absolute inset-0 rounded-full text-bad" />}
      <span className="relative">{n}</span>
    </motion.span>
  );
}

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

/** Proposal §4.1 roles, most senior first: the first one held is the "access level". */
const ROLE_LABELS: [string, string][] = [
  ["platform_admin", "Platform Administrator"],
  ["partner_admin", "Partner Administrator"],
  ["tenant_owner", "Tenant Owner"],
  ["tenant_admin", "Tenant Administrator"],
  ["site_manager", "Site Manager"],
  ["bay_operator", "Bay Operator"],
  ["auditor", "Auditor"],
  ["partner_installer", "Installer"],
  ["integration", "Integration"],
];

function accessLevel(me: Me | undefined): string {
  const held = ROLE_LABELS.find(([role]) => me?.roles.includes(role));
  const level = held ? held[1] : "Signed in";
  // which customer this is matters once there is more than one
  return me?.tenant ? `${level} · ${me.tenant.name}` : level;
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
  const nav = visible(me);
  const { bay, site } = useScope();
  const where = bay ? `${site?.name ?? "Site"} · ${bay.name}` : "No bay configured";

  return (
    <div className="flex min-h-full">
      {/* navigation rail: a lit brand surface, the same in both themes */}
      <aside
        className="fixed inset-y-0 left-0 hidden w-64 flex-col overflow-hidden text-white lg:flex"
        style={{ background: RAIL_GRADIENT }}
      >
        {/* decoration only: on a short window it would sit behind the menu, so it goes */}
        <div className="pointer-events-none absolute inset-x-0 bottom-0 hidden h-72 opacity-90 [@media(min-height:960px)]:block">
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
            <span className="truncate">{where}</span>
          </div>
        </div>

        <nav className="relative mt-5 flex-1 space-y-5 overflow-y-auto px-3">
          {nav.map(({ group, items }) => (
            <div key={group}>
              <div className="px-3 pb-1.5 text-[10px] font-bold uppercase tracking-[0.14em] text-white/40">
                {group}
              </div>
              <div className="space-y-0.5">
                {items.map(({ to, label, icon: Icon, ...rest }) => (
                  <NavLink
                    key={to}
                    to={to}
                    end={to === "/"}
                    className={({ isActive }) =>
                      `group relative flex items-center gap-2.5 rounded-lg py-2 pl-3 pr-3 text-sm font-semibold transition ${
                        isActive ? "text-white" : "text-white/65 hover:bg-white/8 hover:text-white"
                      }`
                    }
                  >
                    {({ isActive }) => (
                      <>
                        {/* one highlight that slides to the page you chose; the rail is
                            single-theme, so its literals are deliberate */}
                        {isActive && (
                          <motion.span
                            layoutId="rail-active"
                            transition={transition.spring}
                            className="absolute inset-0 rounded-lg bg-white/15"
                          >
                            <span className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-[#f06ab5]" />
                          </motion.span>
                        )}
                        <Icon
                          size={16}
                          strokeWidth={2.2}
                          className="relative transition-transform duration-200 ease-out group-hover:scale-110"
                        />
                        <span className="relative flex-1">{label}</span>
                        {"badge" in rest && <AlertBadge />}
                        {"incidents" in rest && <IncidentBadge />}
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
            <BayPicker />
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
              <NavLink
                to="/account/password"
                title="Change your password"
                className="hidden min-w-0 rounded-md px-1 py-0.5 transition hover:bg-ground sm:block"
              >
                <div className="truncate text-xs font-bold leading-tight text-ink">
                  {me?.name ?? ""}
                </div>
                <div className="truncate text-[11px] leading-tight text-muted">{level}</div>
              </NavLink>
            </div>
          </div>
        </header>

        <nav className="flex gap-1 overflow-x-auto border-b border-line bg-surface px-2 lg:hidden">
          {nav.flatMap((g) => g.items).map(({ to, label, ...rest }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                `relative flex items-center gap-1.5 whitespace-nowrap px-3 py-2.5 text-sm font-semibold transition-colors ${
                  isActive ? "text-ink" : "text-muted hover:text-ink"
                }`
              }
            >
              {({ isActive }) => (
                <>
                  {label}
                  {"badge" in rest && <AlertBadge compact />}
                  {"incidents" in rest && <IncidentBadge compact />}
                  {isActive && (
                    <motion.span
                      layoutId="mobile-active"
                      transition={transition.spring}
                      className="absolute inset-x-2 bottom-0 h-0.5 rounded-full bg-accent"
                    />
                  )}
                </>
              )}
            </NavLink>
          ))}
        </nav>

        <main className="flex-1 px-4 pb-10 pt-5 sm:px-6">
          <PageTransition>{children}</PageTransition>
        </main>
      </div>
    </div>
  );
}
