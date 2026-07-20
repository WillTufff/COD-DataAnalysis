// Stacked share bar for an ordered distribution — SnD rounds by kills scored.
// The categories are ordered, so the ramp is one hue at rising strength rather
// than the categorical series palette: 0 kills reads as absence, 4 as the peak.
export type ShareSegment = {
  label: string; // "0k"
  share: number; // 0..1
};

const RAMP = [
  "var(--baseline)",
  "color-mix(in srgb, var(--accent) 30%, var(--baseline))",
  "color-mix(in srgb, var(--accent) 55%, var(--baseline))",
  "var(--accent-dim)",
  "var(--accent)",
];

export function RoundShareBar({ segments }: { segments: ShareSegment[] }) {
  const total = segments.reduce((s, x) => s + x.share, 0);
  if (total <= 0) return null;
  const W = 300;
  const widths = segments.map((s) => (s.share / total) * W);
  const offsets = widths.reduce<number[]>(
    (acc, w, i) => [...acc, (acc[i] ?? 0) + w],
    [0],
  );

  return (
    <div>
      <svg
        viewBox={`0 0 ${W} 16`}
        className="h-4 w-full"
        preserveAspectRatio="none"
        role="img"
        aria-label={segments
          .map((s) => `${s.label}: ${Math.round((s.share / total) * 100)}%`)
          .join(", ")}
      >
        {segments.map((s, i) => (
          <rect
            key={s.label}
            x={offsets[i]}
            y={0}
            width={Math.max(0, widths[i])}
            height={16}
            fill={RAMP[i] ?? "var(--baseline)"}
          />
        ))}
      </svg>
      <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">
        {segments.map((s, i) => (
          <span
            key={s.label}
            className="flex items-center gap-1.5 font-mono text-[10px] text-ink-muted"
          >
            <span
              className="inline-block h-2 w-2 flex-none"
              style={{ background: RAMP[i] ?? "var(--baseline)" }}
            />
            {s.label} {(s.share * 100).toFixed(0)}%
          </span>
        ))}
      </div>
    </div>
  );
}
