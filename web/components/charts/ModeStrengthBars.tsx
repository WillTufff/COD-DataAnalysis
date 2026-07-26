import type { ModeStrength } from "@/lib/analytics";

// Per-mode team strength against the field, in Elo points, as bars diverging
// from zero. The shaded column is the null: the spread of these gaps across a
// shuffled archive. Bars inside it are what chance produces, and on this
// archive that is nearly all of them -- so the band is drawn first and drawn
// solid, not as a footnote under a chart that has already made its point.
export function ModeStrengthBars({ strength }: { strength: ModeStrength }) {
  const { rows, nullSd } = strength;
  if (rows.length === 0) return null;

  const W = 560;
  const M = { left: 118, right: 96, top: 4, bottom: 20 };
  const BAR = 14;
  const GAP = 8;
  const H = M.top + rows.length * (BAR + GAP) - GAP + M.bottom;
  const iw = W - M.left - M.right;

  // Always keep the whole null band in frame, so a team with small gaps reads
  // as small against chance rather than being rescaled to look decisive.
  const span = Math.max(nullSd * 1.15, ...rows.map((r) => Math.abs(r.rel)));
  const domain = Math.ceil(span / 25) * 25;
  const x = (v: number) => M.left + ((v + domain) / (2 * domain)) * iw;
  const ticks = [-domain, -domain / 2, 0, domain / 2, domain];

  return (
    <figure>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        role="img"
        aria-label="Per-mode team strength against the field, in Elo points"
      >
        <rect
          x={x(-nullSd)}
          y={M.top}
          width={x(nullSd) - x(-nullSd)}
          height={H - M.top - M.bottom}
          fill="var(--baseline)"
          opacity={0.32}
        />
        {[-nullSd, nullSd].map((v) => (
          <line
            key={v}
            x1={x(v)}
            x2={x(v)}
            y1={M.top}
            y2={H - M.bottom}
            stroke="var(--baseline)"
            strokeDasharray="3 3"
          />
        ))}
        {ticks.map((v) => (
          <g key={v}>
            <line
              x1={x(v)}
              x2={x(v)}
              y1={M.top}
              y2={H - M.bottom}
              stroke={v === 0 ? "var(--baseline)" : "var(--hairline)"}
            />
            <text
              x={x(v)}
              y={H - 6}
              textAnchor="middle"
              fontSize={9.5}
              fill="var(--ink-muted)"
              className="font-mono"
            >
              {v > 0 ? `+${v}` : v}
            </text>
          </g>
        ))}
        {rows.map((r, i) => {
          const yBar = M.top + i * (BAR + GAP);
          const x0 = x(0);
          const x1 = x(r.rel);
          const inside = Math.abs(r.rel) <= nullSd;
          return (
            <g key={r.mode}>
              <text
                x={M.left - 10}
                y={yBar + BAR / 2 + 3.5}
                textAnchor="end"
                fontSize={11.5}
                fill="var(--ink-secondary)"
              >
                {r.mode}
              </text>
              <rect
                x={Math.min(x0, x1)}
                y={yBar}
                width={Math.max(2, Math.abs(x1 - x0))}
                height={BAR}
                rx={3}
                fill={inside ? "var(--ink-muted)" : "var(--series-1)"}
                opacity={inside ? 0.5 : 1}
              />
              <text
                x={r.rel >= 0 ? x1 + 6 : x1 - 6}
                y={yBar + BAR / 2 + 3.5}
                textAnchor={r.rel >= 0 ? "start" : "end"}
                fontSize={10}
                fill="var(--ink-secondary)"
                className="font-mono"
              >
                {r.rel >= 0 ? "+" : ""}
                {Math.round(r.rel)}
              </text>
            </g>
          );
        })}
      </svg>
      <figcaption className="mt-1 text-xs text-ink-muted">
        Elo points against the field&rsquo;s average gap in each mode, from the
        map-level ratings. Shaded column is one SD of the same gaps under a
        shuffled archive; muted bars fall inside it.
      </figcaption>
    </figure>
  );
}

// The verdict that governs how the chart above may be read. Kept next to it in
// the same file so no page can render one without the other.
export function ModeStrengthNull({ strength }: { strength: ModeStrength }) {
  const { observedSd, nullLo, nullHi, pValue, exceedsNull, minModeMaps } =
    strength;
  return (
    <p className="mt-3 text-xs leading-relaxed text-ink-muted">
      {exceedsNull ? (
        <>
          Across the archive these gaps spread wider than shuffling produces
          &mdash; SD {observedSd.toFixed(1)} against a null of {nullLo.toFixed(1)}
          &ndash;{nullHi.toFixed(1)}, p&nbsp;={" "}
          {pValue.toFixed(3)}. Mode specialism is measurable.
        </>
      ) : (
        <>
          Read these as ordering, not as specialisms. Across the archive their
          spread is SD {observedSd.toFixed(1)}, inside the {nullLo.toFixed(1)}
          &ndash;{nullHi.toFixed(1)} a shuffled archive produces (p&nbsp;={" "}
          {pValue.toFixed(3)}), so this team&rsquo;s ordering is not shown to be
          real. Modes with under {minModeMaps} maps are not rated at all.
        </>
      )}
    </p>
  );
}
