// Native serializers for the export matrix — no dependencies. Each takes the
// one `ExportMatrix` and renders a format; because they share that input they
// can never disagree about the report's contents.

import { type ExportMatrix } from "./export";

function cell(v: string | number | null): string {
  return v === null ? "" : String(v);
}

/** RFC 4180 CSV. Fields with a comma, quote, or newline are quoted; quotes doubled. */
export function toCsv(matrix: ExportMatrix): string {
  const esc = (v: string | number | null): string => {
    const s = cell(v);
    return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [matrix.headers.map(esc).join(",")];
  for (const row of matrix.rows) lines.push(row.map(esc).join(","));
  // BOM-prefixed: without it Excel opens a double-clicked .csv as the system
  // codepage and mangles any non-ASCII handle. CRLF endings, as the spec prefers.
  return "\uFEFF" + lines.join("\r\n");
}

/**
 * Self-describing JSON — doubles as a small open-data API. `meta` carries the
 * run version and cohort; `columns` describes each metric; `rows` are arrays
 * aligned to `headers`.
 */
export function toJson(matrix: ExportMatrix): string {
  return JSON.stringify(
    {
      meta: matrix.meta,
      headers: matrix.headers,
      columns: matrix.columns,
      rows: matrix.rows,
    },
    null,
    2,
  );
}

function xmlEscape(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** A plain XML tree: <report> → <rows> → <row> → <cell field="…">value</cell>. */
export function toXml(matrix: ExportMatrix): string {
  const { meta } = matrix;
  const parts: string[] = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    "<report>",
    `  <meta generatedAt="${xmlEscape(meta.generatedAt)}" entity="${meta.entity}" run="${xmlEscape(
      `${meta.run.model} v${meta.run.version}`,
    )}" seasons="${xmlEscape(
      Array.isArray(meta.cohort.seasons)
        ? meta.cohort.seasons.join(",")
        : meta.cohort.seasons,
    )}" mode="${xmlEscape(
      meta.cohort.mode,
    )}" players="${xmlEscape(
      Array.isArray(meta.cohort.players)
        ? meta.cohort.players.join(",")
        : meta.cohort.players,
    )}" teams="${xmlEscape(
      Array.isArray(meta.cohort.teams)
        ? meta.cohort.teams.join(",")
        : meta.cohort.teams,
    )}" sort="${xmlEscape(meta.sort)}" dir="${meta.dir}" qualifiedOnly="${meta.qualifiedOnly}" rowCount="${meta.rowCount}"${meta.truncated ? ' truncated="true"' : ""}/>`,
    "  <columns>",
    ...matrix.columns.map(
      (c) =>
        `    <column key="${xmlEscape(c.key)}" label="${xmlEscape(c.label)}" unit="${xmlEscape(c.unit)}" higherIsBetter="${c.higherIsBetter}"/>`,
    ),
    "  </columns>",
    "  <rows>",
  ];
  for (const row of matrix.rows) {
    parts.push("    <row>");
    row.forEach((v, i) => {
      parts.push(
        `      <cell field="${xmlEscape(matrix.headers[i] ?? "")}">${xmlEscape(cell(v))}</cell>`,
      );
    });
    parts.push("    </row>");
  }
  parts.push("  </rows>", "</report>");
  return parts.join("\n");
}
