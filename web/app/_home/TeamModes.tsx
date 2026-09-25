"use client";

import Link from "next/link";
import { useState } from "react";
import type { TeamModeRow } from "./queries";

const SHORT: Record<string, string> = {
  Hardpoint: "Hardpoint",
  "Search & Destroy": "S&D",
  Overload: "Overload",
  Control: "Control",
};

function money(n: number) {
  if (!n) return "—";
  return n >= 1e6 ? `$${(n / 1e6).toFixed(2)}M` : `$${Math.round(n / 1000)}K`;
}

function cell(p: number) {
  const d = Math.min(Math.abs(p - 0.5) / 0.25, 1);
  const c = p >= 0.5 ? "52, 169, 95" : "208, 59, 59";
  return `rgba(${c}, ${0.12 + d * 0.6})`;
}

function Th({
  id,
  sort,
  onSort,
  children,
}: {
  id: string;
  sort: string;
  onSort: (id: string) => void;
  children: React.ReactNode;
}) {
  return (
    <th className="px-1 py-2 text-center font-normal">
      <button
        onClick={() => onSort(id)}
        className={`text-xs ${sort === id ? "text-ink underline underline-offset-4" : "text-ink-muted hover:text-ink-secondary"}`}
      >
        {children}
      </button>
    </th>
  );
}

export function TeamModes({ modes, rows }: { modes: string[]; rows: TeamModeRow[] }) {
  const [sort, setSort] = useState<string>("all");
  const pct = (r: TeamModeRow, m: string) => {
    if (m === "all") {
      const v = Object.values(r.modes);
      const maps = v.reduce((a, x) => a + x.maps, 0);
      return maps ? v.reduce((a, x) => a + x.wins, 0) / maps : 0;
    }
    if (m === "prize") return r.prize;
    const x = r.modes[m];
    return x && x.maps ? x.wins / x.maps : 0;
  };
  const sorted = [...rows].sort((a, b) => pct(b, sort) - pct(a, sort));
  const cols = [...modes, "all"];

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-hairline">
            <th className="py-2 pr-3 text-left text-xs font-normal text-ink-muted">Team</th>
            {modes.map((m) => (
              <Th key={m} id={m} sort={sort} onSort={setSort}>
                {SHORT[m] ?? m}
              </Th>
            ))}
            <Th id="all" sort={sort} onSort={setSort}>All maps</Th>
            <Th id="prize" sort={sort} onSort={setSort}>Prize money</Th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.team} className="border-b border-background">
              <td className="whitespace-nowrap py-0.5 pr-3">
                <Link href={`/teams/${r.slug}`} className="hover:text-accent">
                  {r.team}
                </Link>
              </td>
              {cols.map((m) => {
                const x =
                  m === "all"
                    ? Object.values(r.modes).reduce(
                        (a, v) => ({ maps: a.maps + v.maps, wins: a.wins + v.wins }),
                        { maps: 0, wins: 0 },
                      )
                    : r.modes[m];
                if (!x || !x.maps) return <td key={m} className="text-center text-ink-muted">—</td>;
                const p = x.wins / x.maps;
                return (
                  <td key={m} className="px-0.5 py-0.5">
                    <div
                      className="group relative flex h-8 items-center justify-center font-mono text-xs tabular-nums"
                      style={{ background: cell(p) }}
                      title={`${x.wins}–${x.maps - x.wins} in ${m === "all" ? "all maps" : m}`}
                    >
                      <span className="group-hover:hidden">{Math.round(p * 100)}%</span>
                      <span className="hidden text-ink group-hover:inline">
                        {x.wins}–{x.maps - x.wins}
                      </span>
                    </div>
                  </td>
                );
              })}
              <td className="px-2 text-right font-mono text-xs tabular-nums text-ink-secondary">{money(r.prize)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-xs text-ink-muted">
        Map win rate by mode. Hover a cell for the record; click a column to sort.
      </p>
    </div>
  );
}
