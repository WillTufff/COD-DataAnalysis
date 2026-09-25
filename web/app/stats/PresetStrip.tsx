import Link from "next/link";

export type PresetTile = {
  id: string;
  name: string;
  blurb: string;
  /** The preset's link, carrying the row filters the reader already set. */
  href: string;
  /** The seasons the preset's columns are published for, as a span. */
  span: string;
};

/**
 * The presets as one strip of links above the filters, so the entry point is
 * always in view and the active one is named. A preset seeds columns, sort and
 * mode; editing any column drops it, and the strip then marks the report as
 * custom. The line under the strip says what the active preset shows.
 */
export function PresetStrip({
  presets,
  activeId,
}: {
  presets: PresetTile[];
  activeId?: string;
}) {
  const active = presets.find((p) => p.id === activeId);
  return (
    <div className="print:hidden">
      <nav
        aria-label="Presets"
        className="-mx-4 flex gap-1.5 overflow-x-auto px-4 pb-1 sm:mx-0 sm:flex-wrap sm:px-0"
      >
        {presets.map((p) => {
          const on = p.id === activeId;
          return (
            <Link
              key={p.id}
              href={p.href}
              aria-current={on ? "true" : undefined}
              title={p.blurb}
              className={`shrink-0 whitespace-nowrap border px-2.5 py-1 text-xs transition-colors motion-reduce:transition-none ${
                on
                  ? "border-accent bg-surface-raised text-ink"
                  : "border-hairline text-ink-secondary hover:border-accent-dim hover:text-ink"
              }`}
            >
              {p.name}
            </Link>
          );
        })}
        {!active && (
          <span className="shrink-0 whitespace-nowrap border border-accent bg-surface-raised px-2.5 py-1 text-xs text-ink">
            Custom
          </span>
        )}
      </nav>
      <p className="mt-2 text-xs text-ink-muted">
        {active ? (
          <>
            {active.blurb} <span className="font-mono">· {active.span}</span>
          </>
        ) : (
          "Your own columns. Pick a preset to start over."
        )}
      </p>
    </div>
  );
}
