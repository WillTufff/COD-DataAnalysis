// Exit 3 when there is nothing to check: no database, or a database with no
// fitted model in it. checks.sh reads 3 as a skip, and a gate that cannot run
// must never report green.
//
// In smoke mode the pass asserts only that pages render, so an empty database
// is exactly the case it is for and the precheck always succeeds.
import { hasRatings } from "../e2e/population";

async function main() {
  if (process.env.E2E_MODE === "smoke") process.exit(0);
  let ready = false;
  try {
    ready = await hasRatings();
  } catch (err) {
    console.error(`no database reachable: ${(err as Error).message}`);
    process.exit(3);
  }
  if (!ready) {
    console.error("no fitted model in the database: run run_all first");
    process.exit(3);
  }
  process.exit(0);
}

main();
