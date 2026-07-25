"use client";

import { useSearchParams } from "next/navigation";

// Data formats link to the export route carrying the live URL state; PDF is
// handled client-side by the browser's print dialog against the print
// stylesheet, so it needs no server render.
const DATA_FORMATS: { format: string; label: string }[] = [
  { format: "csv", label: "CSV" },
  { format: "xlsx", label: "Excel" },
  { format: "json", label: "JSON" },
  { format: "xml", label: "XML" },
];

/**
 * The export bar. Each data format is a plain download link to
 * `/stats/export?…&format=…` built from whatever the URL currently holds — the
 * columns, cohort and sort the reader is looking at — so a download always
 * matches the on-screen table. PDF prints the page.
 */
export function ExportBar() {
  const searchParams = useSearchParams();

  const href = (format: string) => {
    const params = new URLSearchParams(searchParams.toString());
    // Paging is a screen concern; the export is always the full report.
    params.delete("per");
    params.delete("page");
    params.set("format", format);
    return `/stats/export?${params.toString()}`;
  };

  return (
    <div className="flex flex-wrap items-center gap-2 text-xs print:hidden">
      <span className="text-ink-muted">Export</span>
      {DATA_FORMATS.map((f) => (
        <a
          key={f.format}
          href={href(f.format)}
          className="border border-hairline px-2.5 py-1 hover:border-accent hover:text-accent"
        >
          {f.label}
        </a>
      ))}
      <button
        type="button"
        onClick={() => window.print()}
        className="border border-hairline px-2.5 py-1 hover:border-accent hover:text-accent"
      >
        PDF
      </button>
    </div>
  );
}
