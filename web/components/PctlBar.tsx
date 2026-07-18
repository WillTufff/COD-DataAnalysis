// Signature element: the cohort strip. Every percentile stat renders against
// its cohort as a small inline track, so a number is never shown context-free.
export function PctlBar({
  pctl,
  width = 72,
}: {
  pctl: number; // 0..1
  width?: number;
}) {
  const pct = Math.round(pctl * 100);
  const x = Math.max(0, Math.min(1, pctl)) * width;
  return (
    <span className="inline-flex items-center gap-2 align-middle">
      <svg
        width={width}
        height={10}
        viewBox={`0 0 ${width} 10`}
        role="img"
        aria-label={`${pct}th percentile of cohort`}
      >
        <rect x={0} y={4} width={width} height={2} rx={1} fill="var(--baseline)" />
        <rect x={width / 2 - 0.5} y={2} width={1} height={6} fill="var(--ink-muted)" />
        <circle cx={x} cy={5} r={3.5} fill="var(--accent)" />
      </svg>
      <span className="font-mono text-xs tabular-nums text-ink-secondary">{pct}th</span>
    </span>
  );
}
