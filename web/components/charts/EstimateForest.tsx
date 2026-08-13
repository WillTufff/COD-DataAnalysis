// One estimate per row, drawn as its interval rather than as a number with a
// parenthesis after it.
//
// Every result the evaluation harness publishes is a correlation with a
// resampled interval, and the interval is the finding: a predictor that beats
// another by less than the width of either band has not beaten it. So the band
// is the mark and the point is a dot on it, and the baseline it is measured
// against is a line the reader can see every row cross or fail to cross.
//
// One series, one colour. The emphasised row keeps the accent; everything else
// is ink, because the chart's job is a comparison against the reference line,
// not identity between rows.

export type ForestRow = {
  label: string;
  value: number;
  lo: number;
  hi: number;
  /** Draws the row in the accent. Used for the subject, never for the winner. */
  emphasis?: boolean;
  /** Right-hand annotation, e.g. the gap against the baseline. */
  note?: string;
};

export function EstimateForest({
  rows,
  reference,
  referenceLabel,
  unit,
  width = 640,
}: {
  rows: ForestRow[];
  /** The value every row is judged against, e.g. the baseline's r. */
  reference?: number;
  referenceLabel?: string;
  unit?: string;
  width?: number;
}) {
  if (rows.length === 0) return null;

  const ROW = 30;
  const M = { left: 118, right: 108, top: 10, bottom: 26 };
  const H = M.top + rows.length * ROW + M.bottom;
  const iw = width - M.left - M.right;

  const values = rows.flatMap((r) => [r.lo, r.hi, r.value]);
  if (reference !== undefined) values.push(reference);
  const rawLo = Math.min(...values, 0);
  const rawHi = Math.max(...values, 0);
  const pad = (rawHi - rawLo) * 0.08 || 0.1;
  const lo = rawLo - pad;
  const hi = rawHi + pad;
  const x = (v: number) => M.left + ((v - lo) / (hi - lo)) * iw;

  // Zero is a tick whenever it is on the axis: an interval that covers it is a
  // result whose sign cannot be read, so the reader has to be able to find it.
  const ticks = [lo, ...(lo < 0 && hi > 0 ? [0] : [(lo + hi) / 2]), hi];
  const fmt = (v: number) => v.toFixed(2);

  return (
    <svg
      viewBox={`0 0 ${width} ${H}`}
      className="w-full"
      role="img"
      aria-label={`${rows.length} estimates with 95% intervals${
        unit ? ` of ${unit}` : ""
      }`}
    >
      {ticks.map((t) => (
        <g key={t}>
          <line
            x1={x(t)}
            x2={x(t)}
            y1={M.top}
            y2={H - M.bottom}
            stroke="var(--hairline)"
          />
          <text
            x={x(t)}
            y={H - 10}
            textAnchor="middle"
            fontSize={9.5}
            fill="var(--ink-muted)"
            className="font-mono"
          >
            {fmt(t)}
          </text>
        </g>
      ))}

      {/* Zero, where it is inside the domain: an interval covering it is a
          result the sign cannot be read off. */}
      {lo < 0 && hi > 0 && (
        <line
          x1={x(0)}
          x2={x(0)}
          y1={M.top}
          y2={H - M.bottom}
          stroke="var(--baseline)"
        />
      )}

      {reference !== undefined && (
        <g>
          <line
            x1={x(reference)}
            x2={x(reference)}
            y1={M.top - 4}
            y2={H - M.bottom}
            stroke="var(--ink-muted)"
            strokeDasharray="3 3"
          />
          {referenceLabel && (
            <text
              x={x(reference)}
              y={H - 10}
              textAnchor="middle"
              fontSize={9.5}
              fill="var(--ink-secondary)"
            >
              {referenceLabel}
            </text>
          )}
        </g>
      )}

      {rows.map((r, i) => {
        const y = M.top + i * ROW + ROW / 2;
        const ink = r.emphasis ? "var(--accent)" : "var(--series-1)";
        return (
          <g key={r.label}>
            <title>
              {`${r.label}: ${r.value.toFixed(4)} (95% ${r.lo.toFixed(4)} to ${r.hi.toFixed(4)})`}
            </title>
            <text
              x={M.left - 10}
              y={y + 3.5}
              textAnchor="end"
              fontSize={11}
              fill={r.emphasis ? "var(--ink)" : "var(--ink-secondary)"}
              className="font-mono"
            >
              {r.label}
            </text>
            <line
              x1={x(r.lo)}
              x2={x(r.hi)}
              y1={y}
              y2={y}
              stroke={ink}
              strokeWidth={2}
              strokeLinecap="round"
              opacity={r.emphasis ? 1 : 0.75}
            />
            <circle
              cx={x(r.value)}
              cy={y}
              r={4.5}
              fill="var(--surface)"
              stroke={ink}
              strokeWidth={2}
            />
            <text
              x={width - M.right + 8}
              y={y + 3.5}
              fontSize={10.5}
              fill="var(--ink-secondary)"
              className="font-mono"
            >
              {r.note ?? r.value.toFixed(3)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
