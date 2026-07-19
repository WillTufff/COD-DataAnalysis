import Link from "next/link";
import type { TeamStint } from "@/lib/analytics";

// Roster history as horizontal spans, one row per player, ordered by first
// arrival. Identity lives in the row label, so all bars share one neutral fill.
export function StintTimeline({
  stints,
  rangeEnd,
}: {
  stints: TeamStint[];
  rangeEnd?: string; // clamp open stints to the archive edge (ISO date)
}) {
  if (stints.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-ink-muted">
        No roster records for this team.
      </p>
    );
  }
  const parse = (d: string) => Date.parse(d);
  const t0 = Math.min(...stints.map((s) => parse(s.startDate)));
  const tEnd = rangeEnd
    ? parse(rangeEnd)
    : Math.max(...stints.map((s) => (s.endDate ? parse(s.endDate) : parse(s.startDate))));
  const t1 = Math.max(
    tEnd,
    ...stints.map((s) => (s.endDate ? parse(s.endDate) : 0)),
  );

  // one row per player, stints merged onto it
  const players = new Map<number, { handle: string; slug: string; spans: TeamStint[] }>();
  for (const s of stints) {
    let p = players.get(s.playerId);
    if (!p) {
      p = { handle: s.handle, slug: s.slug, spans: [] };
      players.set(s.playerId, p);
    }
    p.spans.push(s);
  }
  const rows = [...players.values()];

  const W = 720;
  const LABEL = 110;
  const ROW = 20;
  const BAR = 10;
  const M = { top: 4, right: 12, bottom: 22 };
  const H = M.top + rows.length * ROW + M.bottom;
  const iw = W - LABEL - M.right;
  const x = (t: number) => LABEL + (t1 === t0 ? 0 : (t - t0) / (t1 - t0)) * iw;

  const years: number[] = [];
  for (let yr = new Date(t0).getUTCFullYear(); yr <= new Date(t1).getUTCFullYear(); yr++)
    years.push(yr);

  return (
    <div>
      <div className="relative">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="w-full"
          role="img"
          aria-label="Roster stints over time, one row per player"
        >
          {years.map((yr) => {
            const tt = Date.UTC(yr, 0, 1);
            if (tt < t0 || tt > t1) return null;
            return (
              <g key={yr}>
                <line
                  x1={x(tt)}
                  x2={x(tt)}
                  y1={M.top}
                  y2={H - M.bottom}
                  stroke="var(--hairline)"
                />
                <text x={x(tt) + 3} y={H - 8} fontSize={10} fill="var(--ink-muted)">
                  {yr}
                </text>
              </g>
            );
          })}
          {rows.map((p, i) => {
            const yTop = M.top + i * ROW + (ROW - BAR) / 2;
            return (
              <g key={p.slug}>
                <text
                  x={LABEL - 10}
                  y={M.top + i * ROW + ROW / 2 + 3.5}
                  textAnchor="end"
                  fontSize={11}
                  fill="var(--ink-secondary)"
                >
                  {p.handle}
                </text>
                {p.spans.map((s, j) => {
                  const a = parse(s.startDate);
                  const b = s.endDate ? parse(s.endDate) : t1;
                  return (
                    <rect
                      key={j}
                      x={x(a)}
                      y={yTop}
                      width={Math.max(2, x(b) - x(a))}
                      height={BAR}
                      fill="var(--surface-raised)"
                      stroke="var(--baseline)"
                      strokeWidth={1}
                    />
                  );
                })}
              </g>
            );
          })}
        </svg>
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {rows.map((p) => (
          <li key={p.slug}>
            <Link href={`/players/${p.slug}`} className="text-accent underline underline-offset-2 hover:text-ink">
              {p.handle}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
