# Motion

Every animation in the portal goes through this folder, so timing is decided once
and the rules below hold everywhere.

## Rules

1. **Enter by moving, never by fading in from nothing.** Content parked at
   `opacity: 0` is invisible whenever the animation does not run, and a hidden
   browser tab runs no animation frames at all.
2. **Every resting state must be true on its own.** If an animation freezes on its
   first frame, the screen must still be correct:
   - counters never start at zero (`AnimatedNumber` paints the real value first);
   - chart bars grow from the axis only when the page is visible at mount
     (`useChartEntrance`);
   - animated figures are never clipped, so a figure frozen mid-rise still reads.
3. **Move `transform` only.** Width, height, top and left cause layout on every
   frame. Progress bars scale, skeleton shimmers translate, and tiles re-order
   with `layout` (a transform-based FLIP).
4. **Reduced motion is global.** `MotionRoot` sets `reducedMotion="user"`, so
   transform and layout animation stops everywhere for people who ask, and the CSS
   animations stop under the rule in `index.css`.
5. **An exit must not be able to strand content.** Leave out an exit wrapper
   (`AnimatePresence`) when an element hands over to a shared `layoutId`: the
   hand-over already animates, and an exit waiting on it can leave a stale panel
   behind.

## Tokens (`tokens.ts`)

| Preset | Use |
| --- | --- |
| `transition.fast` (150 ms) | small state changes: chevrons, exits |
| `transition.normal` (250 ms) | the default: rows, cards, panels |
| `transition.slow` (400 ms) | page entrances, number glides |
| `transition.spring` | things that travel: tabs, tiles, toasts, feed items |
| `transition.elastic` | a deliberate pop: badges, "used" chips |

Distances are small on purpose (`nudge` 6 px, `rise` 12 px, `slide` 24 px): this
is an operations console. Staggers are capped at 12 items (`staggerDelay`), so a
long list never takes long to settle.

## Components

| Component | Does |
| --- | --- |
| `MotionRoot` | app-wide defaults and reduced motion |
| `PageTransition` | each route rises in (enter only) |
| `Rise`, `MotionRow` | staggered entrance for cards, list items and table rows |
| `AnimatedNumber` | glides between real values; jumps when hidden or reduced |
| `Segmented` | tabs and filters whose selection pill slides (`layoutId`) |
| `Reveal` | swaps a skeleton for content with a rise |
| `Progress` | a progress bar that scales rather than resizes |
| `Skeleton`, `SkeletonRows`, `SkeletonList`, `SkeletonFigure` | loading placeholders in the shape of what is coming |
| `useChartEntrance` | Recharts animation props, on only when it will finish |

Toasts live in `components/toast.tsx` and use the same presets.

## Loading, not empty

While a query is pending, show a skeleton. An empty state means "there is nothing",
which is a claim; saying it before the data has arrived is how pages used to say
"Nothing to reconcile yet" or "0 crates" while still loading.
