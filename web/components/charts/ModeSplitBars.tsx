import type { ModeSplit } from "@/lib/analytics";

// Map win rate by mode: one bar per mode against the 50% baseline, with the
// record printed at the end so color never carries the number alone.
export function ModeSplitBars({ splits }: { splits: ModeSplit[] }) {
  if (splits.length === 0) return null;
  const W = 560;
  const M = { left: 118, right: 96, top: 4, bottom: 20 };
  const BAR = 14;
  const GAP = 8;
  const H = M.top + splits.length * (BAR + GAP) - GAP + M.bottom;
  const iw = W - M.left - M.right;
  const x = (v: number) => M.left + v * iw;

  return (
    <figure>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        role="img"
        aria-label="Map win rate by game mode"
      >
        {[0, 0.25, 0.5, 0.75, 1].map((v) => (
          <g key={v}>
            <line
              x1={x(v)}
              x2={x(v)}
              y1={M.top}
              y2={H - M.bottom}
              stroke={v === 0.5 ? "var(--baseline)" : "var(--hairline)"}
            />
            <text
              x={x(v)}
              y={H - 6}
              textAnchor="middle"
              fontSize={9.5}
              fill="var(--ink-muted)"
              className="font-mono"
            >
              {Math.round(v * 100)}%
            </text>
          </g>
        ))}
        {splits.map((s, i) => {
          const yBar = M.top + i * (BAR + GAP);
          const rate = s.maps > 0 ? s.wins / s.maps : 0;
          const w = Math.max(2, x(rate) - x(0));
          return (
            <g key={s.mode}>
              <text
                x={M.left - 10}
                y={yBar + BAR / 2 + 3.5}
                textAnchor="end"
                fontSize={11.5}
                fill="var(--ink-secondary)"
              >
                {s.mode}
              </text>
              <path
                d={`M${x(0)},${yBar} h${w - 4} a4,4 0 0 1 4,4 v${BAR - 8} a4,4 0 0 1 -4,4 h${4 - w} Z`}
                fill="var(--series-1)"
              />
              <text
                x={x(rate) + 6}
                y={yBar + BAR / 2 + 3.5}
                fontSize={10}
                fill="var(--ink-secondary)"
                className="font-mono"
              >
                {Math.round(rate * 100)}% ({s.wins}–{s.maps - s.wins})
              </text>
            </g>
          );
        })}
      </svg>
      <figcaption className="mt-1 text-xs text-ink-muted">
        Decided maps only. The 50% line is an even split.
      </figcaption>
    </figure>
  );
}
