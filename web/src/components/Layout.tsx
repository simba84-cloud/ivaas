import { Camera, ClipboardCheck, LayoutDashboard, LogOut, MonitorPlay, Sparkles } from "lucide-react";
import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import type { Me } from "../auth/session";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/live", label: "Live View", icon: MonitorPlay },
  { to: "/sessions", label: "Reconciliation", icon: ClipboardCheck },
  { to: "/cameras", label: "Cameras", icon: Camera },
  { to: "/assistant", label: "Assistant", icon: Sparkles },
];

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
  return (
    <div className="flex min-h-screen">
      <aside className="fixed inset-y-0 left-0 hidden w-64 flex-col border-r border-slate-200 bg-white lg:flex">
        <div className="border-b border-slate-100 px-6 py-5">
          <img src="/logo-liquid.png" alt="Liquid Intelligent Technologies" className="h-10" />
        </div>
        <div className="px-6 pb-2 pt-5 text-[11px] font-semibold uppercase tracking-widest text-slate-400">
          Intelligent Video
        </div>
        <nav className="flex-1 space-y-1 px-3">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition ${
                  isActive
                    ? "bg-brand-navy text-white shadow-sm"
                    : "text-slate-600 hover:bg-brand-navy-tint hover:text-brand-navy"
                }`
              }
            >
              <Icon size={18} strokeWidth={2} />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-slate-100 px-6 py-4 text-xs text-slate-400">
          IVaaS Platform · v0.1.0
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col lg:pl-64">
        <header className="sticky top-0 z-10 flex h-16 items-center justify-between border-b border-slate-200 bg-white/90 px-4 backdrop-blur sm:px-8">
          <div className="flex items-center gap-3">
            <img src="/logo-liquid.png" alt="Liquid" className="h-7 lg:hidden" />
            <div className="hidden lg:block">
              <div className="text-sm font-semibold text-brand-navy">
                Demo Bakery Industrial Site
              </div>
              <div className="text-xs text-slate-500">POC Loading Bay · Crate reconciliation</div>
            </div>
          </div>
          <div className="flex items-center gap-3">
          <div
            className={`flex items-center gap-2 rounded-full px-3 py-1 text-xs font-semibold ${
              connected ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-500"
            }`}
          >
            <span
              className={`h-2 w-2 rounded-full ${
                connected ? "animate-pulse bg-emerald-500" : "bg-slate-400"
              }`}
            />
            {connected ? "Live" : "Reconnecting"}
          </div>
          <div className="hidden text-right sm:block">
            <div className="text-sm font-semibold text-brand-navy">{me?.name ?? ""}</div>
            <div className="text-xs capitalize text-slate-500">{me?.roles.join(", ")}</div>
          </div>
          <button
            onClick={onLogout}
            aria-label="Sign out"
            title="Sign out"
            className="rounded-lg p-2 text-slate-500 hover:bg-brand-navy-tint hover:text-brand-navy"
          >
            <LogOut size={18} />
          </button>
          </div>
        </header>

        <nav className="flex gap-1 overflow-x-auto border-b border-slate-200 bg-white px-2 lg:hidden">
          {NAV.map(({ to, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                `whitespace-nowrap border-b-2 px-3 py-3 text-sm font-medium ${
                  isActive
                    ? "border-brand-magenta text-brand-navy"
                    : "border-transparent text-slate-500"
                }`
              }
            >
              {label}
            </NavLink>
          ))}
        </nav>

        <main className="flex-1 px-4 py-6 sm:px-8">{children}</main>
      </div>
    </div>
  );
}
