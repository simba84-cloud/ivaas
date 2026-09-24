/**
 * The brand surfaces: the gradient and the crate motif drawn on it.
 *
 * These surfaces are deliberately single-theme. Like the video wells, a lit brand
 * panel reads the same in light and dark, so its colours are literals rather than
 * tokens and its chrome is always light.
 */

export const BRAND_GRADIENT =
  "linear-gradient(150deg, #16234f 0%, #273c87 32%, #5b2a8c 62%, #a3187f 84%, #c8187d 100%)";

/** The quieter version, for large chrome like the navigation rail. */
export const RAIL_GRADIENT =
  "linear-gradient(170deg, #131d43 0%, #1b2a60 45%, #2b2668 75%, #4a1f63 100%)";

/** One crate: an overhanging lip it stacks on, and a cut-out handle at each end. */
function Crate({ x, y, w, h, o }: { x: number; y: number; w: number; h: number; o: number }) {
  const lip = w * 0.035;
  const slotW = w * 0.16;
  return (
    <g>
      <rect
        x={x}
        y={y}
        width={w}
        height={h}
        rx={6}
        fill="#fff"
        fillOpacity={o}
        stroke="#fff"
        strokeOpacity={o * 2.6}
        strokeWidth={1.5}
      />
      {/* the lip overhangs the body: what makes a stack read as a stack */}
      <rect
        x={x - lip}
        y={y - h * 0.06}
        width={w + lip * 2}
        height={h * 0.17}
        rx={4}
        fill="#fff"
        fillOpacity={o * 1.7}
        stroke="#fff"
        strokeOpacity={o * 2.2}
        strokeWidth={1}
      />
      {[x + w * 0.11, x + w * 0.73].map((sx) => (
        <rect
          key={sx}
          x={sx}
          y={y + h * 0.45}
          width={slotW}
          height={h * 0.22}
          rx={3}
          fill="#0d1636"
          fillOpacity={0.35}
        />
      ))}
    </g>
  );
}

type Stack = { x: number; base: number; w: number; h: number; n: number; o: number };

/** Stacks of crates: the thing this platform counts, as the panel's own motif. */
export function CrateMotif({
  stacks,
  viewBox = "0 0 640 960",
}: {
  stacks: Stack[];
  viewBox?: string;
}) {
  return (
    <svg
      aria-hidden
      className="absolute inset-0 h-full w-full"
      viewBox={viewBox}
      preserveAspectRatio="xMidYMax slice"
      fill="none"
    >
      {stacks.map((s) => (
        <g key={`${s.x}-${s.base}`}>
          {Array.from({ length: s.n }, (_, i) => (
            <Crate
              key={i}
              // hand-loaded stacks lean a little
              x={s.x + Math.round(Math.sin(i * 0.8) * (s.w * 0.012))}
              y={s.base - (i + 1) * (s.h + 2)}
              w={s.w}
              h={s.h}
              o={s.o}
            />
          ))}
        </g>
      ))}
    </svg>
  );
}

/** Few, large crates: the login panel. */
export const LOGIN_STACKS: Stack[] = [
  { x: 296, base: 992, w: 300, h: 86, n: 6, o: 0.09 },
  { x: 92, base: 1004, w: 250, h: 74, n: 4, o: 0.06 },
  { x: 470, base: 560, w: 210, h: 62, n: 3, o: 0.05 },
];

/** Narrow and faint: the foot of the navigation rail. */
export const RAIL_STACKS: Stack[] = [
  { x: 18, base: 300, w: 120, h: 34, n: 5, o: 0.05 },
  { x: 150, base: 300, w: 96, h: 28, n: 3, o: 0.035 },
];
