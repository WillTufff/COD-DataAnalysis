"use client";

import { useState } from "react";
import type { ModeWeightCohort } from "@/lib/analytics";

// What the map-outcome regression learned, drawn as an argument: for each
// (season × mode), how much a one-SD team edge in everything the cohort
// measured *beyond the gunfight* was worth relative to the same edge in kills
// and deaths. Bars diverge from 1× on a log scale — left means the gunfight
// decided maps, right means everything else did.
//
// "Everything else" is deliberately not called "objective play". Depending on
// the cohort it mixes objective columns (hill time, captures, bomb plays) with
// survival and trade economy (time per life, untraded deaths, trade kills), and
// the data cannot adjudicate which of those is "the objective" — so the chart
// names the one boundary the model does define: the slaying pair against the
// rest. insights.py's what_wins computes the identical ratio.
//
// Each bar carries the 95% percentile bootstrap over its cohort's maps, drawn as
// a line through the bar. The intervals are wide and very unequal — cohorts run
// from under a hundred maps to over a thousand, over collinear features, so an
// interval can span a factor of five — and a bar whose interval
// covers the 1× line is drawn faded: the point estimate has a side, the data does
// not. insights.what_wins suppresses those cohorts entirely; the chart keeps them
// visible because "we cannot tell" is itself the reading for that mode.
//
// Era colors are fixed by season and never reassigned (same convention as
// every other chart on the site: color follows the era, not the row).
const ERA_COLOR: Record<number, string> = {
  2017: "var(--series-1)",
  2018: "var(--series-2)",
  2019: "var(--series-3)",
};
const ERA_LABEL: Record<number, string> = {
  2017: "IW ’17",
  2018: "WWII ’18",
  2019: "BO4 ’19",
};

const BAR = 14;
const BAR_GAP = 2;
const ROW_GAP = 18;
const MIN_EXP = -2; // the axis never narrows past 1/4× … 4×, however tight the data
const MAX_EXP = 2;
const CAP = 4; // half-height of the interval's end caps
const MAX_LABELS = 7; // past this, label every other power of two

// A cohort whose interval covers 1× has no resolved side.
const unresolved = (c: ModeWeightCohort) =>
  c.restVsSlayCi !== null && c.restVsSlayCi[0] <= 1 && c.restVsSlayCi[1] >= 1;

// Doubling is the unit on this axis, so ticks are powers of two and sub-1 ones
// are named as the fractions they are: "0.06×" invites reading a 6% difference
// where the tick means one sixteenth.
const tickLabel = (exp: number) => (exp >= 0 ? `${2 ** exp}×` : `1/${2 ** -exp}×`);

// One decimal is the readable default, but a ratio of 0.03 rendered as "0.0×"
// reads as zero — an interval end that small is a real number, not an absence.
const fmt = (v: number, dp = 1) => v.toFixed(v < 0.1 ? 2 : dp);

// An interval's two ends at one precision, set by whichever end needs more.
const span = (lo: number, hi: number) => {
  const dp = Math.min(lo, hi) < 0.1 ? 2 : 1;
  return `${fmt(lo, dp)}–${fmt(hi, dp)}`;
};

export function WhatWinsMaps({ cohorts }: { cohorts: ModeWeightCohort[] }) {
  const [hover, setHover] = useState<ModeWeightCohort | null>(null);

  const shown = cohorts.filter((c) => c.restVsSlay > 0);
  // A cohort with no usable ratio is not a chart with empty rows: say nothing
  // rather than draw a bare axis under a confident caption.
  if (shown.length === 0) return null;

  const modes = [...new Set(shown.map((c) => c.mode))];
  // Least gunfight-driven modes first, so the chart reads as a gradient.
  modes.sort((a, b) => {
    const max = (m: string) =>
      Math.max(...shown.filter((c) => c.mode === m).map((c) => c.restVsSlay));
    return max(b) - max(a);
  });
  const years = [...new Set(shown.map((c) => c.year))].sort();
  // The extreme cohort, named from the data instead of frozen into the caption —
  // and preferring one whose interval resolved, so the sentence does not point at
  // a bar the chart itself fades out.
  const namable = shown.filter((c) => !unresolved(c));
  const peak = (namable.length ? namable : shown).reduce((a, b) =>
    b.restVsSlay > a.restVsSlay ? b : a,
  );

  // The axis has to hold the intervals, not just the points, or a whisker runs
  // off the plot and reads as a shorter one. Rounded out to whole powers of two
  // so the gridlines bracket the data instead of ending inside it.
  const ends = shown.flatMap((c) => [c.restVsSlay, ...(c.restVsSlayCi ?? [])]);
  const logLo = Math.min(MIN_EXP, Math.floor(Math.log2(Math.min(...ends))));
  const logHi = Math.max(MAX_EXP, Math.ceil(Math.log2(Math.max(...ends))));
  const exps = Array.from({ length: logHi - logLo + 1 }, (_, i) => logLo + i);
  // Alternating from 1×, so the baseline always keeps its label.
  const labeled = (exp: number) => exps.length <= MAX_LABELS || exp % 2 === 0;

  // Right margin holds the value label, which now sits past the interval's cap
  // rather than past the bar's end.
  const M = { top: 6, right: 56, bottom: 20, left: 118 };
  const W = 560;
  const iw = W - M.left - M.right;
  const x = (v: number) => M.left + ((Math.log2(v) - logLo) / (logHi - logLo)) * iw;

  const rows = modes.map((mode) => shown.filter((c) => c.mode === mode));
  const rowH = (n: number) => n * BAR + (n - 1) * BAR_GAP;
  const H =
    M.top + rows.reduce((s, r) => s + rowH(r.length) + ROW_GAP, -ROW_GAP) + M.bottom;

  let yCursor = M.top;
  const rowTops = rows.map((r) => {
    const top = yCursor;
    yCursor += rowH(r.length) + ROW_GAP;
    return top;
  });

  return (
    <figure>
      <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-secondary">
        {years.map((y) => (
          <span key={y} className="flex items-center gap-1.5">
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{ background: ERA_COLOR[y] }}
            />
            {ERA_LABEL[y]}
          </span>
        ))}
        {shown.some((c) => c.restVsSlayCi !== null) && (
          <span className="flex items-center gap-1.5 text-ink-muted">
            <svg width="14" height="8" aria-hidden="true">
              <g stroke="var(--ink)" strokeWidth={1.25} opacity={0.75}>
                <line x1={1} x2={13} y1={4} y2={4} />
                <line x1={1} x2={1} y1={1} y2={7} />
                <line x1={13} x2={13} y1={1} y2={7} />
              </g>
            </svg>
            95% CI
          </span>
        )}
        <span className="ml-auto text-ink-muted">
          ← the gunfight decided · everything else decided →
        </span>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        role="img"
        aria-label="Map-win weight beyond the gunfight, relative to slaying, by mode and title"
        onMouseLeave={() => setHover(null)}
      >
        {exps.map((exp) => (
          <g key={exp}>
            <line
              x1={x(2 ** exp)}
              x2={x(2 ** exp)}
              y1={M.top}
              y2={H - M.bottom}
              stroke={exp === 0 ? "var(--baseline)" : "var(--hairline)"}
            />
            {labeled(exp) && (
              <text
                x={x(2 ** exp)}
                y={H - 6}
                textAnchor="middle"
                fontSize={10}
                fill="var(--ink-muted)"
                className="font-mono"
              >
                {tickLabel(exp)}
              </text>
            )}
          </g>
        ))}
        {modes.map((mode, mi) => {
          const rowCells = rows[mi];
          const top = rowTops[mi];
          return (
            <g key={mode}>
              <text
                x={M.left - 10}
                y={top + rowH(rowCells.length) / 2 + 3.5}
                textAnchor="end"
                fontSize={11.5}
                fill="var(--ink-secondary)"
              >
                {mode}
              </text>
              {rowCells.map((c, ri) => {
                const yBar = top + ri * (BAR + BAR_GAP);
                const yMid = yBar + BAR / 2;
                const ci = c.restVsSlayCi;
                const x0 = x(1);
                const x1 = x(c.restVsSlay);
                const objSide = x1 >= x0;
                const w = Math.max(Math.abs(x1 - x0), 2);
                // Rounded end on the data side; flat edge sits on the 1× line.
                const d = objSide
                  ? `M${x0},${yBar} h${w - 4} a4,4 0 0 1 4,4 v${BAR - 8} a4,4 0 0 1 -4,4 h${4 - w} Z`
                  : `M${x0},${yBar} h${4 - w} a4,4 0 0 0 -4,4 v${BAR - 8} a4,4 0 0 0 4,4 h${w - 4} Z`;
                // Full-width hover bands tile the chart with no dead space:
                // each extends halfway into the neighboring gap, so moving
                // the pointer between bars hands hover off without a flicker.
                const bandTop =
                  ri === 0
                    ? mi === 0
                      ? 0
                      : yBar - ROW_GAP / 2
                    : yBar - BAR_GAP / 2;
                const bandBottom =
                  ri === rowCells.length - 1
                    ? mi === rows.length - 1
                      ? H
                      : yBar + BAR + ROW_GAP / 2
                    : yBar + BAR + BAR_GAP / 2;
                return (
                  <g
                    key={`${c.year}-${c.mode}`}
                    onMouseEnter={() => setHover(c)}
                    onMouseLeave={() => setHover(null)}
                  >
                    <rect
                      x={0}
                      y={bandTop}
                      width={W}
                      height={bandBottom - bandTop}
                      fill="transparent"
                    />
                    <path
                      d={d}
                      fill={ERA_COLOR[c.year]}
                      opacity={
                        (unresolved(c) ? 0.4 : 1) * (hover && hover !== c ? 0.45 : 1)
                      }
                    />
                    {ci && (
                      <g
                        stroke="var(--ink)"
                        strokeWidth={1.25}
                        opacity={hover && hover !== c ? 0.4 : 0.75}
                      >
                        <line x1={x(ci[0])} x2={x(ci[1])} y1={yMid} y2={yMid} />
                        <line
                          x1={x(ci[0])}
                          x2={x(ci[0])}
                          y1={yMid - CAP}
                          y2={yMid + CAP}
                        />
                        <line
                          x1={x(ci[1])}
                          x2={x(ci[1])}
                          y1={yMid - CAP}
                          y2={yMid + CAP}
                        />
                      </g>
                    )}
                    {/* Left-pointing bars label on their empty right side, so
                        small ratios never collide with the mode name. Either way
                        the label clears the interval's upper cap. */}
                    <text
                      x={Math.max(objSide ? x1 : x0, ci ? x(ci[1]) : 0) + 5}
                      y={yMid + 3.5}
                      textAnchor="start"
                      fontSize={10}
                      fill="var(--ink-secondary)"
                      className="font-mono"
                    >
                      {fmt(c.restVsSlay)}×
                    </text>
                  </g>
                );
              })}
            </g>
          );
        })}
      </svg>
      <figcaption className="mt-1 text-xs text-ink-muted">
        {hover ? (
          <span className="text-ink-secondary">
            {ERA_LABEL[hover.year]} {hover.mode}: a one-SD team edge beyond the
            gunfight was worth {fmt(hover.restVsSlay)}× the equivalent
            edge in kills and deaths (regression over{" "}
            {hover.nMaps.toLocaleString()} maps
            {hover.restVsSlayCi &&
              `, 95% CI ${span(hover.restVsSlayCi[0], hover.restVsSlayCi[1])}×`}
            ).{" "}
            {unresolved(hover)
              ? "The interval covers 1×, so which half carried this mode is unresolved. "
              : ""}
            Averaged over{" "}
            {hover.restFeatures
              .map((k) => hover.labels[k] ?? k)
              .join(", ")
              .toLowerCase()}
            .
          </span>
        ) : (
          <>
            Mean win-odds weight of a one-SD team edge in everything the cohort
            measured beyond kills and deaths, relative to a one-SD edge in kills
            and deaths, per (title × mode), log scale. 1× means both mattered
            equally; {ERA_LABEL[peak.year]} {peak.mode} is the outlier at{" "}
            {fmt(peak.restVsSlay)}×. Lines are 95% percentile bootstrap
            intervals over each cohort&rsquo;s maps
            {shown.some(unresolved) &&
              "; faded bars are the cohorts whose interval covers 1×, where the model cannot say which half carried the mode"}
            . Hover a bar for the features behind it.
          </>
        )}
      </figcaption>
    </figure>
  );
}
