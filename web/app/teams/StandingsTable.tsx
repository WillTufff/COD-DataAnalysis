"use client";

import { useMemo } from "react";
import Link from "next/link";
import { Sparkline } from "@/components/charts/Sparkline";
import { type Column, DataTable } from "@/components/table/DataTable";
import type { Per } from "@/lib/paging";

export type StandingRow = {
  teamId: number;
  team: string;
  slug: string;
  finalElo: number;
  peakElo: number;
  glicko: number | null;
  glickoRd: number | null;
  rec: { wins: number; losses: number } | null;
  spark: number[] | null;
  lastPlayedIso: string | null;
};

export function StandingsTable({
  rows,
  sparkDomain,
  initialPer,
  initialPage,
}: {
  rows: StandingRow[];
  sparkDomain: [number, number];
  initialPer: Per;
  initialPage: number;
}) {
  const columns = useMemo<Column<StandingRow>[]>(
    () => [
      {
        id: "team",
        header: "Team",
        cellClassName: "font-medium",
        render: (t) => (
          <Link
            href={`/teams/${t.slug}`}
            className="hover:text-accent hover:underline"
          >
            {t.team}
          </Link>
        ),
      },
      {
        id: "trajectory",
        header: "Trajectory",
        render: (t) =>
          t.spark && t.spark.length > 1 ? (
            <Sparkline
              values={t.spark}
              domain={sparkDomain}
              label={`${t.team} Elo trajectory`}
            />
          ) : null,
      },
      {
        id: "elo",
        header: "Elo",
        align: "right",
        cellClassName: "font-mono tabular-nums",
        render: (t) => t.finalElo.toFixed(0),
      },
      {
        id: "peak",
        header: "Peak",
        align: "right",
        cellClassName: "font-mono tabular-nums text-ink-secondary",
        render: (t) => t.peakElo.toFixed(0),
      },
      {
        id: "glicko",
        header: "Glicko-2 ± RD",
        align: "right",
        cellClassName: "font-mono tabular-nums",
        render: (t) =>
          t.glicko !== null ? (
            <>
              {t.glicko.toFixed(0)}
              <span className="text-ink-muted"> ±{t.glickoRd?.toFixed(0)}</span>
            </>
          ) : (
            "—"
          ),
      },
      {
        id: "record",
        header: "Series W–L",
        align: "right",
        cellClassName: "font-mono tabular-nums text-ink-secondary",
        render: (t) => (t.rec ? `${t.rec.wins}–${t.rec.losses}` : "—"),
      },
      {
        id: "last",
        header: "Last rated",
        align: "right",
        cellClassName: "font-mono text-xs tabular-nums text-ink-muted",
        render: (t) => (t.lastPlayedIso ? t.lastPlayedIso.slice(0, 10) : "—"),
      },
    ],
    [sparkDomain],
  );

  return (
    <DataTable
      rows={rows}
      columns={columns}
      rowKey={(t) => String(t.teamId)}
      rank
      unit="teams"
      initialPer={initialPer}
      initialPage={initialPage}
    />
  );
}
