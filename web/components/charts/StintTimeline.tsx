import Link from "next/link";
import type { TeamStint } from "@/lib/analytics";
import { type RosterGroup, rosterGroup } from "@/lib/roster";

type RowGroup = "player" | "staff";
const GROUP_ORDER: RowGroup[] = ["player", "staff"];
const GROUP_LABEL: Record<RowGroup, string> = { player: "Players", staff: "Staff" };
const rowGroup = (g: RosterGroup): RowGroup => (g === "staff" ? "staff" : "player");

// Roster history as horizontal spans, one row per person, players ordered by
// first arrival and then staff. Identity lives in the row label, so bars share
// one neutral fill; time on the bench is a dashed outline on the player's row.
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

  // one row per person per group, stints merged onto it
  type Row = { handle: string; slug: string; group: RowGroup; spans: TeamStint[] };
  const byKey = new Map<string, Row>();
  for (const s of stints) {
    const group = rowGroup(rosterGroup(s.role));
    const key = `${s.playerId}-${group}`;
    let p = byKey.get(key);
    if (!p) {
      p = { handle: s.handle, slug: s.slug, group, spans: [] };
      byKey.set(key, p);
    }
    p.spans.push(s);
  }
  const grouped = GROUP_ORDER.map((g) => ({
    group: g,
    rows: [...byKey.values()].filter((p) => p.group === g),
  })).filter((g) => g.rows.length > 0);
  // Headings only appear once there is more than one group to tell apart.
  const headed = grouped.length > 1;
  type Line = { kind: "head"; group: RowGroup } | { kind: "row"; row: Row };
  const lines: Line[] = grouped.flatMap((g) => [
    ...(headed ? [{ kind: "head" as const, group: g.group }] : []),
    ...g.rows.map((row) => ({ kind: "row" as const, row })),
  ]);
  const staffRole = (p: Row) => {
    const roles = [...new Set(p.spans.map((s) => s.role).filter(Boolean))];
    return roles.join(", ");
  };

  const W = 720;
  const LABEL = 110;
  const ROW = 20;
  const BAR = 10;
  const M = { top: 4, right: 12, bottom: 22 };
  const H = M.top + lines.length * ROW + M.bottom;
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
          aria-label="Roster stints over time, one row per person"
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
          {lines.map((line, i) => {
            const yMid = M.top + i * ROW + ROW / 2;
            if (line.kind === "head") {
              return (
                <text
                  key={`head-${line.group}`}
                  x={0}
                  y={yMid + 4}
                  fontSize={9}
                  fill="var(--ink-muted)"
                  className="font-mono"
                  style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}
                >
                  {GROUP_LABEL[line.group]}
                </text>
              );
            }
            const p = line.row;
            const yTop = M.top + i * ROW + (ROW - BAR) / 2;
            return (
              <g key={`${p.slug}-${p.group}`}>
                <Link href={`/players/${p.slug}`}>
                  <text
                    x={LABEL - 10}
                    y={yMid + 3.5}
                    textAnchor="end"
                    fontSize={11}
                    fill="var(--ink-secondary)"
                    className="hover:fill-[var(--accent)]"
                  >
                    {p.handle}
                    {p.group === "staff" && <title>{staffRole(p)}</title>}
                  </text>
                </Link>
                {p.spans.map((s, j) => {
                  const a = parse(s.startDate);
                  const b = s.endDate ? parse(s.endDate) : t1;
                  const w = Math.max(2, x(b) - x(a));
                  const kind = rosterGroup(s.role);
                  return (
                    <g key={j}>
                      <rect
                        x={x(a)}
                        y={yTop}
                        width={w}
                        height={BAR}
                        fill={kind === "player" ? "var(--surface-raised)" : "transparent"}
                        stroke="var(--baseline)"
                        strokeWidth={1}
                        strokeDasharray={kind === "bench" ? "3 2" : undefined}
                      >
                        <title>
                          {`${p.handle}${s.role ? ` · ${s.role}` : ""} · ${s.startDate.slice(0, 10)} – ${s.endDate ? s.endDate.slice(0, 10) : "present"}`}
                        </title>
                      </rect>
                      {kind === "staff" && w > 60 && s.role !== p.spans[j - 1]?.role && (
                        <text
                          x={x(a) + 5}
                          y={yTop + BAR - 2}
                          fontSize={8}
                          fill="var(--ink-muted)"
                        >
                          {s.role}
                        </text>
                      )}
                    </g>
                  );
                })}
              </g>
            );
          })}
        </svg>
      </div>
      {stints.some((s) => rosterGroup(s.role) === "bench") && (
        <p className="mt-2 text-xs text-ink-muted">
          Dashed spans are time as a substitute, on loan, or inactive. Hover a
          span for its role and dates.
        </p>
      )}
    </div>
  );
}
