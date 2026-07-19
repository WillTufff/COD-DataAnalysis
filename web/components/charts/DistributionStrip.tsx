// One-axis strip plot of a cohort. Every member is a translucent tick; the
// highlighted subject, if any, gets the accent dot and a direct label.
export function DistributionStrip({
  values,
  highlight,
  highlightLabel,
  domain,
  unit,
  width = 560,
}: {
  values: number[];
  highlight?: number;
  highlightLabel?: string;
  domain?: [number, number];
  unit?: string; // axis caption, e.g. "raw K/D"
  width?: number;
}) {
  if (values.length === 0) return null;
  const lo = domain?.[0] ?? Math.min(...values, highlight ?? Infinity);
  const hi = domain?.[1] ?? Math.max(...values, highlight ?? -Infinity);
  const H = 44;
  const M = { left: 8, right: 8 };
  const x = (v: number) =>
    M.left + (hi === lo ? 0.5 : (v - lo) / (hi - lo)) * (width - M.left - M.right);
  const sorted = [...values].sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)];
  const fmt = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2));

  return (
    <svg
      viewBox={`0 0 ${width} ${H}`}
      className="w-full"
      role="img"
      aria-label={`Distribution of ${unit ?? "values"} across ${values.length} cohort members`}
    >
      <line
        x1={M.left}
        x2={width - M.right}
        y1={22}
        y2={22}
        stroke="var(--hairline)"
      />
      {values.map((v, i) => (
        <line
          key={i}
          x1={x(v)}
          x2={x(v)}
          y1={14}
          y2={30}
          stroke="var(--ink-muted)"
          strokeWidth={1}
          opacity={0.28}
        />
      ))}
      <line x1={x(median)} x2={x(median)} y1={11} y2={33} stroke="var(--ink-secondary)" strokeWidth={1.5} />
      <text x={x(median)} y={42} textAnchor="middle" fontSize={9} fill="var(--ink-muted)" className="font-mono">
        median {fmt(median)}
      </text>
      <text x={M.left} y={9} fontSize={9} fill="var(--ink-muted)" className="font-mono">
        {fmt(lo)}
      </text>
      <text x={width - M.right} y={9} textAnchor="end" fontSize={9} fill="var(--ink-muted)" className="font-mono">
        {fmt(hi)}
      </text>
      {highlight !== undefined && (
        <g>
          <circle cx={x(highlight)} cy={22} r={5} fill="var(--accent)" stroke="var(--background)" strokeWidth={2} />
          {highlightLabel && (
            <text
              x={x(highlight)}
              y={9}
              textAnchor="middle"
              fontSize={9.5}
              fill="var(--ink)"
            >
              {highlightLabel} {fmt(highlight)}
            </text>
          )}
        </g>
      )}
    </svg>
  );
}
