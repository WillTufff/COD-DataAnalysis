// Smoke check: verifies db/schema.ts agrees with the migrated database by
// querying every table through drizzle. Any column drift throws. Run in CI
// after migrations + seeds.
import { sql } from "drizzle-orm";
import { db } from "../lib/db";
import * as schema from "../db/schema";

async function main() {
  const tables = Object.entries(schema);
  for (const [name, table] of tables) {
    const rows = await db.select().from(table as never).limit(1);
    console.log(`ok  ${name} (${rows.length} sample row${rows.length === 1 ? "" : "s"})`);
  }
  const [{ count }] = await db.execute<{ count: string }>(
    sql`SELECT count(*)::text AS count FROM game_player_stats`,
  );
  console.log(`game_player_stats rows: ${count}`);
  process.exit(0);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
