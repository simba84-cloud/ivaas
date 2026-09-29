/**
 * The motion building blocks pages use. Anything that animates goes through one of
 * these, so timing and the rules in tokens.ts are decided once.
 */
import {
  AnimatePresence,
  LayoutGroup,
  MotionConfig,
  animate,
  motion,
  useReducedMotion,
} from "framer-motion";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { distance, rise, staggerDelay, transition } from "./tokens";

/** Wraps the app: shared defaults, and reduced motion honoured everywhere. */
export function MotionRoot({ children }: { children: ReactNode }) {
  return (
    <MotionConfig reducedMotion="user" transition={transition.normal}>
      {children}
    </MotionConfig>
  );
}

/** Each route rises in when navigated to. Enter only: an exit would hold the old page on screen. */
export function PageTransition({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  return (
    <motion.div key={pathname} initial={{ y: distance.nudge }} animate={{ y: 0 }} transition={transition.slow}>
      {children}
    </motion.div>
  );
}

/** One item in a staggered group: cards on a dashboard, rows in a table. */
export function Rise({
  index = 0,
  children,
  className,
  as = "div",
}: {
  index?: number;
  children: ReactNode;
  className?: string;
  as?: "div" | "li" | "section" | "article";
}) {
  const Tag = motion[as];
  return (
    <Tag
      className={className}
      variants={rise}
      initial="initial"
      animate="animate"
      transition={{ ...transition.normal, delay: staggerDelay(index) }}
    >
      {children}
    </Tag>
  );
}

/** A table row that rises in; `index` staggers the first screenful and no more. */
export function MotionRow({
  index = 0,
  children,
  className,
}: {
  index?: number;
  children: ReactNode;
  className?: string;
}) {
  return (
    <motion.tr
      layout="position"
      className={className}
      initial={{ y: distance.nudge }}
      animate={{ y: 0 }}
      transition={{ ...transition.normal, delay: staggerDelay(index) }}
    >
      {children}
    </motion.tr>
  );
}

/**
 * A number that glides from its old value to its new one.
 *
 * It never counts up from zero: the first paint shows the real figure, because a
 * screen caught mid-count would show a number that was never measured. When the
 * tab is hidden (no animation frames) or motion is reduced, it jumps straight
 * to the value. Screen readers always get the final value.
 */
export function AnimatedNumber({
  value,
  format = (n) => Math.round(n).toLocaleString(),
  className,
}: {
  value: number;
  format?: (n: number) => string;
  className?: string;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const shown = useRef(value);
  const still = useReducedMotion();

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const from = shown.current;
    shown.current = value;
    if (from === value || still || document.hidden) {
      el.textContent = format(value);
      return;
    }
    const controls = animate(from, value, {
      ...transition.slow,
      onUpdate: (v) => {
        el.textContent = format(v);
      },
      onComplete: () => {
        el.textContent = format(value);
      },
    });
    return () => {
      controls.stop();
      el.textContent = format(value);
    };
    // format is stable in meaning; the value is what drives this
  }, [value, still]);

  return (
    <span className={className} aria-label={format(value)}>
      <span ref={ref} aria-hidden>
        {format(value)}
      </span>
    </span>
  );
}

/** A segmented control whose selection pill slides between options. */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
  size = "sm",
}: {
  options: { value: T; label: ReactNode; title?: string; count?: number }[];
  value: T;
  onChange: (v: T) => void;
  label: string;
  size?: "sm" | "md";
}) {
  const id = useId();
  return (
    <LayoutGroup id={id}>
      <div className="segment" role="group" aria-label={label}>
        {options.map((o) => {
          const on = o.value === value;
          return (
            <button
              key={o.value}
              type="button"
              aria-pressed={on}
              aria-label={o.title}
              title={o.title}
              onClick={() => onChange(o.value)}
              className={`segment-item relative ${size === "md" ? "h-8 px-3" : ""} ${on ? "text-ink" : ""}`}
            >
              {on && (
                <motion.span
                  layoutId="segment-pill"
                  className="absolute inset-0 rounded-md bg-surface shadow-card"
                  transition={transition.spring}
                />
              )}
              <span className="relative inline-flex items-center gap-1.5">
                {o.label}
                {o.count !== undefined && <span className="num text-faint">{o.count}</span>}
              </span>
            </button>
          );
        })}
      </div>
    </LayoutGroup>
  );
}

/** Shows `children` once `ready`; until then a skeleton the same shape. Swaps with a rise. */
export function Reveal({
  ready,
  skeleton,
  children,
}: {
  ready: boolean;
  skeleton: ReactNode;
  children: ReactNode;
}) {
  // a value that was ready on first paint needs no entrance
  const [wasReady] = useState(ready);
  return (
    <AnimatePresence mode="popLayout" initial={false}>
      {ready ? (
        <motion.div key="ready" initial={wasReady ? false : { y: distance.nudge }} animate={{ y: 0 }}>
          {children}
        </motion.div>
      ) : (
        <motion.div key="skeleton" exit={{ y: -distance.nudge }} transition={transition.fast}>
          {skeleton}
        </motion.div>
      )}
    </AnimatePresence>
  );
}

/**
 * A progress bar that moves by transform, not width, so it never re-lays out the
 * page. While work is running, a highlight travels along the filled part.
 */
export function Progress({ value, active = true, label }: { value: number; active?: boolean; label: string }) {
  const v = Math.max(0, Math.min(1, value));
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(v * 100)}
      className="h-2 overflow-hidden rounded-full bg-line"
    >
      <div
        className={`h-full origin-left rounded-full bg-accent transition-transform duration-500 ease-out ${active ? "skeleton-bar" : ""}`}
        style={{ transform: `scaleX(${v})` }}
      />
    </div>
  );
}
