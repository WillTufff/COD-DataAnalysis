"use client";

import Link from "next/link";
import { type Column, DataTable } from "@/components/table/DataTable";
import type { SortState } from "@/components/table/tableState";
import type { Per } from "@/lib/paging";
import type { PlayerIndexRow } from "@/lib/analytics";
import { teamSlug } from "@/lib/slug";

const NUM =
  "font-mono tabular-nums text-ink-secondary" as const;

const COLUMNS: Column<PlayerIndexRow>[] = [
  {
    id: "handle",
    header: "Player",
    cellClassName: "font-medium",
    sortable: true,
    sortDir: "asc",
    sortValue: (r) => r.handle,
    render: (r) => (
      <Link
        href={`/players/${r.slug}`}
        className="hover:text-accent hover:underline"
      >
        {r.handle}
      </Link>
    ),
  },
  {
    id: "last_year",
    header: "Active",
    cellClassName: "font-mono text-xs tabular-nums text-ink-secondary",
    sortable: true,
    sortDir: "desc",
    sortValue: (r) => r.lastYear,
    render: (r) =>
      r.firstYear === r.lastYear ? r.firstYear : `${r.firstYear}–${r.lastYear}`,
  },
  {
    id: "team",
    header: "Team",
    cellClassName: "text-ink-secondary",
    render: (r) =>
      r.latestTeam ? (
        <Link
          href={`/teams/${teamSlug(r.latestTeam)}`}
          className="hover:text-accent hover:underline"
        >
          {r.latestTeam}
        </Link>
      ) : (
        "—"
      ),
  },
  {
    id: "teams",
    header: "Teams",
    align: "right",
    cellClassName: NUM,
    sortable: true,
    sortDir: "desc",
    sortValue: (r) => r.teamCount,
    render: (r) => r.teamCount || "—",
  },
  {
    id: "seasons",
    header: "Seasons",
    align: "right",
    cellClassName: NUM,
    sortable: true,
    sortDir: "desc",
    sortValue: (r) => r.seasons,
    render: (r) => r.seasons,
  },
  {
    id: "maps",
    header: "Maps",
    align: "right",
    cellClassName: "font-mono tabular-nums",
    sortable: true,
    sortDir: "desc",
    sortValue: (r) => r.maps,
    render: (r) => r.maps,
  },
  {
    id: "rating",
    header: "Best rating",
    align: "right",
    cellClassName: "font-mono tabular-nums",
    sortable: true,
    sortDir: "desc",
    sortValue: (r) => r.bestRating,
    render: (r) =>
      r.bestRating !== null ? (
        <>
          {r.bestRating.toFixed(2)}
          <span className="ml-1 text-ink-muted">{r.bestRatingYear}</span>
        </>
      ) : (
        "—"
      ),
  },
];

const DEFAULT_SORT: SortState = { id: "rating", dir: "desc" };

export function PlayersIndexTable({
  rows,
  initialPer,
  initialPage,
  initialSort,
}: {
  rows: PlayerIndexRow[];
  initialPer: Per;
  initialPage: number;
  initialSort: SortState;
}) {
  return (
    <div className="mt-6">
      <DataTable
        rows={rows}
        columns={COLUMNS}
        rowKey={(r) => String(r.playerId)}
        rank
        unit="players"
        initialPer={initialPer}
        initialPage={initialPage}
        initialSort={initialSort}
        defaultSort={DEFAULT_SORT}
      />
    </div>
  );
}
