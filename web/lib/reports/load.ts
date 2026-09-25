// The rows behind a resolved report, from whichever path computes them: the
// published season rows, or a re-aggregation over map rows when the pick is a
// mix of modes or a combined span. The page and the export both load through
// here, so a download reads the same numbers as the screen.

import {
  type MetricCatalog,
  type ReportColumn,
  type ReportRow,
  queryReport,
  queryTeamReport,
} from "@/lib/analytics";
import { queryAggregateReport } from "./aggregate";
import type { ResolvedReport } from "./resolve";

export async function loadReport(
  runId: number,
  resolved: ResolvedReport,
  catalog: MetricCatalog,
  modeName: (slug: string) => string,
): Promise<{ columns: ReportColumn[]; rows: ReportRow[] }> {
  if (resolved.aggregate) {
    return queryAggregateReport(
      resolved.aggregate,
      resolved.entity,
      resolved.selectedEntries,
      catalog.map_keys ?? {},
      modeName,
    );
  }
  return resolved.entity === "teams"
    ? queryTeamReport(runId, resolved.query, resolved.selectedEntries)
    : queryReport(runId, resolved.query, resolved.selectedEntries);
}
