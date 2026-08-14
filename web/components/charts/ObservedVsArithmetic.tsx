// What actually happened from a score state, against what the race arithmetic
// says should happen from it. One mark per state, on the line where the two
// agree.
//
// The segment model's whole finding is a comparison, and it is a comparison a
// table hides: twenty rows of "0.622 against 0.637" reads as twenty small
// discrepancies, while the same twenty rows drawn read as one line. Every mark
// sitting on the diagonal is the claim — a lead in Call of Duty is worth its
// arithmetic and no more — and any mark that ever left the diagonal would be
// visible without the reader computing anything.
//
// Marks are sized by how many maps stand behind them, because a cell with sixty
// observations and a cell with a thousand are not equally strong evidence and
// equal dots would say they were.

export type StateCell = {
  own: number;
  opp: number;
  n: number;
  observed: number;
  expected: number;
};

export function ObservedVsArithmetic({
  cells,
  expectedLabel = "the race arithmetic",
  observedLabel = "what happened",
  width = 460,
}: {
  cells: StateCell[];
  expectedLabel?: string;
  observedLabel?: string;
  width?: number;
}) {
  if (cells.length === 0) return null;

  const M = { left: 44, right: 14, top: 12, bottom: 34 };
  const H = width - M.left - M.right + M.top + M.bottom;
  const iw = width - M.left - M.right;
  const ih = H - M.top - M.bottom;

  const x = (v: number) => M.left + v * iw;
  const y = (v: number) => M.top + (1 - v) * ih;

  const maxN = Math.max(...cells.map((c) => c.n));
  const radius = (n: number) => 2.5 + 4 * Math.sqrt(n / maxN);
  const ticks = [0, 0.25, 0.5, 0.75, 1];

  // The worst mark carries a label, so the picture states its own limit rather
  // than inviting the reader to trust that nothing is hiding in the cloud.
  const worst = cells.reduce((a, b) =>
    Math.abs(a.observed - a.expected) >= Math.abs(b.observed - b.expected) ? a : b,
  );

  return (
    <figure>
      <svg
        viewBox={`0 0 ${width} ${H}`}
        className="w-full"
        role="img"
        aria-label={`${observedLabel} against ${expectedLabel}, for ${cells.length} score states`}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line
              x1={M.left}
              x2={M.left + iw}
              y1={y(t)}
              y2={y(t)}
              stroke="var(--hairline)"
            />
            <text
              x={M.left - 8}
              y={y(t) + 3.5}
              textAnchor="end"
              fontSize={9.5}
              fill="var(--ink-muted)"
              className="font-mono"
            >
              {t.toFixed(2)}
            </text>
            <text
              x={x(t)}
              y={H - 14}
              textAnchor="middle"
              fontSize={9.5}
              fill="var(--ink-muted)"
              className="font-mono"
            >
              {t.toFixed(2)}
            </text>
          </g>
        ))}

        {/* Agreement. Not a fitted line — the line the two would share if the
            arithmetic were the whole story. */}
        <line
          x1={x(0)}
          y1={y(0)}
          x2={x(1)}
          y2={y(1)}
          stroke="var(--baseline)"
          strokeWidth={1.5}
          strokeDasharray="4 3"
        />

        {cells.map((c) => (
          <circle
            key={`${c.own}-${c.opp}`}
            cx={x(c.expected)}
            cy={y(c.observed)}
            r={radius(c.n)}
            fill="var(--surface)"
            stroke="var(--series-1)"
            strokeWidth={1.75}
          >
            <title>
              {`${c.own}-${c.opp}: ${c.observed.toFixed(3)} observed against ${c.expected.toFixed(3)} expected, over ${c.n} states`}
            </title>
          </circle>
        ))}

        <text
          x={x(worst.expected)}
          y={y(worst.observed) - radius(worst.n) - 5}
          textAnchor="middle"
          fontSize={9.5}
          fill="var(--ink-muted)"
          className="font-mono"
        >
          {`${worst.own}–${worst.opp}`}
        </text>

        <text
          x={M.left + iw / 2}
          y={H - 2}
          textAnchor="middle"
          fontSize={10}
          fill="var(--ink-secondary)"
        >
          {expectedLabel}
        </text>
        <text
          x={12}
          y={M.top + ih / 2}
          textAnchor="middle"
          fontSize={10}
          fill="var(--ink-secondary)"
          transform={`rotate(-90 12 ${M.top + ih / 2})`}
        >
          {observedLabel}
        </text>
      </svg>
    </figure>
  );
}
