"use client";

import { AddColumnMenu, type MetricOption } from "./AddColumnMenu";
import { useReportUrl } from "./reportUrl";

/**
 * The add control for the blank slate. With no columns there is no table, and
 * so no header row to hang the `+` cell off — this is the one place it needs a
 * home of its own.
 */
export function AddFirstColumn({
  catalog,
}: {
  catalog: MetricOption[];
}) {
  const push = useReportUrl();
  return (
    <AddColumnMenu
      catalog={catalog}
      selected={[]}
      onAdd={(key) => push({ metrics: key, preset: null })}
    />
  );
}
