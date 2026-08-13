// The opponent-adjustment ladder: how far each rung moves the leaderboard,
// against the threshold it has to clear and the placebo it has to stand clear
// of.
//
// The rung that moves the most is not the rung that was adopted, and a table of
// four numbers hides that. Here the bar is the movement, the tick is the
// declared threshold, and the three criteria ride alongside as pass marks — as
// a glyph and a word, never as colour alone, because "clears" is a state rather
// than an identity.

export type LadderRungView = {
  rung: string;
  move: number;
  placeboRatio: number | null;
  reliabilityGain: number | null;
  clears: boolean;
  adopted: boolean;
};

export function LadderMovement({
  rungs,
  threshold,
  width = 640,
}: {
  rungs: LadderRungView[];
  /** The movement a rung must reach to be worth adopting, in cohort sd. */
  threshold?: number;
  width?: number;
}) {
  if (rungs.length === 0) return null;

  const ROW = 34;
  const M = { left: 128, right: 150, top: 10, bottom: 26 };
  const H = M.top + rungs.length * ROW + M.bottom;
  const iw = width - M.left - M.right;

  const hi = Math.max(...rungs.map((r) => r.move), threshold ?? 0) * 1.12;
  const x = (v: number) => M.left + (v / hi) * iw;
  const ticks = [0, hi / 2, hi];

  return (
    <figure>
      <svg
        viewBox={`0 0 ${width} ${H}`}
        className="w-full"
        role="img"
        aria-label={`Leaderboard movement at each of ${rungs.length} rungs, in cohort standard deviations`}
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
              {t.toFixed(2)}
            </text>
          </g>
        ))}

        {threshold !== undefined && (
          <line
            x1={x(threshold)}
            x2={x(threshold)}
            y1={M.top}
            y2={H - M.bottom}
            stroke="var(--ink-muted)"
            strokeDasharray="3 3"
          />
        )}

        {rungs.map((r, i) => {
          const y = M.top + i * ROW + ROW / 2;
          return (
            <g key={r.rung}>
              <title>
                {`${r.rung}: moves ${r.move.toFixed(4)} cohort sd${
                  r.placeboRatio === null
                    ? ""
                    : `, placebo ratio ${r.placeboRatio.toFixed(2)}`
                }${r.clears ? ", clears every criterion" : ", does not clear"}`}
              </title>
              <text
                x={M.left - 10}
                y={y + 3.5}
                textAnchor="end"
                fontSize={10.5}
                fill={r.adopted ? "var(--ink)" : "var(--ink-secondary)"}
                className="font-mono"
              >
                {r.rung}
              </text>
              <rect
                x={M.left}
                y={y - 6}
                width={Math.max(x(r.move) - M.left, 1)}
                height={12}
                rx={3}
                fill={r.adopted ? "var(--accent)" : "var(--series-1)"}
                opacity={r.clears ? 0.9 : 0.4}
              />
              <text
                x={x(r.move) + 8}
                y={y + 3.5}
                fontSize={10}
                fill="var(--ink-secondary)"
                className="font-mono"
              >
                {r.move.toFixed(3)}
              </text>
              <text
                x={width - M.right + 60}
                y={y + 3.5}
                fontSize={10}
                fill={r.clears ? "var(--ink-secondary)" : "var(--ink-muted)"}
              >
                {r.clears ? "✓ clears" : "✗ fails a criterion"}
              </text>
            </g>
          );
        })}
      </svg>
    </figure>
  );
}
