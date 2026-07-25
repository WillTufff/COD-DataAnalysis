"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { PctlBar } from "@/components/PctlBar";
import { type Column, DataTable } from "@/components/table/DataTable";
import type { Per } from "@/lib/paging";
import type { SortState } from "@/components/table/tableState";
import type { ReportColumn, ReportRow } from "@/lib/analytics";

const MODE_LABELS: Record<string, string> = {
  hardpoint: "Hardpoint",
  "search-and-destroy": "Search & Destroy",
  control: "Control",
  "capture-the-flag": "Capture the Flag",
  uplink: "Uplink",
};

// Mirrors the single-metric table's formatting: shares render as percentages,
// everything else scales its precision to its magnitude.
function formatValue(v: number, unit: string): string {
  if (unit.startsWith("share")) return `${(v * 100).toFixed(1)}%`;
  if (Math.abs(v) >= 100) return v.toFixed(0);
  if (Math.abs(v) >= 10) return v.toFixed(2);
  return v.toFixed(3);
}

// How every metric cell renders. A wide report is hard to read as raw numbers,
// so the same cells can be flipped to their within-cohort percentile or z-score.
type CellMode = "value" | "pctl" | "z";

const CELL_MODES: { id: CellMode; label: string }[] = [
  { id: "value", label: "Value" },
  { id: "pctl", label: "Percentile" },
  { id: "z", label: "vs cohort" },
];

/** One metric cell, honouring the display mode and greying below-minimum samples. */
function Cell({
  cell,
  col,
  mode,
}: {
  cell: ReportRow["cells"][string] | undefined;
  col: ReportColumn;
  mode: CellMode;
}) {
  // An absent cell — this column does not cover the row's season/mode.
  if (!cell) return <span className="text-ink-muted">—</span>;

  let body: React.ReactNode;
  if (mode === "pctl") {
    body = cell.pctl !== null ? <PctlBar pctl={cell.pctl} /> : "—";
  } else if (mode === "z") {
    body =
      cell.z !== null ? `${cell.z >= 0 ? "+" : ""}${cell.z.toFixed(2)}σ` : "—";
  } else {
    body = formatValue(cell.value, col.unit);
  }

  // Below its own sample minimum: shown, but greyed and scored against the
  // qualified cohort rather than trusted on its own.
  return (
    <span
      className={cell.qualified ? "" : "text-ink-muted"}
      title={
        cell.qualified
          ? undefined
          : `Below the ${col.minDenom} ${col.denomKind} minimum`
      }
    >
      {body}
    </span>
  );
}

export function ReportTable({
  columns,
  rows,
  showMode,
  initialPer,
  initialPage,
  initialSort,
  defaultSort,
}: {
  columns: ReportColumn[];
  rows: ReportRow[];
  showMode: boolean;
  initialPer: Per;
  initialPage: number;
  initialSort: SortState;
  defaultSort: SortState;
}) {
  const [cellMode, setCellMode] = useState<CellMode>("value");

  const tableColumns = useMemo<Column<ReportRow>[]>(() => {
    const cols: Column<ReportRow>[] = [
      {
        id: "player",
        header: "Player",
        sortable: true,
        sortDir: "asc",
        sortValue: (r) => r.handle,
        render: (r) => (
          <Link
            href={`/players/${r.slug}`}
            className="font-medium hover:text-accent"
          >
            {r.handle}
          </Link>
        ),
      },
      {
        id: "season",
        header: "Season",
        cellClassName: "whitespace-nowrap text-ink-secondary",
        render: (r) => `${r.year} ${r.title}`,
      },
    ];
    if (showMode) {
      cols.push({
        id: "mode",
        header: "Mode",
        cellClassName: "text-ink-secondary",
        render: (r) => (r.mode ? (MODE_LABELS[r.mode] ?? r.mode) : "All"),
      });
    }
    for (const col of columns) {
      cols.push({
        id: col.key,
        header: (
          <span title={col.higherIsBetter ? undefined : "Lower is better"}>
            {col.label}
            {col.higherIsBetter ? "" : " ↓"}
          </span>
        ),
        align: "right",
        cellClassName: "font-mono tabular-nums",
        sortable: true,
        // Best-first on first click: descending when higher is better.
        sortDir: col.higherIsBetter ? "desc" : "asc",
        sortValue: (r) => r.cells[col.key]?.value ?? null,
        render: (r) => <Cell cell={r.cells[col.key]} col={col} mode={cellMode} />,
      });
    }
    return cols;
  }, [columns, showMode, cellMode]);

  return (
    <div className="mt-4">
      <div className="mb-3 flex items-center gap-2 text-xs print:hidden">
        <span className="text-ink-muted">Show cells as</span>
        <div className="inline-flex overflow-hidden border border-hairline">
          {CELL_MODES.map((m) => (
            <button
              key={m.id}
              type="button"
              onClick={() => setCellMode(m.id)}
              className={`px-2.5 py-1 ${
                cellMode === m.id
                  ? "bg-surface-raised text-ink"
                  : "text-ink-muted hover:text-ink"
              }`}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>
      <DataTable
        rows={rows}
        columns={tableColumns}
        rowKey={(r) => `${r.playerId}-${r.year}-${r.mode ?? "all"}`}
        rank
        initialPer={initialPer}
        initialPage={initialPage}
        initialSort={initialSort}
        defaultSort={defaultSort}
      />
    </div>
  );
}
