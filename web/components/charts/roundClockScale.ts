import type { RoundTimelineBin } from "@/lib/analytics";

// Shared by the figure and by the caption that annotates it, so the prose can
// never describe a bin the chart stopped drawing. Not in the chart module
// itself: that one is a client component, and the page reads this on the server.

/** Rounds thin out badly past the point where a hundredth of them remain, and a
 *  mean over forty rounds drawn at the same weight as one over nine thousand
 *  invites reading noise as shape. The axis stops here and the caption says so. */
export const TAIL_CUT = 0.01;

export function visibleBins(bins: RoundTimelineBin[]): RoundTimelineBin[] {
  const out = bins.filter((b) => b.n_live > 0 && b.live_share >= TAIL_CUT);
  return out.length > 1 ? out : bins.filter((b) => b.n_live > 0);
}
