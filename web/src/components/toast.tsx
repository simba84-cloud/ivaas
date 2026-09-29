/**
 * Toasts: brief word that something happened somewhere else, or that an action
 * succeeded. They stack at the bottom right and slide in and out from that edge.
 *
 * A critical toast stays until dismissed, because a fault nobody saw is the
 * dangerous case; the rest leave on their own. Every toast is announced: critical
 * ones assertively, the rest politely.
 */
import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, CheckCircle2, Info, OctagonAlert, X } from "lucide-react";
import { type ReactNode, createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import { slideIn, transition } from "../motion";

export type ToastSeverity = "critical" | "warning" | "info" | "success";

export interface Toast {
  id: number;
  severity: ToastSeverity;
  title: string;
  detail?: string;
  action?: { label: string; onClick: () => void };
}

type Push = (t: Omit<Toast, "id">) => void;
const ToastContext = createContext<Push>(() => {});

const LIFETIME: Record<ToastSeverity, number | null> = {
  critical: null,
  warning: 9000,
  info: 6000,
  success: 5000,
};
const MAX = 4;

const LOOK: Record<ToastSeverity, { icon: typeof Info; tone: string }> = {
  critical: { icon: OctagonAlert, tone: "text-bad" },
  warning: { icon: AlertTriangle, tone: "text-warn" },
  info: { icon: Info, tone: "text-brand" },
  success: { icon: CheckCircle2, tone: "text-good" },
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(1);
  const timers = useRef(new Map<number, number>());

  const dismiss = useCallback((id: number) => {
    window.clearTimeout(timers.current.get(id));
    timers.current.delete(id);
    setToasts((ts) => ts.filter((t) => t.id !== id));
  }, []);

  const push = useCallback<Push>(
    (t) => {
      const id = next.current++;
      setToasts((ts) => [...ts, { ...t, id }].slice(-MAX));
      const life = LIFETIME[t.severity];
      if (life) timers.current.set(id, window.setTimeout(() => dismiss(id), life));
    },
    [dismiss],
  );

  const variants = useMemo(() => slideIn("right"), []);

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div
        className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2"
        style={{ marginBottom: "env(safe-area-inset-bottom, 0px)" }}
      >
        <AnimatePresence initial={false}>
          {toasts.map((t) => {
            const { icon: Icon, tone } = LOOK[t.severity];
            return (
              <motion.div
                key={t.id}
                layout
                variants={variants}
                initial="initial"
                animate="animate"
                exit="exit"
                transition={transition.spring}
                role={t.severity === "critical" ? "alert" : "status"}
                aria-live={t.severity === "critical" ? "assertive" : "polite"}
                className="pointer-events-auto flex items-start gap-3 rounded-xl border border-line bg-raised p-3.5 shadow-lift"
              >
                <span className={`relative mt-0.5 ${tone}`}>
                  <Icon size={17} />
                  {t.severity === "critical" && <span aria-hidden className="ping absolute inset-0 rounded-full" />}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-bold text-ink">{t.title}</div>
                  {t.detail && <div className="mt-0.5 text-xs text-muted">{t.detail}</div>}
                  {t.action && (
                    <button
                      className="mt-2 text-xs font-bold text-brand hover:underline"
                      onClick={() => {
                        t.action!.onClick();
                        dismiss(t.id);
                      }}
                    >
                      {t.action.label}
                    </button>
                  )}
                </div>
                <button
                  onClick={() => dismiss(t.id)}
                  aria-label="Dismiss"
                  className="rounded-md p-1 text-faint transition hover:bg-ground hover:text-ink"
                >
                  <X size={14} />
                </button>
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);
