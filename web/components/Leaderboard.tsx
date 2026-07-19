"use client";

import Link from "next/link";
import { useState } from "react";
import { PctlBar } from "@/components/PctlBar";
import type { LeaderboardRow } from "@/lib/analytics";

// Player-season leaderboard with the raw-vs-adjusted toggle. "Adjusted" ranks
// by cohort z-score (comparable across titles); "raw" ranks by plain K/D and
// shows how era pace distorts cross-year comparison.
export function Leaderboard({
  rows,
  limit = 25,
}: {
  rows: LeaderboardRow[];
  limit?: number;
}) {
  const [mode, setMode] = useState<"adjusted" | "raw">("adjusted");

  const sorted = [...rows].sort((a, b) =>
    mode === "adjusted"
      ? (b.kdZ ?? -Infinity) - (a.kdZ ?? -Infinity)
      : (b.kdRaw ?? -Infinity) - (a.kdRaw ?? -Infinity),
  );
  const top = sorted.slice(0, limit);

  return (
    <div>
      <div className="mb-3 flex items-center gap-1 text-xs" role="tablist">
        {(["adjusted", "raw"] as const).map((m) => (
          <button
            key={m}
            role="tab"
            aria-selected={mode === m}
            onClick={() => setMode(m)}
            className={`border px-2.5 py-1 transition-colors ${
              mode === m
                ? "border-accent-dim bg-surface-raised text-ink"
                : "border-hairline text-ink-muted hover:text-ink-secondary"
            }`}
          >
            {m === "adjusted" ? "Era-adjusted" : "Raw K/D"}
          </button>
        ))}
        <span className="ml-2 text-ink-muted">
          {mode === "adjusted"
            ? "ranked by cohort z-score, comparable across titles"
            : "ranked by unadjusted K/D, which favors slower-paced titles"}
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-hairline text-xs text-ink-muted">
              <th className="py-2 pr-3 font-normal">#</th>
              <th className="py-2 pr-4 font-normal">Player</th>
              <th className="py-2 pr-4 font-normal">Season</th>
              <th className="py-2 pr-4 text-right font-normal">Maps</th>
              <th
                className={`py-2 pr-4 text-right font-normal ${mode === "raw" ? "text-ink" : ""}`}
              >
                K/D
              </th>
              <th
                className={`py-2 pr-4 text-right font-normal ${mode === "adjusted" ? "text-ink" : ""}`}
              >
                vs cohort
              </th>
              <th className="py-2 font-normal">Percentile</th>
            </tr>
          </thead>
          <tbody>
            {top.map((r, i) => (
              <tr
                key={`${r.playerId}-${r.year}`}
                className="border-b border-hairline/60"
              >
                <td className="py-1.5 pr-3 font-mono text-xs tabular-nums text-ink-muted">
                  {i + 1}
                </td>
                <td className="py-1.5 pr-4">
                  <Link
                    href={`/players/${r.slug}`}
                    className="font-medium hover:text-accent"
                  >
                    {r.handle}
                  </Link>
                </td>
                <td className="py-1.5 pr-4 text-ink-secondary">
                  {r.year} {r.title}
                </td>
                <td className="py-1.5 pr-4 text-right font-mono tabular-nums">
                  {r.mapsPlayed}
                </td>
                <td className="py-1.5 pr-4 text-right font-mono tabular-nums">
                  {r.kdRaw?.toFixed(2) ?? "—"}
                </td>
                <td className="py-1.5 pr-4 text-right font-mono tabular-nums">
                  {r.kdZ !== null ? `${r.kdZ >= 0 ? "+" : ""}${r.kdZ.toFixed(2)}σ` : "—"}
                </td>
                <td className="py-1.5">
                  {r.kdPctl !== null ? <PctlBar pctl={r.kdPctl} /> : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-ink-muted">
        Qualified player-seasons only (≥ 30 maps, all modes combined). Percentile is
        within that season-and-title cohort, so a 90th in 2017 IW and a 90th in 2019
        BO4 mean the same thing.
      </p>
    </div>
  );
}
