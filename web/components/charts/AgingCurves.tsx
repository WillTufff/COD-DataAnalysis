// Three aging curves on one axis, with the peak each of them locates.
//
// The finding is a disagreement, and a table of three peak ages hides it: three
// numbers a year apart read as three estimates of one thing, while three curves
// drawn together read as one curve and two corrections to it. The naive fit sits
// visibly to the right of the two within-player fits, which is the survivorship
// bias, and the delta and retention curves sit on top of each other, which is
// the null the retention weighting produced.
//
// Curves are centred on their own mean, because a within-player change says
// nothing about the level it started from. Only the shape is comparable, so the
// y axis carries no numbers.

export type AgingFitCurve = {
  fit: string;
  peak: number | null;
  peakLo: number | null;
  peakHi: number | null;
  points: { x: number; y: number }[];
};

const STROKE: Record<string, string> = {
  naive: "var(--baseline)",
  delta: "var(--series-1)",
  retention: "var(--series-2)",
};

const DASH: Record<string, string | undefined> = {
  naive: "4 3",
};

export function AgingCurves({
  curves,
  intervalLo,
  intervalHi,
  caption,
  width = 460,
}: {
  curves: AgingFitCurve[];
  intervalLo: number | null;
  intervalHi: number | null;
  caption: string;
  width?: number;
}) {
  const drawn = curves.filter((c) => c.points.length > 1);
  if (drawn.length === 0) return null;

  const M = { left: 16, right: 14, top: 14, bottom: 40 };
  const H = 250;
  const iw = width - M.left - M.right;
  const ih = H - M.top - M.bottom;

  const xs = drawn.flatMap((c) => c.points.map((p) => p.x));
  const ys = drawn.flatMap((c) => c.points.map((p) => p.y));
  const xMin = Math.min(...xs);
  const xMax = Math.max(...xs);
  const yMin = Math.min(...ys);
  const yMax = Math.max(...ys);
  const span = yMax - yMin || 1;

  const x = (v: number) => M.left + ((v - xMin) / (xMax - xMin || 1)) * iw;
  const y = (v: number) => M.top + (1 - (v - yMin) / span) * ih;

  const path = (points: { x: number; y: number }[]) =>
    points.map((p, i) => `${i === 0 ? "M" : "L"}${x(p.x)} ${y(p.y)}`).join(" ");

  const ticks: number[] = [];
  for (let age = Math.ceil(xMin); age <= Math.floor(xMax); age += 2) ticks.push(age);

  return (
    <figure>
      <svg
        viewBox={`0 0 ${width} ${H}`}
        className="w-full"
        role="img"
        aria-label={caption}
      >
        {/* The published peak: the union of every fit's interval. Drawn as a
            band because that is the claim, and the three vertical marks inside
            it are the point estimates it spans. */}
        {intervalLo !== null && intervalHi !== null && (
          <rect
            x={x(intervalLo)}
            y={M.top}
            width={Math.max(x(intervalHi) - x(intervalLo), 1)}
            height={ih}
            fill="var(--series-1)"
            opacity={0.08}
          />
        )}

        {ticks.map((age) => (
          <text
            key={age}
            x={x(age)}
            y={H - 24}
            textAnchor="middle"
            fontSize={9.5}
            fill="var(--ink-muted)"
            className="font-mono"
          >
            {age}
          </text>
        ))}

        <line
          x1={M.left}
          x2={M.left + iw}
          y1={M.top + ih}
          y2={M.top + ih}
          stroke="var(--hairline)"
        />

        {drawn.map((c) => (
          <g key={c.fit}>
            <path
              d={path(c.points)}
              fill="none"
              stroke={STROKE[c.fit] ?? "var(--ink-muted)"}
              strokeWidth={1.75}
              strokeDasharray={DASH[c.fit]}
            />
            {c.peak !== null && (
              <line
                x1={x(c.peak)}
                x2={x(c.peak)}
                y1={M.top + ih}
                y2={M.top + ih - 10}
                stroke={STROKE[c.fit] ?? "var(--ink-muted)"}
                strokeWidth={1.75}
              >
                <title>{`${c.fit}: peak ${c.peak.toFixed(2)}`}</title>
              </line>
            )}
          </g>
        ))}

        <text
          x={M.left + iw / 2}
          y={H - 8}
          textAnchor="middle"
          fontSize={10}
          fill="var(--ink-secondary)"
        >
          age in years
        </text>
      </svg>
      <figcaption className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-muted">
        {drawn.map((c) => (
          <span key={c.fit} className="inline-flex items-center gap-1.5">
            <svg width="18" height="8" aria-hidden="true">
              <line
                x1="0"
                x2="18"
                y1="4"
                y2="4"
                stroke={STROKE[c.fit] ?? "var(--ink-muted)"}
                strokeWidth={1.75}
                strokeDasharray={DASH[c.fit]}
              />
            </svg>
            {c.fit}
            {c.peak !== null ? ` ${c.peak.toFixed(2)}` : " no peak"}
          </span>
        ))}
      </figcaption>
    </figure>
  );
}
