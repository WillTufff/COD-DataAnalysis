"use client";

import { useState } from "react";
import type { RoundTimeline, RoundTimelineBin } from "@/lib/analytics";
import { visibleBins } from "./roundClockScale";

// The round along its own clock. Four panels stacked on one shared time axis —
// survivors, win probability, the model against the outcome, and how many rounds
// are still going — because these are four different y scales and putting two of
// them on one pair of axes would be a lie about their comparability.
//
// One hover moves through all four: the whole point of stacking them is that a
// reader asks "what is happening at 40 seconds" once.

const MIN_LEADER_N = 100;

const W = 680;
const M = { top: 16, right: 18, bottom: 34, left: 46 };
const IW = W - M.left - M.right;
const H_STATE = 104; // survivors, win probability, calibration
const H_STRIP = 34; // rounds still live
const GAP = 26;

const PANELS = 3;
const H = M.top + PANELS * (H_STATE + GAP) + H_STRIP + M.bottom;

/** Where each panel's plot area starts, top down. */
function panelTop(i: number): number {
  return M.top + i * (H_STATE + GAP);
}

const STRIP_TOP = panelTop(PANELS);

export function RoundClock({ timeline }: { timeline: RoundTimeline }) {
  const bins = visibleBins(timeline.bins);
  const [hover, setHover] = useState<number | null>(null);

  const tMax = bins[bins.length - 1].t_s + timeline.bin_ms / 1000;
  const x = (t_s: number) => M.left + (t_s / tMax) * IW;
  // Survivors: a fixed 0–4 axis, because 4v4 is the format and a data-fitted
  // axis would make every season's opening look like a different game.
  const ySurv = (v: number) => panelTop(0) + H_STATE - (v / 4) * H_STATE;
  // Both probability panels share 0.5–1.0: 0.5 is where a round with equal
  // survivors sits by construction, so it is the floor of the question.
  const yProb = (v: number, panel: number) =>
    panelTop(panel) + H_STATE - ((v - 0.5) / 0.5) * H_STATE;
  const yStrip = (share: number) => STRIP_TOP + H_STRIP - share * H_STRIP;

  const at = hover === null ? null : bins[hover];
  // Two rounds in the archive open uneven, and a rate over two rounds is not a
  // rate. Panel three starts where the subset is a sample.
  const leaderBins = bins.filter((b) => b.leader_n >= MIN_LEADER_N);
  const path = (
    pts: RoundTimelineBin[],
    value: (b: RoundTimelineBin) => number | null,
    y: (v: number) => number,
  ) =>
    pts
      .map((b, i) => {
        const v = value(b);
        return v === null ? null : `${i ? "L" : "M"}${x(b.t_s)},${y(v)}`;
      })
      .filter(Boolean)
      .join(" ")
      .replace(/^L/, "M");

  const ticks = [0, 15, 30, 45, 60, 75, 90, 105, 120].filter((t) => t <= tMax);

  return (
    <figure>
      <div className="mb-2 flex flex-wrap items-center gap-x-5 gap-y-1 text-xs">
        <Key color="var(--series-1)">winner&rsquo;s side</Key>
        <Key color="var(--series-4)">loser&rsquo;s side</Key>
        <Key color="var(--accent)">model</Key>
        <Key color="var(--ink-secondary)" dot>
          observed
        </Key>
        <span className="ml-auto font-mono text-ink-muted tabular-nums">
          {at
            ? `${at.t_s}s · ${at.n_live.toLocaleString()} rounds live · ` +
              `${at.winner_alive?.toFixed(2) ?? "—"} v ${at.loser_alive?.toFixed(2) ?? "—"} alive`
            : "hover for a second-by-second readout"}
        </span>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        role="img"
        aria-label="Survivors, win probability, model calibration and rounds still live, by seconds elapsed in the round"
        onMouseLeave={() => setHover(null)}
      >
        {/* panel frames and y axes */}
        {[0, 1, 2].map((p) => (
          <g key={p}>
            <line
              x1={M.left}
              x2={W - M.right}
              y1={panelTop(p) + H_STATE}
              y2={panelTop(p) + H_STATE}
              stroke="var(--hairline)"
            />
            {(p === 0 ? [0, 2, 4] : [0.5, 0.75, 1]).map((v) => {
              const yy = p === 0 ? ySurv(v) : yProb(v, p);
              return (
                <g key={v}>
                  <line
                    x1={M.left}
                    x2={W - M.right}
                    y1={yy}
                    y2={yy}
                    stroke="var(--hairline)"
                    strokeDasharray={v === 0 || v === 0.5 ? undefined : "2 3"}
                  />
                  <text
                    x={M.left - 7}
                    y={yy + 3.5}
                    textAnchor="end"
                    fontSize={9}
                    fill="var(--ink-muted)"
                    className="font-mono"
                  >
                    {p === 0 ? v : `${Math.round(v * 100)}%`}
                  </text>
                </g>
              );
            })}
          </g>
        ))}

        {/* panel titles */}
        <PanelTitle y={panelTop(0)}>players alive</PanelTitle>
        <PanelTitle y={panelTop(1)}>P(win) for the side that won</PanelTitle>
        <PanelTitle y={panelTop(2)}>the side ahead: model v outcome</PanelTitle>
        <PanelTitle y={STRIP_TOP}>rounds still live</PanelTitle>

        {/* 1 — survivors */}
        <path
          d={path(bins, (b) => b.winner_alive, ySurv)}
          fill="none"
          stroke="var(--series-1)"
          strokeWidth={2}
        />
        <path
          d={path(bins, (b) => b.loser_alive, ySurv)}
          fill="none"
          stroke="var(--series-4)"
          strokeWidth={2}
        />

        {/* 2 — win probability for the eventual winner, with its band */}
        <path
          d={
            path(bins, (b) => (b.p_winner === null ? null : b.p_winner + 1.96 * (b.p_winner_se ?? 0)), (v) => yProb(v, 1)) +
            bins
              .slice()
              .reverse()
              .map((b) =>
                b.p_winner === null
                  ? ""
                  : `L${x(b.t_s)},${yProb(b.p_winner - 1.96 * (b.p_winner_se ?? 0), 1)}`,
              )
              .join("") +
            " Z"
          }
          fill="var(--accent-dim)"
          fillOpacity={0.28}
          stroke="none"
        />
        <path
          d={path(bins, (b) => b.p_winner, (v) => yProb(v, 1))}
          fill="none"
          stroke="var(--accent)"
          strokeWidth={2}
        />

        {/* 3 — what the table says the leader is worth, against what happened.
            Model and outcome are drawn over the same bins, so neither is shown
            reaching somewhere the other could not be measured. */}
        <path
          d={path(leaderBins, (b) => b.p_leader, (v) => yProb(v, 2))}
          fill="none"
          stroke="var(--accent)"
          strokeWidth={2}
        />
        {leaderBins.map((b) =>
          b.leader_wins === null ? null : (
            <g key={b.t_s}>
              <line
                x1={x(b.t_s)}
                x2={x(b.t_s)}
                y1={yProb(
                  Math.min(1, b.leader_wins + 1.96 * (b.leader_wins_se ?? 0)),
                  2,
                )}
                y2={yProb(
                  Math.max(0.5, b.leader_wins - 1.96 * (b.leader_wins_se ?? 0)),
                  2,
                )}
                stroke="var(--ink-muted)"
                strokeWidth={1}
              />
              <circle
                cx={x(b.t_s)}
                cy={yProb(b.leader_wins, 2)}
                r={2.6}
                fill="var(--ink-secondary)"
              />
              <title>
                {`${b.t_s}s: the side ahead won ${(b.leader_wins * 100).toFixed(1)}% of ` +
                  `${b.leader_n.toLocaleString()} rounds; the table said ` +
                  `${((b.p_leader ?? 0) * 100).toFixed(1)}%`}
              </title>
            </g>
          ),
        )}

        {/* 4 — the survival strip: how much of the sample is left */}
        <path
          d={
            `M${x(0)},${yStrip(0)} ` +
            bins.map((b) => `L${x(b.t_s)},${yStrip(b.live_share)}`).join(" ") +
            ` L${x(bins[bins.length - 1].t_s)},${yStrip(0)} Z`
          }
          fill="var(--ink-muted)"
          fillOpacity={0.22}
          stroke="var(--ink-muted)"
          strokeWidth={1}
        />

        {/* x axis */}
        {ticks.map((t) => (
          <text
            key={t}
            x={x(t)}
            y={H - M.bottom + 20}
            textAnchor="middle"
            fontSize={9}
            fill="var(--ink-muted)"
            className="font-mono"
          >
            {t}s
          </text>
        ))}

        {/* hover crosshair across every panel */}
        {at && (
          <line
            x1={x(at.t_s)}
            x2={x(at.t_s)}
            y1={M.top}
            y2={STRIP_TOP + H_STRIP}
            stroke="var(--ink-secondary)"
            strokeWidth={1}
            strokeDasharray="3 3"
          />
        )}
        {bins.map((b, i) => (
          <rect
            key={b.t_s}
            x={x(b.t_s) - IW / bins.length / 2}
            y={M.top}
            width={IW / bins.length}
            height={STRIP_TOP + H_STRIP - M.top}
            fill="transparent"
            onMouseEnter={() => setHover(i)}
          />
        ))}
      </svg>
    </figure>
  );
}

function PanelTitle({ y, children }: { y: number; children: React.ReactNode }) {
  return (
    <text x={M.left} y={y - 5} fontSize={9} fill="var(--ink-secondary)" className="font-mono uppercase">
      {children}
    </text>
  );
}

function Key({
  color,
  dot,
  children,
}: {
  color: string;
  dot?: boolean;
  children: React.ReactNode;
}) {
  return (
    <span className="flex items-center gap-1.5 text-ink-secondary">
      <svg width={14} height={8} aria-hidden="true">
        {dot ? (
          <circle cx={7} cy={4} r={2.6} fill={color} />
        ) : (
          <rect x={0} y={3} width={14} height={2} fill={color} />
        )}
      </svg>
      {children}
    </span>
  );
}

/**
 * How long a death waited to be answered, against the 5 s line the trade metric
 * draws. The window is not arbitrary — the distribution peaks well inside it —
 * but it is a cutoff on a smooth decay rather than a natural boundary, and this
 * is the figure that says so.
 */
export function TradeLatency({
  latency,
}: {
  latency: RoundTimeline["trade_latency"];
}) {
  const w = 420;
  const h = 130;
  const pad = { top: 10, right: 10, bottom: 30, left: 34 };
  const iw = w - pad.left - pad.right;
  const ih = h - pad.top - pad.bottom;
  const cells = latency.bins;
  const peak = Math.max(...cells.map((c) => c.share), 0.0001);
  const bw = iw / cells.length;
  const windowS = latency.window_ms / 1000;
  const xw = pad.left + (windowS / (latency.max_ms / 1000)) * iw;

  return (
    <figure>
      <svg
        viewBox={`0 0 ${w} ${h}`}
        className="w-full"
        role="img"
        aria-label="Distribution of the delay before a death was answered, with the 5 second trade window marked"
      >
        {[0, 0.025, 0.05].map((v) => (
          <line
            key={v}
            x1={pad.left}
            x2={w - pad.right}
            y1={pad.top + ih - (v / peak) * ih}
            y2={pad.top + ih - (v / peak) * ih}
            stroke="var(--hairline)"
          />
        ))}
        {cells.map((c, i) => {
          const bh = (c.share / peak) * ih;
          return (
            <g key={c.lo_s}>
              <rect
                x={pad.left + i * bw + 1}
                y={pad.top + ih - bh}
                width={bw - 2}
                height={bh}
                rx={2}
                fill={c.in_window ? "var(--series-5)" : "var(--baseline)"}
              />
              <title>
                {`${c.lo_s}–${c.hi_s}s: ${c.n.toLocaleString()} answers ` +
                  `(${(c.share * 100).toFixed(1)}% of deaths)` +
                  (c.in_window ? " — counted as a trade" : " — outside the window")}
              </title>
            </g>
          );
        })}
        {/* the window edge, labelled: everything left of it counts as a trade */}
        <line
          x1={xw}
          x2={xw}
          y1={pad.top - 4}
          y2={pad.top + ih}
          stroke="var(--ink)"
          strokeWidth={1}
          strokeDasharray="3 2"
        />
        <text x={xw + 4} y={pad.top + 4} fontSize={9} fill="var(--ink-secondary)" className="font-mono">
          {windowS}s window
        </text>
        {[0, 3, 6, 9, 12].map((t) => (
          <text
            key={t}
            x={pad.left + (t / (latency.max_ms / 1000)) * iw}
            y={h - pad.bottom + 18}
            textAnchor="middle"
            fontSize={9}
            fill="var(--ink-muted)"
            className="font-mono"
          >
            {t}s
          </text>
        ))}
      </svg>
    </figure>
  );
}
