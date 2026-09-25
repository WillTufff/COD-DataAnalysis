import { ordinal } from "@/lib/ordinal";
import type { ReportView } from "@/lib/reports/rows";

/**
 * A metric value as the table shows it: shares as percentages, everything else
 * with precision scaled to its magnitude.
 */
export function formatValue(v: number, unit: string): string {
  if (unit.startsWith("share")) return `${(v * 100).toFixed(1)}%`;
  if (Math.abs(v) >= 100) return v.toFixed(0);
  if (Math.abs(v) >= 10) return v.toFixed(2);
  return v.toFixed(3);
}

/** A z-score as the table shows it. */
export function formatZ(z: number): string {
  return `${z >= 0 ? "+" : ""}${z.toFixed(2)}σ`;
}

/** A threshold's number in the units its field reads in. */
export function formatThreshold(n: number, field: ReportView, unit: string): string {
  if (field === "pctl") return `${ordinal(Math.round(n))} pctl`;
  if (field === "z") return `${n >= 0 ? "+" : ""}${Number(n.toFixed(2))}σ`;
  if (unit.startsWith("share")) return `${Number((n * 100).toFixed(1))}%`;
  return String(Number(n.toFixed(3)));
}
