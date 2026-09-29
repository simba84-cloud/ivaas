/**
 * Loading placeholders. A skeleton says "this is coming" in the shape of what is
 * coming; it never says "nothing here", which is what an empty state shown during
 * a load used to say by mistake.
 *
 * The shimmer moves a highlight by transform (see `.skeleton` in index.css), so it
 * costs no layout, and it stops under reduced motion.
 */
export function Skeleton({ className = "" }: { className?: string }) {
  return <span aria-hidden className={`skeleton block ${className}`} />;
}

/** Stands in for a table body while its rows load. */
export function SkeletonRows({ rows = 5, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <tbody aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, r) => (
        <tr key={r} className="border-t border-line">
          {Array.from({ length: cols }, (_, c) => (
            <td key={c} className="td">
              <Skeleton className={`h-3.5 ${c === 0 ? "w-24" : c === cols - 1 ? "w-12" : "w-full max-w-[9rem]"}`} />
            </td>
          ))}
        </tr>
      ))}
    </tbody>
  );
}

/** Stands in for a list of feed or card items. */
export function SkeletonList({ rows = 4 }: { rows?: number }) {
  return (
    <div aria-busy="true" aria-label="Loading" className="space-y-3 p-4">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex gap-3">
          <Skeleton className="mt-1 h-2.5 w-2.5 rounded-full" />
          <div className="flex-1 space-y-1.5">
            <Skeleton className="h-3.5 w-2/3" />
            <Skeleton className="h-3 w-full max-w-sm" />
          </div>
        </div>
      ))}
    </div>
  );
}

/** Stands in for a headline figure. */
export function SkeletonFigure({ className = "" }: { className?: string }) {
  return <Skeleton className={`h-8 w-24 rounded-md ${className}`} />;
}
