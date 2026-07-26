// Composite-rating uncertainty, drawn rather than parenthesised.
//
// The rating's posterior sd is stored per player-season, so every rating on the
// site can carry its own interval. Two shapes share one convention here: an
// inline track for table cells, and a season-by-season plot for the player
// page. Both draw ±1.96 sd — the same 95% the career arc uses — on a domain
// shared across rows, because the point of the band is that neighbouring rows
// overlap. An interval that is only comparable to itself hides that.
//
// A season with no stored sd gets a point and no band, never a zero-width one,
// which would read as certainty.

/** Half-width of the 95% interval, or null where the model stored no sd. */
export function halfWidth(sd: number | null): number | null {
  return sd === null ? null : 1.96 * sd;
}

/** Do two intervals overlap? Missing sd means "cannot tell" — treated as yes,
 *  since claiming separation on a number with no error bar is the mistake this
 *  whole section exists to avoid. */
export function overlaps(
  a: { rating: number; ratingSd: number | null },
  b: { rating: number; ratingSd: number | null },
): boolean {
  const ha = halfWidth(a.ratingSd);
  const hb = halfWidth(b.ratingSd);
  if (ha === null || hb === null) return true;
  return Math.abs(a.rating - b.rating) <= ha + hb;
}

/**
 * Inline 95% interval on a shared domain, for a table cell. Sibling to PctlBar:
 * same track height, same "number is never shown context-free" rule, except
 * what it carries is the error rather than the rank.
 */
export function IntervalBar({
  value,
  sd,
  lo,
  hi,
  mark,
  width = 96,
  label,
}: {
  value: number;
  sd: number | null;
  lo: number; // domain, shared by every row in the table
  hi: number;
  mark?: number; // reference line, e.g. the 1.00 league mean
  width?: number;
  label: string; // spoken description; the visual has no axis of its own
}) {
  const H = 10;
  const span = hi - lo || 1;
  const x = (v: number) =>
    Math.max(0, Math.min(1, (v - lo) / span)) * width;
  const half = halfWidth(sd);

  return (
    <svg
      width={width}
      height={H}
      viewBox={`0 0 ${width} ${H}`}
      className="align-middle"
      role="img"
      aria-label={label}
    >
      <rect x={0} y={4} width={width} height={2} rx={1} fill="var(--baseline)" />
      {/* A reference outside the domain is dropped rather than clamped: a tick
          pinned to the edge would read as a value that is on the scale. */}
      {mark !== undefined && mark >= lo && mark <= hi && (
        <rect x={x(mark) - 0.5} y={1} width={1} height={8} fill="var(--ink-muted)" />
      )}
      {half !== null && (
        <rect
          x={x(value - half)}
          y={3.5}
          width={Math.max(1, x(value + half) - x(value - half))}
          height={3}
          rx={1.5}
          fill="var(--accent-dim)"
        />
      )}
      <circle cx={x(value)} cy={5} r={3} fill="var(--accent)" />
    </svg>
  );
}

export type RatingSeasonPoint = {
  seasonId: number;
  year: number;
  title: string;
  mapsPlayed: number;
  rating: number;
  ratingSd: number | null;
  qualified: boolean;
};

/**
 * One row per rated season: the composite rating with its 95% posterior
 * interval, on the league's qualified range. Seasons that missed the map
 * minimum are drawn dimmed — the model still rates them, but the board does not
 * rank them, and their intervals are wide enough to show why.
 */
export function RatingIntervals({
  seasons,
  lo,
  hi,
  minMaps,
  mark = 1,
}: {
  seasons: RatingSeasonPoint[];
  lo: number;
  hi: number;
  minMaps: number;
  mark?: number;
}) {
  const W = 640;
  const ROW = 30;
  // The right margin holds the map counts, so a band that runs to the end of
  // the domain never collides with them.
  const M = { top: 20, right: 96, bottom: 30, left: 92 };
  const iw = W - M.left - M.right;
  const H = M.top + seasons.length * ROW + M.bottom;

  // Pad the domain so a band that reaches the extreme is not flush to the edge.
  const pad = (hi - lo) * 0.06 || 0.05;
  const dLo = lo - pad;
  const dHi = hi + pad;
  const x = (v: number) => M.left + ((v - dLo) / (dHi - dLo)) * iw;
  const rowY = (i: number) => M.top + i * ROW + ROW / 2;

  // Ticks every 0.1 through the domain, which spans well under one rating unit.
  const ticks: number[] = [];
  for (let v = Math.ceil(dLo * 10) / 10; v <= dHi; v = Math.round((v + 0.1) * 10) / 10)
    ticks.push(v);

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="w-full"
      role="img"
      aria-label="Composite rating by season with 95% posterior intervals"
    >
      {ticks.map((v) => (
        <g key={v}>
          <line
            x1={x(v)}
            x2={x(v)}
            y1={M.top - 6}
            y2={H - M.bottom}
            stroke="var(--hairline)"
            strokeWidth={1}
          />
          <text
            x={x(v)}
            y={H - M.bottom + 16}
            textAnchor="middle"
            fontSize={10}
            fill="var(--ink-muted)"
            className="font-mono"
          >
            {v.toFixed(1)}
          </text>
        </g>
      ))}
      {/* the league mean — an average qualified season sits at 1.00 */}
      <line
        x1={x(mark)}
        x2={x(mark)}
        y1={M.top - 6}
        y2={H - M.bottom}
        stroke="var(--baseline)"
        strokeWidth={1.5}
      />
      <text
        x={x(mark)}
        y={M.top - 10}
        textAnchor="middle"
        fontSize={10}
        fill="var(--ink-muted)"
        className="font-mono"
      >
        league average
      </text>

      {seasons.map((s, i) => {
        const half = halfWidth(s.ratingSd);
        const y = rowY(i);
        const dim = !s.qualified;
        return (
          <g key={s.seasonId} opacity={dim ? 0.55 : 1}>
            <text
              x={M.left - 10}
              y={y + 3.5}
              textAnchor="end"
              fontSize={11}
              fill="var(--ink-secondary)"
              className="font-mono"
            >
              {s.year} {s.title}
            </text>
            {half !== null && (
              <>
                <line
                  x1={x(s.rating - half)}
                  x2={x(s.rating + half)}
                  y1={y}
                  y2={y}
                  stroke="var(--accent-dim)"
                  strokeWidth={5}
                  strokeLinecap="round"
                />
                <line
                  x1={x(s.rating - half)}
                  x2={x(s.rating - half)}
                  y1={y - 5}
                  y2={y + 5}
                  stroke="var(--accent-dim)"
                  strokeWidth={1}
                />
                <line
                  x1={x(s.rating + half)}
                  x2={x(s.rating + half)}
                  y1={y - 5}
                  y2={y + 5}
                  stroke="var(--accent-dim)"
                  strokeWidth={1}
                />
              </>
            )}
            <circle cx={x(s.rating)} cy={y} r={4} fill="var(--accent)" />
            <text
              x={W - 8}
              y={y + 3.5}
              textAnchor="end"
              fontSize={10}
              fill="var(--ink-muted)"
              className="font-mono"
            >
              {s.mapsPlayed} maps{dim ? ` · under ${minMaps}` : ""}
            </text>
            <title>
              {`${s.year} ${s.title}: ${s.rating.toFixed(2)}${
                half === null ? "" : ` (95% ${(s.rating - half).toFixed(2)}–${(s.rating + half).toFixed(2)})`
              } over ${s.mapsPlayed} maps`}
            </title>
          </g>
        );
      })}
    </svg>
  );
}
