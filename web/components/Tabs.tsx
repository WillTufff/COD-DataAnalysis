"use client";

import { useState, type ReactNode } from "react";

export function Tabs({
  tabs,
}: {
  tabs: { label: string; content: ReactNode }[];
}) {
  const [active, setActive] = useState(0);
  return (
    <div>
      <div role="tablist" className="flex gap-1 border-b border-hairline">
        {tabs.map((t, i) => (
          <button
            key={t.label}
            role="tab"
            aria-selected={i === active}
            onClick={() => setActive(i)}
            className={`eyebrow -mb-px border-b-2 px-3 py-2 transition-colors ${
              i === active
                ? "border-accent text-ink"
                : "border-transparent text-ink-muted hover:text-ink-secondary"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" className="pt-4">
        {tabs[active]?.content}
      </div>
    </div>
  );
}
