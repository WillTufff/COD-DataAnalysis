// Where a SKILL rating came from, per season: the box-score prior, the
// plus-minus coefficient with its standard error, and the posterior of the two.
//
// The number this rating is argued about is `weight_prior`, and a column of
// percentages does not show what it means. Drawn on one axis it does: the
// plus-minus whisker is wide, the prior mark is narrow, and the posterior sits
// almost on top of the prior. The pull is the distance between the posterior
// and the coefficient, and it is nearly the whole gap.
//
// Three marks, so a legend is present and every mark has its own shape.

export type BlendSeason = {
  label: string;
  priorMean: number;
  coef: number;
  se: number;
  skill: number;
  skillSd: number;
  weightPrior: number;
};

export function SkillBlend({
  seasons,
  width = 640,
}: {
  seasons: BlendSeason[];
  width?: number;
}) {
  if (seasons.length === 0) return null;

  const ROW = 34;
  const M = { left: 62, right: 104, top: 10, bottom: 26 };
  const H = M.top + seasons.length * ROW + M.bottom;
  const iw = width - M.left - M.right;

  const spans = seasons.flatMap((s) => [
    s.coef - 1.96 * s.se,
    s.coef + 1.96 * s.se,
    s.priorMean,
    s.skill - 1.96 * s.skillSd,
    s.skill + 1.96 * s.skillSd,
  ]);
  const rawLo = Math.min(...spans, 0);
  const rawHi = Math.max(...spans, 0);
  const pad = (rawHi - rawLo) * 0.06 || 0.05;
  const lo = rawLo - pad;
  const hi = rawHi + pad;
  const x = (v: number) => M.left + ((v - lo) / (hi - lo)) * iw;
  const ticks = [lo, 0, hi].filter((t, i, a) => a.indexOf(t) === i);

  return (
    <figure>
      <figcaption className="mb-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-ink-secondary">
        <span className="flex items-center gap-2">
          <svg width="26" height="10" aria-hidden="true">
            <line
              x1={1}
              x2={25}
              y1={5}
              y2={5}
              stroke="var(--series-4)"
              strokeWidth={2}
              strokeLinecap="round"
            />
          </svg>
          plus-minus, 95% interval
        </span>
        <span className="flex items-center gap-2">
          <svg width="10" height="12" aria-hidden="true">
            <line
              x1={5}
              x2={5}
              y1={1}
              y2={11}
              stroke="var(--series-1)"
              strokeWidth={2}
            />
          </svg>
          box-score prior
        </span>
        <span className="flex items-center gap-2">
          <svg width="11" height="11" aria-hidden="true">
            <circle
              cx={5.5}
              cy={5.5}
              r={4}
              fill="var(--surface)"
              stroke="var(--accent)"
              strokeWidth={2}
            />
          </svg>
          SKILL
        </span>
      </figcaption>
      <svg
        viewBox={`0 0 ${width} ${H}`}
        className="w-full"
        role="img"
        aria-label={`SKILL against the prior and the plus-minus it was blended from, for ${seasons.length} seasons`}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line
              x1={x(t)}
              x2={x(t)}
              y1={M.top}
              y2={H - M.bottom}
              stroke={t === 0 ? "var(--baseline)" : "var(--hairline)"}
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

        {seasons.map((s, i) => {
          const y = M.top + i * ROW + ROW / 2;
          return (
            <g key={s.label}>
              <title>
                {`${s.label}: prior ${s.priorMean.toFixed(3)}, plus-minus ${s.coef.toFixed(3)} ± ${s.se.toFixed(3)}, SKILL ${s.skill.toFixed(3)} (${(s.weightPrior * 100).toFixed(0)}% prior)`}
              </title>
              <text
                x={M.left - 10}
                y={y + 3.5}
                textAnchor="end"
                fontSize={10.5}
                fill="var(--ink-secondary)"
              >
                {s.label}
              </text>
              <line
                x1={x(s.coef - 1.96 * s.se)}
                x2={x(s.coef + 1.96 * s.se)}
                y1={y}
                y2={y}
                stroke="var(--series-4)"
                strokeWidth={2}
                strokeLinecap="round"
                opacity={0.85}
              />
              <line
                x1={x(s.skill - 1.96 * s.skillSd)}
                x2={x(s.skill + 1.96 * s.skillSd)}
                y1={y}
                y2={y}
                stroke="var(--accent)"
                strokeWidth={3}
                strokeLinecap="round"
              />
              <line
                x1={x(s.priorMean)}
                x2={x(s.priorMean)}
                y1={y - 10}
                y2={y + 10}
                stroke="var(--series-1)"
                strokeWidth={2}
              />
              <circle
                cx={x(s.skill)}
                cy={y}
                r={4.5}
                fill="var(--surface)"
                stroke="var(--accent)"
                strokeWidth={2}
              />
              <text
                x={width - M.right + 8}
                y={y + 3.5}
                fontSize={10}
                fill="var(--ink-muted)"
                className="font-mono"
              >
                {(s.weightPrior * 100).toFixed(0)}% prior
              </text>
            </g>
          );
        })}
      </svg>
    </figure>
  );
}
