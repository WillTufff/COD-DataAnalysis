"use client";

import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useReportUrl } from "./reportUrl";

export type CellMode = "value" | "pctl" | "z";

export const CELL_MODES: { id: CellMode; label: string }[] = [
  { id: "value", label: "Value" },
  { id: "pctl", label: "Percentile" },
  { id: "z", label: "vs cohort" },
];

// Each format links to the export route carrying the live URL state, so the
// file always matches the on-screen report.
const DATA_FORMATS: { format: string; label: string }[] = [
  { format: "csv", label: "CSV" },
  { format: "xlsx", label: "Excel" },
  { format: "json", label: "JSON" },
  { format: "xml", label: "XML" },
];

/** The export menu: one control instead of a row of format buttons. */
function ExportMenu() {
  const searchParams = useSearchParams();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(e: PointerEvent) {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const href = (format: string) => {
    const params = new URLSearchParams(searchParams.toString());
    // Paging is a screen concern; the export is always the full report.
    params.delete("per");
    params.delete("page");
    params.set("format", format);
    return `/stats/export?${params.toString()}`;
  };

  return (
    <div ref={ref} className="relative">
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className={`border px-2.5 py-1 transition-colors motion-reduce:transition-none ${
          open
            ? "border-accent text-accent"
            : "border-hairline text-ink-secondary hover:border-accent-dim hover:text-accent"
        }`}
      >
        Export <span aria-hidden="true">▾</span>
      </button>
      {open && (
        <div className="absolute right-0 top-full z-20 mt-1 w-32 border border-hairline bg-surface py-1 shadow-lg">
          {DATA_FORMATS.map((f) => (
            <a
              key={f.format}
              href={href(f.format)}
              onClick={() => setOpen(false)}
              className="block px-2.5 py-1.5 text-ink-secondary hover:bg-surface-raised hover:text-ink"
            >
              {f.label}
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * The report's toolbar, above the table with the other controls: what the
 * report contains, the two switches that change how it reads, and the way out
 * of the page.
 */
export function ReportToolbar({
  rowCount,
  columnCount,
  qualifiedOnly,
  cellMode,
  setCellMode,
}: {
  rowCount: number;
  columnCount: number;
  qualifiedOnly: boolean;
  cellMode: CellMode;
  setCellMode: (mode: CellMode) => void;
}) {
  const push = useReportUrl();

  return (
    <div className="flex flex-wrap items-center justify-between gap-x-5 gap-y-2 border border-hairline px-3 py-2 text-xs print:hidden">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="font-mono tabular-nums text-ink-muted">
          {rowCount.toLocaleString()} row{rowCount === 1 ? "" : "s"} ·{" "}
          {columnCount} column{columnCount === 1 ? "" : "s"}
        </span>
        <label className="flex cursor-pointer items-center gap-2 text-ink-muted hover:text-ink">
          <input
            type="checkbox"
            checked={!qualifiedOnly}
            onChange={(e) => push({ all: e.target.checked ? "1" : null })}
            className="accent-[var(--accent)]"
          />
          Include small samples
        </label>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div className="inline-flex border border-hairline">
          {CELL_MODES.map((m) => (
            <button
              key={m.id}
              type="button"
              aria-pressed={cellMode === m.id}
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
        <ExportMenu />
      </div>
    </div>
  );
}
