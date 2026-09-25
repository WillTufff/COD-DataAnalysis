// Liquipedia's roster role, folded into the three groups a roster chart shows.
// A null role is a starting player; the CWL archive carries no roles at all.
export type RosterGroup = "player" | "bench" | "staff";

const PLAYER = new Set(["captain"]);
const BENCH = new Set([
  "substitute",
  "temp sub",
  "stand-in",
  "loan",
  "inactive",
  "rfa",
]);

export function rosterGroup(role: string | null): RosterGroup {
  if (role === null) return "player";
  const r = role.trim().toLowerCase();
  if (PLAYER.has(r)) return "player";
  if (BENCH.has(r)) return "bench";
  return "staff";
}
