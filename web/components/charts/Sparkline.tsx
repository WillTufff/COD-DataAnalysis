// Inline rating trajectory for table rows. One series per row, so the line
// stays in neutral ink; the end dot marks the final (current) value.
export function Sparkline({
  values,
  domain,
  width = 104,
  height = 24,
  label,
}: {
  values: number[];
  domain?: [number, number]; // shared across rows for comparability
  width?: number;
  height?: number;
  label?: string;
}) {
  if (values.length < 2) return null;
  const [d0, d1] = domain ?? [Math.min(...values), Math.max(...values)];
  const pad = 2.5;
  const x = (i: number) => pad + (i / (values.length - 1)) * (width - pad * 2);
  const y = (v: number) =>
    d1 === d0
      ? height / 2
      : height - pad - ((v - d0) / (d1 - d0)) * (height - pad * 2);
  const d = values.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join(" ");
  const last = values[values.length - 1];
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={label ?? "rating over time"}
      className="align-middle"
    >
      <path
        d={d}
        fill="none"
        stroke="var(--ink-muted)"
        strokeWidth={1.5}
        strokeLinejoin="round"
      />
      <circle cx={x(values.length - 1)} cy={y(last)} r={2.5} fill="var(--accent)" />
    </svg>
  );
}
