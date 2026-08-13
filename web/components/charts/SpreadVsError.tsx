// Two quantities per row, on one axis, joined by the line between them: how far
// apart a cell's estimates are, and how large their own standard error is.
//
// The season plus-minus argues one thing above everything else — the spread of
// the coefficients inside a cell is smaller than the uncertainty on any one of
// them — and that is a sentence a table makes the reader compute. Drawn, the
// two marks cross over and the argument is the picture: every row whose error
// mark sits to the right of its spread mark is a cell where the model does not
// establish that the players differ.
//
// Two series, so a legend is present, and the marks differ in shape as well as
// colour so identity never rests on hue alone.

export type SpreadRow = {
  label: string;
  spread: number;
  error: number;
  note?: string;
};

export function SpreadVsError({
  rows,
  spreadLabel = "spread of the estimates",
  errorLabel = "their median standard error",
  width = 640,
}: {
  rows: SpreadRow[];
  spreadLabel?: string;
  errorLabel?: string;
  width?: number;
}) {
  if (rows.length === 0) return null;

  const ROW = 26;
  const M = { left: 130, right: 96, top: 10, bottom: 26 };
  const H = M.top + rows.length * ROW + M.bottom;
  const iw = width - M.left - M.right;

  const hi = Math.max(...rows.flatMap((r) => [r.spread, r.error])) * 1.08;
  const x = (v: number) => M.left + (v / hi) * iw;
  const ticks = [0, hi / 2, hi];

  return (
    <figure>
      {/* Legend in markup rather than inside the drawing, so it wraps with the
          column instead of running off the viewBox at a narrow width. */}
      <figcaption className="mb-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-ink-secondary">
        <span className="flex items-center gap-2">
          <svg width="10" height="10" aria-hidden="true">
            <rect
              x={1}
              y={1}
              width={8}
              height={8}
              fill="var(--surface)"
              stroke="var(--series-1)"
              strokeWidth={2}
            />
          </svg>
          {spreadLabel}
        </span>
        <span className="flex items-center gap-2">
          <svg width="11" height="11" aria-hidden="true">
            <circle
              cx={5.5}
              cy={5.5}
              r={4}
              fill="var(--surface)"
              stroke="var(--series-4)"
              strokeWidth={2}
            />
          </svg>
          {errorLabel}
        </span>
      </figcaption>
      <svg
        viewBox={`0 0 ${width} ${H}`}
        className="w-full"
        role="img"
        aria-label={`${spreadLabel} against ${errorLabel}, for ${rows.length} cells`}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line
              x1={x(t)}
              x2={x(t)}
              y1={M.top - 6}
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

        {rows.map((r, i) => {
          const y = M.top + i * ROW + ROW / 2;
          return (
            <g key={r.label}>
              <title>
                {`${r.label}: spread ${r.spread.toFixed(4)}, median se ${r.error.toFixed(4)}`}
              </title>
              <text
                x={M.left - 10}
                y={y + 3.5}
                textAnchor="end"
                fontSize={10.5}
                fill="var(--ink-secondary)"
              >
                {r.label}
              </text>
              <line
                x1={x(Math.min(r.spread, r.error))}
                x2={x(Math.max(r.spread, r.error))}
                y1={y}
                y2={y}
                stroke="var(--baseline)"
                strokeWidth={2}
              />
              {/* Square: what the cell claims to have measured. */}
              <rect
                x={x(r.spread) - 4}
                y={y - 4}
                width={8}
                height={8}
                fill="var(--surface)"
                stroke="var(--series-1)"
                strokeWidth={2}
              />
              {/* Circle: what it does not know. */}
              <circle
                cx={x(r.error)}
                cy={y}
                r={4.5}
                fill="var(--surface)"
                stroke="var(--series-4)"
                strokeWidth={2}
              />
              {r.note && (
                <text
                  x={width - M.right + 8}
                  y={y + 3.5}
                  fontSize={10}
                  fill="var(--ink-muted)"
                  className="font-mono"
                >
                  {r.note}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </figure>
  );
}
