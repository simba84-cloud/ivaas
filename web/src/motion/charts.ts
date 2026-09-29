/**
 * Chart entrances.
 *
 * Recharts grows bars from the axis and draws lines left to right, which starts
 * every value at zero. That is only acceptable if the animation will certainly
 * finish: a chart frozen at its first frame reads as "no activity", a figure the
 * platform never reported. So the entrance runs only when the page is visible at
 * the moment the chart mounts and the user has not asked for reduced motion;
 * otherwise the chart simply appears, complete.
 */
import { useReducedMotion } from "framer-motion";
import { useState } from "react";

export function useChartEntrance(durationMs = 700) {
  const still = useReducedMotion();
  // decided once per mount: a later tab switch must not re-trigger or strand it
  const [visible] = useState(() => typeof document !== "undefined" && document.visibilityState === "visible");
  const active = visible && !still;
  return {
    isAnimationActive: active,
    animationDuration: durationMs,
    animationEasing: "ease-out" as const,
  };
}
