"use client";

import Link from "next/link";
import { TableControls } from "@/components/table/TableControls";
import { useTableState } from "@/components/table/tableState";
import type { Per } from "@/lib/paging";
import type { FeedItem } from "@/lib/analytics";
import { kindLabel } from "@/lib/insightKinds";

// Insight details carry the mode as its display label; /stats filters by slug.
const MODE_SLUG: Record<string, string> = {
  Hardpoint: "hardpoint",
  "Search & Destroy": "search-and-destroy",
  Control: "control",
  "Capture the Flag": "capture-the-flag",
  Uplink: "uplink",
};

/** Where a finding's evidence actually lives. Metric-backed kinds deep-link
 *  into the exact /stats leaderboard the claim was read from; the rest fall
 *  back to the subject page or the model spec. */
function evidenceHref(item: FeedItem): string {
  const d = item.detail;
  const metric =
    typeof d.metric === "string" && item.subjectType !== "team" ? d.metric : null;
  if (metric) {
    // The report builder takes an ordered `metrics` column list; a single-metric
    // finding deep-links to a one-column report scoped to its cohort.
    const params = new URLSearchParams({ metrics: metric });
    if (typeof d.year === "number") params.set("year", String(d.year));
    const mode = typeof d.mode === "string" ? MODE_SLUG[d.mode] : undefined;
    if (mode) params.set("mode", mode);
    return `/stats?${params}`;
  }
  if (item.kind === "meta_shift") return "/meta";
  if (item.kind === "trade_asymmetry") return "/rounds";
  if (item.subjectSlug) {
    return item.subjectType === "team"
      ? `/teams/${item.subjectSlug}`
      : `/players/${item.subjectSlug}`;
  }
  if (item.kind === "what_wins") return "/methodology#player-rating";
  if (item.kind === "model_null") return "/methodology#winprob";
  if (item.kind === "mode_null") return "/methodology#map-elo";
  return "/methodology";
}

function Chips({ detail }: { detail: Record<string, unknown> }) {
  const chips: string[] = [];
  if (typeof detail.kd_raw === "number") chips.push(`K/D ${detail.kd_raw.toFixed(2)}`);
  if (typeof detail.kd_z === "number")
    chips.push(`${detail.kd_z > 0 ? "+" : ""}${detail.kd_z.toFixed(1)}σ`);
  if (typeof detail.maps_played === "number") chips.push(`${detail.maps_played} maps`);
  if (typeof detail.career_maps === "number") chips.push(`${detail.career_maps} maps`);
  if (typeof detail.peak_elo === "number")
    chips.push(`peak ${Math.round(detail.peak_elo)}`);
  if (typeof detail.win_rate === "number" && typeof detail.n === "number")
    chips.push(`${Math.round(detail.win_rate * 100)}% over ${detail.n} series`);
  if (typeof detail.pct_change === "number")
    chips.push(
      `${detail.pct_change > 0 ? "+" : ""}${Math.round(detail.pct_change * 100)}% pace`,
    );
  if (typeof detail.rating === "number" && typeof detail.rating_sd === "number")
    chips.push(`${detail.rating.toFixed(2)} ±${detail.rating_sd.toFixed(2)}`);
  if (typeof detail.rest_vs_slay === "number")
    chips.push(`rest ${detail.rest_vs_slay.toFixed(1)}× slaying`);
  if (typeof detail.n_maps === "number") chips.push(`${detail.n_maps} maps`);
  if (typeof detail.pctl === "number")
    chips.push(`${Math.round(detail.pctl * 100)}th pctl`);
  if (typeof detail.z === "number")
    chips.push(`${detail.z > 0 ? "+" : ""}${detail.z.toFixed(1)}σ`);
  if (typeof detail.n === "number") chips.push(`n=${Math.round(detail.n)}`);
  if (typeof detail.attempts === "number")
    chips.push(`${Math.round(detail.attempts)} attempts`);
  if (typeof detail.n_deaths === "number")
    chips.push(`${Math.round(detail.n_deaths)} deaths`);
  if (typeof detail.swing === "number")
    chips.push(`${detail.swing > 0 ? "+" : ""}${Math.round(detail.swing * 100)} pts share`);
  if (chips.length === 0) return null;
  return (
    <span className="ml-3 space-x-2 font-mono text-[11px] text-ink-muted">
      {chips.map((c) => (
        <span key={c}>{c}</span>
      ))}
    </span>
  );
}

/** What the line is worth against chance, in the vocabulary the run declared.
 *
 *  Only a testable finding carries a q-value. A descriptive one is a statement
 *  about the record and has no null to be wrong about, so it is left unmarked
 *  rather than given a reassuring number it did not earn. An uncorrected one
 *  claims a latent tendency the database holds no error for, and says so. */
function Verdict({ item, qThreshold }: { item: FeedItem; qThreshold: number }) {
  if (item.findingClass === "uncorrected")
    return (
      <span className="font-mono text-[11px] text-ink-muted" title="No error model exists for this metric, so no test was run.">
        uncorrected
      </span>
    );
  if (item.qBh === null) return null;
  // BH decides retraction and BY is published beside it; where the two
  // disagree about a standing finding, the line says so rather than the site
  // picking the flattering one.
  const byDisagrees =
    !item.retracted && item.qBy !== null && item.qBy > qThreshold;
  return (
    <span
      className={`font-mono text-[11px] ${item.retracted ? "text-ink-muted" : "text-ink-secondary"}`}
      title={
        item.qBy === null
          ? undefined
          : `Benjamini-Yekutieli q ${item.qBy.toFixed(2)}, valid under arbitrary dependence`
      }
    >
      q {item.qBh.toFixed(2)}
      {item.retracted && " · retracted"}
      {byDisagrees && " · BY keeps it out"}
    </span>
  );
}

export function FindingsFeed({
  rows,
  initialPer,
  initialPage,
  qThreshold,
}: {
  rows: FeedItem[];
  initialPer: Per;
  initialPage: number;
  qThreshold: number;
}) {
  const state = useTableState<FeedItem>({
    rows,
    initialPer,
    initialPage,
  });

  return (
    <div className="mt-2">
      <TableControls
        per={state.per}
        setPer={state.setPer}
        page={state.page}
        setPage={state.setPage}
        pageCount={state.pageCount}
        total={state.total}
        offset={state.offset}
        visibleCount={state.visible.length}
        unit="findings"
      />
      <ol className="divide-y divide-hairline/60">
        {state.visible.map((item) => (
          <li key={item.id} className="py-3">
            <div className="flex items-baseline gap-4">
              <span className="eyebrow w-28 flex-none text-[10px] leading-snug text-ink-muted">
                {kindLabel(item.kind)}
              </span>
              <p className="text-sm leading-snug">
                <span
                  className={
                    item.retracted
                      ? "text-ink-secondary line-through decoration-hairline"
                      : ""
                  }
                >
                  {item.headline}
                </span>
                <Chips detail={item.detail} />
              </p>
              <span className="ml-auto flex flex-none items-baseline gap-3">
                <Verdict item={item} qThreshold={qThreshold} />
                {item.subjectSlug && (
                  <Link
                    href={
                      item.subjectType === "team"
                        ? `/teams/${item.subjectSlug}`
                        : `/players/${item.subjectSlug}`
                    }
                    className="font-mono text-xs text-ink-muted underline underline-offset-2 hover:text-ink"
                  >
                    {item.subjectType === "team" ? "team" : "player"}
                  </Link>
                )}
                <Link
                  href={evidenceHref(item)}
                  className="font-mono text-xs text-accent underline underline-offset-2 hover:text-ink"
                >
                  evidence
                </Link>
              </span>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
