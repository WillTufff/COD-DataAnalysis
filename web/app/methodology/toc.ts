// Tier membership for the methodology sidebar and search. This is the single
// place tier grouping lives — it does not require moving any <section> in
// page.tsx, since sections are targeted by the ids below, not by JSX order.

export type TocSection = { id: string; title: string };
export type TocTier = { tier: string; label: string; sections: TocSection[] };

export const TOC: TocTier[] = [
  {
    tier: "0",
    label: "Foundations",
    sections: [
      { id: "coverage", title: "Archive coverage" },
      { id: "era", title: "Era adjustment" },
      { id: "metrics", title: "Metric glossary" },
    ],
  },
  {
    tier: "1",
    label: "Match record",
    sections: [
      { id: "rounds", title: "Structured event tier" },
      { id: "round-win-probability", title: "Round win probability" },
      { id: "segment-win-probability", title: "Segment win probability" },
      { id: "elo", title: "Team ratings: Elo & Glicko-2" },
    ],
  },
  {
    tier: "2",
    label: "Ratings",
    sections: [
      { id: "map-elo", title: "Map Elo, and one rating per mode" },
      { id: "player-rating", title: "Open player rating" },
      { id: "season-rapm", title: "Season plus-minus" },
      { id: "opponent-adjustment", title: "Opponent adjustment" },
      { id: "match-context", title: "Match context" },
      { id: "evaluation", title: "The evaluation harness" },
      { id: "skill", title: "SKILL" },
      { id: "primacy", title: "Which rating is the rating" },
      { id: "series-dynamics", title: "Series dynamics" },
      { id: "player-style", title: "Player style" },
      { id: "role", title: "Role at the opening engagement" },
      { id: "winprob", title: "Series win probability (winprob_v1)" },
      { id: "backtests", title: "Backtest report cards" },
    ],
  },
  {
    tier: "3",
    label: "Career",
    sections: [
      { id: "career-value", title: "Career value" },
      { id: "career-rank", title: "Career rank" },
      { id: "aging", title: "Aging" },
    ],
  },
  {
    tier: "5",
    label: "Findings",
    sections: [
      { id: "insights", title: "Insights" },
      { id: "error-control", title: "What a finding is worth" },
    ],
  },
  {
    tier: "—",
    label: "Reference",
    sections: [
      { id: "validation", title: "Four checks the ratings could have failed" },
      { id: "attribution", title: "Data & attribution" },
    ],
  },
];
