// Download the current report. Same URL contract as the page (metrics, cohort,
// sort, qualified gate), resolved through the same `resolveReport`, so the file
// mirrors the on-screen table. Not cached — the report is request-shaped.

import {
  getMetricCatalog,
  getTeamMetricCatalog,
  latestRun,
  queryReport,
  queryTeamReport,
  getModeCatalog,
} from "@/lib/analytics";
import { buildExportMatrix, cohortSlug } from "@/lib/reports/export";
import { parseEntity, resolveReportForUrl } from "@/lib/reports/resolve";
import { toCsv, toJson, toXml } from "@/lib/reports/serialize";
import { buildXlsx } from "@/lib/reports/xlsx";

export const dynamic = "force-dynamic";

type Serialized = {
  body: string | Uint8Array<ArrayBuffer>;
  contentType: string;
  ext: string;
};

const SERIALIZERS: Record<
  string,
  (matrix: ReturnType<typeof buildExportMatrix>) => Serialized
> = {
  csv: (m) => ({
    body: toCsv(m),
    contentType: "text/csv; charset=utf-8",
    ext: "csv",
  }),
  json: (m) => ({
    body: toJson(m),
    contentType: "application/json; charset=utf-8",
    ext: "json",
  }),
  xml: (m) => ({
    body: toXml(m),
    contentType: "application/xml; charset=utf-8",
    ext: "xml",
  }),
  xlsx: (m) => ({
    body: buildXlsx(m),
    contentType:
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ext: "xlsx",
  }),
};

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const format = (searchParams.get("format") ?? "csv").toLowerCase();
  const serialize = SERIALIZERS[format];
  if (!serialize) {
    return new Response(`Unsupported format: ${format}`, { status: 400 });
  }

  const run = await latestRun("metric_layer");
  const catalog = run ? await getMetricCatalog(run.id) : null;
  if (!run || !catalog) {
    return new Response("No metric run has been published.", { status: 503 });
  }

  const sp = Object.fromEntries(searchParams);
  const entity = parseEntity(sp);
  const metrics =
    entity === "teams"
      ? await getTeamMetricCatalog(run.id)
      : catalog.metrics.filter((m) => m.titles.length > 0);
  // The same entry point the page uses, so a bare download resolves the report
  // a bare visit renders rather than an empty set.
  const resolved = await resolveReportForUrl(run.id, sp, metrics);
  if (resolved.selected.length === 0) {
    return new Response("Select at least one metric column to export.", {
      status: 400,
    });
  }

  const { columns, rows } =
    resolved.entity === "teams"
      ? await queryTeamReport(run.id, resolved.query, resolved.selectedEntries)
      : await queryReport(run.id, resolved.query, resolved.selectedEntries);
  const matrix = buildExportMatrix(
    resolved,
    columns,
    rows,
    { model: "metric_layer", version: run.version },
    await getModeCatalog(),
  );

  const { body, contentType, ext } = serialize(matrix);
  // Date-stamped so repeated exports of the same cohort don't collide on disk.
  const stamp = matrix.meta.generatedAt.slice(0, 10);
  return new Response(body, {
    headers: {
      "Content-Type": contentType,
      "Content-Disposition": `attachment; filename="cdlhub-report-${cohortSlug(resolved)}-${stamp}.${ext}"`,
      // The matrix caps rows; surface it in a header so a truncated file is
      // never silently short.
      ...(matrix.meta.truncated
        ? { "X-Report-Truncated": String(matrix.meta.rowCount) }
        : {}),
    },
  });
}
