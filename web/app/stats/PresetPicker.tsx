import Link from "next/link";

export type PresetTile = {
  id: string;
  name: string;
  blurb: string;
  category: string;
  columns: number;
};

/**
 * The discovery layer over the builder: curated column bundles as tiles, so a
 * reader can get a useful report in one click without meeting the full catalog.
 * Each tile links to `?preset=<id>`, which the page resolves to columns plus a
 * cohort default; editing any column then drops the preset. Pure links, so this
 * stays a server component.
 */
export function PresetPicker({
  presets,
  activeId,
}: {
  presets: PresetTile[];
  activeId?: string;
}) {
  const groups = new Map<string, PresetTile[]>();
  for (const p of presets) {
    const list = groups.get(p.category) ?? [];
    list.push(p);
    groups.set(p.category, list);
  }

  return (
    <div className="flex flex-col gap-4">
      {[...groups.entries()].map(([category, list]) => (
        <div key={category}>
          <div className="mb-2 text-[10px] uppercase tracking-wide text-ink-muted">
            {category}
          </div>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {list.map((p) => {
              const active = p.id === activeId;
              return (
                <Link
                  key={p.id}
                  href={`/stats?preset=${p.id}`}
                  aria-current={active ? "true" : undefined}
                  className={`flex flex-col gap-1 border p-3 transition-colors ${
                    active
                      ? "border-accent bg-surface-raised"
                      : "border-hairline hover:border-accent-dim hover:bg-surface-raised"
                  }`}
                >
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="font-display text-sm font-semibold">
                      {p.name}
                    </span>
                    <span className="shrink-0 font-mono text-[10px] text-ink-muted">
                      {p.columns} col{p.columns === 1 ? "" : "s"}
                    </span>
                  </div>
                  <span className="text-xs text-ink-secondary">{p.blurb}</span>
                </Link>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
