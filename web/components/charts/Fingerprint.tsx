import { Fragment } from "react";

export type FingerprintSeason = { year: number; title: string };

export type FingerprintCell = {
  pctl: number; // 0..1, already flipped so higher is better
  value: string;
} | null;

export type FingerprintRow = { label: string; cells: FingerprintCell[] };

export type FingerprintGroup = { label: string; rows: FingerprintRow[] };

// Blue sequential ramp (steps 700→100), dark-surface order: low percentiles
// recede toward the surface, high percentiles come forward.
const RAMP = [
  "#0d366b",
  "#104281",
  "#184f95",
  "#256abf",
  "#3987e5",
  "#6da7ec",
  "#9ec5f4",
  "#cde2fb",
];

// Ink flips to the dark surface color from step 400 up: every step then clears
// 4.5:1 against its cell (measured, not estimated).
const DARK_INK_FROM = 4;

function cellColor(pctl: number): { bg: string; darkInk: boolean } {
  const i = Math.min(RAMP.length - 1, Math.floor(pctl * RAMP.length));
  return { bg: RAMP[i], darkInk: i >= DARK_INK_FROM };
}

/**
 * Career fingerprint: headline metrics (rows, grouped by mode) × seasons
 * (columns), each cell colored by within-cohort percentile.
 */
export function Fingerprint({
  seasons,
  groups,
}: {
  seasons: FingerprintSeason[];
  groups: FingerprintGroup[];
}) {
  return (
    <div className="overflow-x-auto">
      <table className="border-separate text-left" style={{ borderSpacing: 2 }}>
        <thead>
          <tr>
            <th />
            {seasons.map((s) => (
              <th
                key={s.year}
                className="px-1 pb-1 text-center font-mono text-xs font-normal text-ink-muted"
              >
                {s.year}
                <span className="block text-[10px]">{s.title}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {groups.map((g) => (
            <Fragment key={g.label}>
              <tr>
                <td
                  colSpan={seasons.length + 1}
                  className="eyebrow pb-1 pt-3 text-[10px] text-ink-muted first:pt-0"
                >
                  {g.label}
                </td>
              </tr>
              {g.rows.map((r) => (
                <tr key={`${g.label}:${r.label}`}>
                  <td className="max-w-44 pr-3 text-xs leading-tight text-ink-secondary">
                    {r.label}
                  </td>
                  {r.cells.map((c, i) => (
                    <td key={seasons[i].year} className="p-0">
                      {c ? (
                        <div
                          className="flex h-7 w-14 items-center justify-center font-mono text-xs tabular-nums"
                          style={{
                            background: cellColor(c.pctl).bg,
                            color: cellColor(c.pctl).darkInk
                              ? "var(--surface)"
                              : "var(--ink)",
                          }}
                          title={`${r.label} · ${seasons[i].year} ${seasons[i].title}: ${c.value} (${Math.round(c.pctl * 100)}th percentile)`}
                        >
                          {Math.round(c.pctl * 100)}
                        </div>
                      ) : (
                        <div className="flex h-7 w-14 items-center justify-center bg-surface font-mono text-xs text-ink-muted">
                          —
                        </div>
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </Fragment>
          ))}
        </tbody>
      </table>
      <div className="mt-3 flex items-center gap-2 font-mono text-[10px] text-ink-muted">
        <span>0</span>
        <div className="flex">
          {RAMP.map((c) => (
            <div key={c} className="h-2 w-5" style={{ background: c }} />
          ))}
        </div>
        <span>100th percentile</span>
      </div>
    </div>
  );
}
