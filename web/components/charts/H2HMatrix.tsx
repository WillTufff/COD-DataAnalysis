import Link from "next/link";
import { teamSlug, type H2HCell } from "@/lib/analytics";

// Head-to-head grid over a set of teams: row team's series record against the
// column team. Cell tint is a two-hue diverging scale around an even record
// (blue = row team ahead, orange = behind); the printed record carries the
// exact number, so tint is never the only encoding.
export function H2HMatrix({
  teams,
  cells,
}: {
  teams: { teamId: number; team: string }[];
  cells: H2HCell[];
}) {
  if (teams.length < 2) return null;
  const byPair = new Map<string, H2HCell>();
  for (const c of cells) byPair.set(`${c.rowId}-${c.colId}`, c);
  // Abbreviate every column header so no header is wider than its cell —
  // otherwise header text stretches columns unevenly. Full name stays in title.
  const short = (name: string) => {
    if (name.length <= 4) return name;
    const caps = name.replace(/[^A-Z0-9]/g, "");
    return caps.length >= 2 ? caps.slice(0, 4) : name.slice(0, 3).toUpperCase();
  };

  return (
    <div className="overflow-x-auto">
      <table className="border-collapse text-xs">
        <thead>
          <tr>
            <th className="p-1" />
            {teams.map((t) => (
              <th
                key={t.teamId}
                className="p-1 pb-2 text-center font-normal text-ink-muted"
                title={t.team}
              >
                {short(t.team)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {teams.map((row) => (
            <tr key={row.teamId}>
              <th className="p-1 pr-3 text-right font-normal text-ink-secondary">
                <Link
                  href={`/teams/${teamSlug(row.team)}`}
                  className="hover:text-ink"
                >
                  {row.team}
                </Link>
              </th>
              {teams.map((col) => {
                if (row.teamId === col.teamId)
                  return (
                    <td key={col.teamId} className="p-0">
                      <div className="m-px flex h-9 w-12 items-center justify-center bg-surface" />
                    </td>
                  );
                const c = byPair.get(`${row.teamId}-${col.teamId}`);
                if (!c || c.wins + c.losses === 0)
                  return (
                    <td key={col.teamId} className="p-0">
                      <div className="m-px flex h-9 w-12 items-center justify-center text-ink-muted">
                        ·
                      </div>
                    </td>
                  );
                const share = c.wins / (c.wins + c.losses);
                const tint =
                  share === 0.5
                    ? "transparent"
                    : share > 0.5
                      ? "var(--series-1)"
                      : "var(--series-6)";
                const alpha = Math.abs(share - 0.5) * 2 * 0.45;
                return (
                  <td key={col.teamId} className="p-0">
                    <div
                      className="relative m-px flex h-9 w-12 items-center justify-center font-mono tabular-nums"
                      title={`${row.team} ${c.wins}–${c.losses} vs ${col.team}`}
                    >
                      <div
                        className="absolute inset-0"
                        style={{ background: tint, opacity: alpha }}
                      />
                      <span className="relative">
                        {c.wins}–{c.losses}
                      </span>
                    </div>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-xs text-ink-muted">
        Row team&rsquo;s decided-series record against the column team. Blue
        tint: row team ahead. Orange tint: behind. A dot means the two never
        played a decided series.
      </p>
    </div>
  );
}
