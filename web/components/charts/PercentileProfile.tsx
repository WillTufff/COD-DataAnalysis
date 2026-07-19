// Percentile panel: one labeled 0–100 track per stat, filled to the subject's
// cohort percentile. The accent marks the subject, as in PctlBar; the midpoint
// tick is the cohort median.
export type ProfileStat = {
  label: string;
  pctl: number; // 0..1
  value: string; // the underlying number, shown at right
};

export function PercentileProfile({ stats }: { stats: ProfileStat[] }) {
  if (stats.length === 0) return null;
  return (
    <div className="space-y-2.5">
      {stats.map((s) => {
        const pct = Math.round(Math.max(0, Math.min(1, s.pctl)) * 100);
        return (
          <div key={s.label} className="flex items-center gap-3">
            <span className="w-28 flex-none text-xs text-ink-secondary">
              {s.label}
            </span>
            <svg
              viewBox="0 0 300 14"
              className="h-3.5 min-w-0 flex-1"
              preserveAspectRatio="none"
              role="img"
              aria-label={`${s.label}: ${pct}th percentile`}
            >
              <rect x={0} y={5.5} width={300} height={3} fill="var(--baseline)" />
              <rect x={149.5} y={2.5} width={1} height={9} fill="var(--ink-muted)" />
              <rect
                x={0}
                y={5.5}
                width={Math.max(3, pct * 3)}
                height={3}
                fill="var(--accent-dim)"
              />
              <circle cx={Math.max(4, Math.min(296, pct * 3))} cy={7} r={4} fill="var(--accent)" />
            </svg>
            <span className="w-10 flex-none text-right font-mono text-xs tabular-nums">
              {pct}th
            </span>
            <span className="w-14 flex-none text-right font-mono text-xs tabular-nums text-ink-secondary">
              {s.value}
            </span>
          </div>
        );
      })}
    </div>
  );
}
