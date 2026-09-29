/**
 * Motion tokens: the only timings and curves the portal uses.
 *
 * Rules, each learned the hard way (see CLAUDE.md):
 *
 * 1. Content enters by moving, never by fading in from nothing. Something parked
 *    at opacity 0 is invisible when the animation does not run, and a hidden tab
 *    runs no animation frames at all.
 * 2. Every resting state must be true on its own. If an animation freezes at its
 *    first frame the screen must still be correct: a chart does not start at zero
 *    unless it will certainly finish, a counter does not start at zero, a figure is
 *    never clipped mid-rise.
 * 3. Move transform only. Width, height, top and left cause layout on every frame.
 * 4. `MotionRoot` sets reducedMotion="user", so a user who asks for less motion
 *    gets no transform or layout animation anywhere, with no per-component checks.
 */
import type { Transition, Variants } from "framer-motion";

/** The portal's one easing: quick out of the gate, gentle landing. Matches `ease-out` in Tailwind. */
export const EASE_OUT = [0.2, 0.7, 0.2, 1] as const;

export const duration = {
  fast: 0.15,
  normal: 0.25,
  slow: 0.4,
} as const;

/** Standard transitions. Springs are for things that move a distance; tweens for small nudges. */
export const transition = {
  fast: { duration: duration.fast, ease: EASE_OUT },
  normal: { duration: duration.normal, ease: EASE_OUT },
  slow: { duration: duration.slow, ease: EASE_OUT },
  spring: { type: "spring", bounce: 0.15, duration: 0.45 },
  elastic: { type: "spring", bounce: 0.35, duration: 0.6 },
} satisfies Record<string, Transition>;

/** How far entrances travel. Small: this is an operations console, not a landing page. */
export const distance = { nudge: 6, rise: 12, slide: 24 } as const;

/** Rise into place. The default entrance for cards, rows and panels. */
export const rise: Variants = {
  initial: { y: distance.rise },
  animate: { y: 0, transition: transition.normal },
};

/** Arrive from the side: feeds and toasts, where new things come from one edge. */
export const slideIn = (from: "left" | "right" = "right"): Variants => ({
  initial: { x: from === "right" ? distance.slide : -distance.slide },
  animate: { x: 0, transition: transition.spring },
  exit: { x: from === "right" ? distance.slide * 2 : -distance.slide * 2, transition: transition.fast },
});

/** Stagger children by this much, capped so a long list never takes long to settle. */
export const STAGGER = 0.04;
export const MAX_STAGGERED = 12;
export const staggerDelay = (index: number) => Math.min(index, MAX_STAGGERED) * STAGGER;

export const list: Variants = {
  initial: {},
  animate: { transition: { staggerChildren: STAGGER, delayChildren: 0.02 } },
};
