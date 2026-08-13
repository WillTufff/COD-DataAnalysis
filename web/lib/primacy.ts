// Which of the three player ratings answers which question, and which one a
// page leads with for a given season.
//
// The site publishes three ratings that a reader will otherwise assume are
// three attempts at one number. They are not. Each names its own object, its
// own judge and its own known failure, and the rule below is the site's answer
// to "which one is the rating" — stated once here rather than implied by
// whichever board a page happens to render first.
//
// The rule degrades by season, and that is not a detail. SKILL exists only
// where a season has a season before it to train the prior on and a
// season-resolution plus-minus to blend with, which is 2021 onward. Before
// that a SKILL-first page renders nothing at all, so the covered seasons are
// read from the database and passed in — never assumed from a year constant.
//
// Kept free of server-only imports so client components can render the copy.

export type RatingId = "skill" | "value" | "decomposition";

export type RatingIdentity = {
  id: RatingId;
  name: string;
  /** The question this rating is the answer to. */
  question: string;
  /** What it is a number about. */
  object: string;
  /** What judged it, and what it was scored against. */
  judge: string;
  /** The failure the rating is known to have. Never omitted on a page. */
  failure: string;
  href: string;
};

export const RATINGS: Record<RatingId, RatingIdentity> = {
  skill: {
    id: "skill",
    name: "SKILL",
    question: "How good is this player now?",
    object: "a player-season, forward-looking",
    judge:
      "a box-score prior fitted on earlier seasons, blended with the season's own plus-minus by inverse variance",
    failure:
      "it lost its own persistence gate: against next season's K/D z it scores below raw K/D z, and most of its weight sits on the prior rather than on the maps",
    href: "/methodology#skill",
  },
  value: {
    id: "value",
    name: "VALUE",
    question: "What was that season worth?",
    object: "one season a player actually played",
    judge:
      "per-cohort regression weights for winning maps, read through a two-level model of the cohort",
    failure:
      "it describes a season rather than forecasting one, and most of a career's seasons have overlapping intervals",
    href: "/methodology#player-rating",
  },
  decomposition: {
    id: "decomposition",
    name: "Plus-minus",
    question: "What won the map?",
    object: "a map, split between the players on the server",
    judge:
      "ridge regression on map margin, holding the other seven players constant",
    failure:
      "almost every published coefficient has an interval covering zero, so it is an interval to read and not a ranking to sort",
    href: "/methodology#rapm",
  },
};

/**
 * The rating a page leads with for one season. SKILL where it covers the
 * season, VALUE everywhere else — which is the whole CWL era and 2020.
 */
export function primaryRating(
  year: number,
  skillYears: ReadonlySet<number>,
): RatingId {
  return skillYears.has(year) ? "skill" : "value";
}

/**
 * Why the leading rating on a season page is what it is. Returned for every
 * season, so a page states the rule instead of showing an empty SKILL panel.
 */
export function primacyReason(
  year: number,
  skillYears: ReadonlySet<number>,
): string {
  if (skillYears.has(year)) return "";
  const covered = [...skillYears].sort((a, b) => a - b);
  const first = covered[0];
  if (first === undefined) return "SKILL is not published, so VALUE leads.";
  return year < first
    ? `SKILL starts at ${first}: an earlier season has no season before it to train the prior on, and the CWL years carry no season-resolution plus-minus to blend with. VALUE leads here.`
    : `SKILL does not cover ${year}. VALUE leads here.`;
}
